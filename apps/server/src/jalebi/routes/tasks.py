"""Task routes: create, list, detail, cancel, rerun, publish, delete, live events."""

import json
import queue as _queue_module
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
from flask import Blueprint, Response, current_app, jsonify, request, send_file
from flask.typing import ResponseReturnValue

from jalebi import (
    artifacts,
    db,
    ide,
    masking,
    prompts,
    reviews,
    secrets,
    settings,
    tasks,
    workspace_files,
)
from jalebi.adapters import available_adapters, is_backend_available
from jalebi.catalog import agent_by_slug
from jalebi.config import Config
from jalebi.db import Artifact, Run, Task, now
from jalebi.git_workspace import GitWorkspace, GitWorkspaceError, PushLeaseFailed
from jalebi.github import GitHubClient, GitHubError
from jalebi.queue import PublishConflict, PublishError, TaskQueue

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")

TERMINAL_STATUSES = {"done", "failed", "timed_out", "cancelled", "needs_approval", "interrupted"}

# Review comments travel into context_json → the worktree AGENTS.md → the model
# context. A PR with many/large reviews must not balloon every run's brief, so
# each comment and the total are truncated (with a marker) at fetch time.
MAX_REVIEW_CHARS = 8_000  # per review comment embedded into the task context
MAX_REVIEWS_TOTAL_CHARS = 24_000  # total embedded review text across comments


def _queue() -> TaskQueue:
    return current_app.config["JALEBI_QUEUE"]


def _repo_name(session, repo_id: int) -> str | None:
    repo = session.get(db.Repo, repo_id)
    return repo.full_name if repo is not None else None


def _task_dict(session, task: Task) -> dict[str, object]:
    """Serialize a task with its run, followups, artifacts, and reviewers."""
    run = tasks.latest_run(session, task.id)
    reviewers_raw = reviews.reviews_json_for_task(session, task.id)
    reviewers = json.loads(reviewers_raw) if reviewers_raw else []
    # Look up normalized PR facts from the polling observer (T2.1). The
    # poller is registered as ``JALEBI_POLLER`` in ``create_app``; tests
    # without the poller (or with the flag off) get ``None`` and degrade
    # to the no-PR-facts branch of ``attention_for``.
    poller = current_app.config.get("JALEBI_POLLER")
    pr_facts = (
        poller.pr_facts_for_task(task.repo_id, task.id)
        if poller is not None
        else None
    )
    # Phase 4 T4.1 — task dependency fields (depends_on / blocked_by /
    # blocking / blocked).
    deps = tasks.dep_dict(session, task.id)
    return tasks.task_to_dict(
        task,
        run=run,
        followups=tasks.list_followups(session, task.id),
        artifacts=tasks.list_artifacts(session, run.id) if run is not None else None,
        repo_full_name=_repo_name(session, task.repo_id),
        reviewers=reviewers,
        pr_facts=pr_facts,
        deps=deps,
    )


def _valid_pat(config, name: str | None) -> bool:
    """A PAT/account name is valid only if it's a stored named account."""
    return name in secrets.token_names(config)


def _parse_number(value, name: str) -> tuple[int | None, str | None]:
    """Parse an optional JSON number field (F8).

    Accepts ints and digit strings; anything else (floats, garbage
    strings, bools) is a 400, never an unhandled ValueError → 500.
    Returns ``(int-or-None, error-or-None)``.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{name} must be an integer"
    if isinstance(value, int):
        return value, None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()), None
    return None, f"{name} must be an integer"


def _masker(session) -> Callable[[str], str]:
    config: Config = current_app.config["JALEBI_CONFIG"]
    patterns = settings.get_setting(session, "secret_patterns") or []
    patterns = [str(p) for p in patterns] if isinstance(patterns, list) else []
    values = secrets.all_token_values(config)
    return masking.build_masker(values, patterns)


def _fetch_context(
    session,
    repo_id: int,
    issue_number: int | None,
    pr_number: int | None,
    pat_name: str | None = None,
):
    """Fetch issue/PR context from GitHub for the task's linked targets (masked).

    ``issue_number``/``pr_number`` drive the fetch directly — the route only
    passes them for task types that accept a link, but a freeform task that
    links a PR gets the same PR context a ``pr_review`` task does. PR review
    comments are fetched and embedded too (masked), so "fix the issues in this
    PR" freeform tasks see exactly what the reviewers said.
    """
    if issue_number is None and pr_number is None:
        return {}
    config: Config = current_app.config["JALEBI_CONFIG"]
    repo = session.get(db.Repo, repo_id)
    if repo is None:
        raise ValueError("repo not found")
    token = secrets.resolve_token(config, pat_name)
    if token is None:
        raise ValueError("no GitHub token configured")
    masker = _masker(session)
    context: dict = {}
    client = GitHubClient(token)
    try:
        if issue_number is not None:
            issue = client.get_issue(repo.full_name, issue_number)
            masked_body = masker(issue["body"]) if issue.get("body") else ""
            context["issues"] = [
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": masked_body,
                    "html_url": issue["html_url"],
                }
            ]
        if pr_number is not None:
            pr = client.get_pr(repo.full_name, pr_number)
            reviews: list[dict[str, str]] = []
            embedded_total = 0
            try:
                for review in client.list_pr_reviews(repo.full_name, pr_number):
                    text = (review.get("body") or "").strip()
                    if not text:
                        continue
                    if len(text) > MAX_REVIEW_CHARS:
                        text = text[:MAX_REVIEW_CHARS] + "\n\n[… review truncated …]"
                    remaining = MAX_REVIEWS_TOTAL_CHARS - embedded_total
                    if remaining <= 0:
                        break
                    if len(text) > remaining:
                        text = text[:remaining] + "\n\n[… review truncated …]"
                    embedded_total += len(text)
                    reviews.append(
                        {"author": review.get("user") or "unknown", "body": masker(text)}
                    )
            except Exception:
                # Review comments are enrichment, not essential: a failed
                # reviews fetch must not block task creation (the PR body
                # still goes through). Mirrors _with_review_comments.
                reviews = []
            context["prs"] = [
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "body": masker(pr["body"]) if pr.get("body") else "",
                    "html_url": pr["html_url"],
                    "state": pr["state"],
                    "base": pr["base"],
                    "head": pr["head"],
                    "author": pr["author"],
                    "reviews": reviews,
                }
            ]
    except (httpx.HTTPError, GitHubError) as exc:
        raise ValueError(f"failed to fetch task context: {exc}")
    finally:
        client.close()
    return context


@bp.post("")
def create_task() -> ResponseReturnValue:
    config: Config = current_app.config["JALEBI_CONFIG"]
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400

    repo_id = payload.get("repo_id")
    if not isinstance(repo_id, int):
        return jsonify({"error": "repo_id is required"}), 400

    session = db.get_session()
    masker = _masker(session)

    # Catalog agent selection: the slug is validated to exist + be enabled here;
    # the task's OWN cli/model are explicit user overrides only (the agent's
    # pinned cli/model apply at run time when the task has no override — see
    # queue._agent_run_opts). Precedence: task override > live agent pin > default.
    agent_id = payload.get("agent_id")
    agent = None
    if agent_id is not None:
        agent = agent_by_slug(session, agent_id)
        if agent is None:
            return jsonify({"error": f"catalog agent not found: {agent_id}"}), 400
        if not agent.enabled:
            return jsonify({"error": f"catalog agent is disabled: {agent_id}"}), 400

    cli = payload.get("cli")
    if cli is not None and cli not in available_adapters():
        return jsonify({"error": f"unsupported agent cli: {cli}"}), 400

    # Refuse to create a task whose backend isn't installed: a missing binary
    # would only fail the run after creation. Resolve the same precedence the
    # queue uses at dispatch (task override > agent pin > default backend).
    agent_cli = agent.cli if agent is not None else None
    default_cli = settings.get_setting(session, "default_backend")
    effective_cli = cli or agent_cli or default_cli
    if isinstance(effective_cli, str) and not is_backend_available(effective_cli):
        installed = [c for c in available_adapters() if is_backend_available(c)]
        hint = f" Installed here: {', '.join(installed)}." if installed else ""
        return (
            jsonify(
                {
                    "error": (
                        f"backend '{effective_cli}' is not installed on this machine — "
                        f"the task was not created. Pick an installed backend.{hint}"
                    ),
                    "missing_backend": effective_cli,
                    "installed_backends": installed,
                }
            ),
            400,
        )

    model = payload.get("model")
    if model is not None and not isinstance(model, str):
        return jsonify({"error": "`model` must be a string"}), 400

    raw_timeout = payload.get("timeout_minutes")
    if isinstance(raw_timeout, int) and raw_timeout > 0:
        timeout_minutes = raw_timeout
    else:
        raw_default = settings.get_setting(session, "default_timeout_minutes") or 60
        timeout_minutes = (
            raw_default if isinstance(raw_default, int) and raw_default > 0 else 60
        )

    type_ = payload.get("type", "freeform")
    prompt = payload.get("prompt", "")
    issue_number = payload.get("issue_number")
    pr_number = payload.get("pr_number")
    # F8 — validate number fields up front: bare int() conversions below
    # used to raise unhandled ValueError → HTTP 500 on garbage input.
    pr_number_int, _err = _parse_number(pr_number, "pr_number")
    if _err is not None:
        return jsonify({"error": _err}), 400
    issue_number_int, _err = _parse_number(issue_number, "issue_number")
    if _err is not None:
        return jsonify({"error": _err}), 400
    if type_ == "issue_fix" and issue_number is None:
        return jsonify({"error": "issue_number is required for issue_fix tasks"}), 400
    if type_ == "pr_review" and pr_number is None:
        return jsonify({"error": "pr_number is required for pr_review tasks"}), 400

    env_vars = payload.get("env_vars")
    if env_vars is not None and (
        not isinstance(env_vars, list) or not all(isinstance(n, str) for n in env_vars)
    ):
        return jsonify({"error": "env_vars must be a list of names"}), 400

    # Per-task publish mode: explicit override, else a type-based default
    # (issue_fix auto-publishes on done; freeform/manual types default to manual
    # publish so exploratory work isn't silently pushed as a PR). None falls
    # back to the global auto_publish setting.
    publish_mode = payload.get("publish_mode")
    if publish_mode is None:
        publish_mode = "auto" if type_ == "issue_fix" else "manual"

    # Creation-time "address the review comments on the linked PR" (freeform
    # only): guarantees the address-reviews instruction in the run prompt even
    # when no reviews were fetched at creation time.
    address_reviews = payload.get("address_reviews", False)
    if not isinstance(address_reviews, bool):
        return jsonify({"error": "address_reviews must be a boolean"}), 400
    if address_reviews and type_ != "freeform":
        return jsonify({"error": "address_reviews is only valid for freeform tasks"}), 400
    if address_reviews and pr_number is None:
        return jsonify({"error": "address_reviews requires a linked pr_number"}), 400

    pat_name = payload.get("pat_name")
    if pat_name is not None and not _valid_pat(config, pat_name):
        return jsonify({"error": f"unknown PAT: {pat_name}"}), 400

    source_branch = payload.get("source_branch")
    target_branch = payload.get("target_branch")
    repo = session.get(db.Repo, repo_id)
    if source_branch is None or not str(source_branch):
        source_branch = repo.default_branch if repo else "main"
    if target_branch is None or not str(target_branch):
        target_branch = repo.default_branch if repo else "main"

    # The account is explicit, never guessed: the user-selected one, else the
    # account bound to the repo at connect time. No "default"/fallback exists.
    effective_pat = pat_name or (repo.pat_name if repo is not None else None)
    if not effective_pat:
        return jsonify({"error": "select an account (pat_name) for this task"}), 400
    if not _valid_pat(config, effective_pat):
        return jsonify({"error": f"unknown PAT: {effective_pat}"}), 400

    # PR-head worktree base (fork-aware fix flow, freeform only): the worktree
    # starts at the current head of the linked PR instead of an origin branch,
    # so a fork branch that never exists on origin can still be addressed.
    pr_head_num = tasks.pr_head_source_number(str(source_branch))
    if pr_head_num is not None:
        if type_ != "freeform":
            return jsonify({"error": "pr-head source is only valid for freeform tasks"}), 400
        if pr_number_int is None or pr_number_int != pr_head_num:
            return jsonify({"error": "pr-head source must match the linked pr_number"}), 400

    try:
        context = _fetch_context(
            session, repo_id, issue_number, pr_number, pat_name=effective_pat
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if pr_head_num is not None:
        # The merge target is the PR's base (authoritative from the fetched
        # context) so the new_pr fallback opens against the right branch even
        # when the form left target at its default.
        try:
            prs_ctx = (context.get("prs") or []) if isinstance(context, dict) else []
            base = prs_ctx[0].get("base") if prs_ctx else None
            if isinstance(base, str) and base:
                target_branch = base
        except (IndexError, AttributeError, TypeError):
            pass

    # Reviewer workflow (PRD F7): a pr_review task may select reviewers from the
    # catalog (kind reviewer). Each reviewer runs as its OWN pr_review task;
    # assignments link task ↔ agent ↔ PR and track posted status.
    reviewers = payload.get("reviewers")
    if reviewers is not None and not isinstance(reviewers, list):
        return jsonify({"error": "reviewers must be a list of catalog agent ids"}), 400
    if reviewers:
        if type_ != "pr_review":
            return jsonify({"error": "reviewers are only valid for pr_review tasks"}), 400
        if pr_number is None:
            return jsonify({"error": "pr_number is required for pr_review tasks"}), 400
        assert pr_number_int is not None  # validated above; None returns 400 here
        if repo is None:
            return jsonify({"error": "repo not found"}), 400
        try:
            created = reviews.assign_reviewers(
                session, repo, pr_number_int, [str(r) for r in reviewers],
                queue=_queue(), masker=masker,
            )
        except reviews.ReviewError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify([_task_dict(session, t) for t in created]), 201

    try:
        task = tasks.create_task(
            session,
            type_=type_,
            repo_id=repo_id,
            prompt=prompt,
            source_branch=str(source_branch),
            target_branch=str(target_branch),
            agent_id=agent_id,
            model=model,
            cli=cli,
            pat_name=effective_pat,
            issues=[issue_number_int] if issue_number_int is not None else None,
            prs=[pr_number_int] if pr_number_int is not None else None,
            context=context,
            env_vars=env_vars,
            timeout_minutes=timeout_minutes,
            publish_mode=publish_mode,
            address_reviews=address_reviews,
            masker=masker,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    _queue().enqueue(task.id)
    return jsonify(_task_dict(session, task)), 201


@bp.get("")
def list_tasks() -> ResponseReturnValue:
    session = db.get_session()
    return jsonify([_task_dict(session, task) for task in tasks.list_tasks(session)])


@bp.get("/<int:task_id>")
def get_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(_task_dict(session, task))


@bp.post("/<int:task_id>/cancel")
def cancel_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if task.status in ("queued", "needs_approval"):
        was_queued = task.status == "queued"
        task.status = "cancelled"
        task.updated_at = now()
        session.commit()
        # Also flag any in-flight pickup: if the worker has already registered
        # this task's _RunState (but not yet committed "running"), queue.cancel
        # sets state.reason so the worker kills the process instead of running
        # a task the user saw as cancelled.
        if was_queued:
            _queue().cancel(task_id)
        return jsonify({"status": "cancelled"})
    if task.status == "running":
        killed = _queue().cancel(task_id)
        return jsonify({"status": "cancelling", "killed": killed})
    return jsonify({"error": f"cannot cancel task in state {task.status}"}), 409


@bp.post("/<int:task_id>/dismiss-attention")
def dismiss_attention(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.dismiss_task_attention(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(_task_dict(session, task))


@bp.post("/<int:task_id>/rerun")
def rerun_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if task.status in ("queued", "running"):
        return jsonify({"error": f"cannot rerun task in state {task.status}"}), 409
    if tasks.has_unmet_deps(session, task.id):
        return (
            jsonify(
                {
                    "error": (
                        "cannot rerun: task is blocked by unmet dependencies "
                        "(resolve them first)"
                    )
                }
            ),
            409,
        )
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    if "cli" in payload:
        cli = payload.get("cli")
        if cli in (None, ""):
            task.cli = None
        elif cli not in available_adapters():
            return jsonify({"error": f"unsupported agent cli: {cli}"}), 400
        else:
            task.cli = cli
    if "model" in payload:
        raw_model = payload.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            return jsonify({"error": "`model` must be a string"}), 400
        task.model = raw_model or None
    # A fresh attempt gets a fresh recovery budget: without this, a task that
    # hit the attempt cap fails its rerun once and immediately gives up
    # again with zero auto-recovery.
    task.retry_count = 0
    task.status = "queued"
    task.updated_at = now()
    session.commit()
    _queue().enqueue(task.id)
    return jsonify(_task_dict(session, task))


# ---- Phase 4 T4.1 — task dependency endpoints ------------------------------


@bp.post("/<int:task_id>/dependencies")
def add_task_dependency(task_id: int) -> ResponseReturnValue:
    """Add a dependency edge ``task_id -> depends_on_id`` (Phase 4 T4.1).

    Body: ``{"depends_on_id": N}``. 400 on self-reference or cycle, 404 on
    missing task; the task's status is recomputed to ``blocked`` if the new
    edge leaves an unmet dep, else to ``queued`` if it satisfies a previous
    block.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    raw_dep = payload.get("depends_on_id")
    if raw_dep is None:
        return jsonify({"error": "depends_on_id is required"}), 400
    try:
        depends_on_id = int(raw_dep)
    except (TypeError, ValueError):
        return jsonify({"error": "depends_on_id must be an integer"}), 400
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    try:
        tasks.add_dependency(session, task_id, depends_on_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # Recompute blocked status. A blocked task with newly-unmet deps stays
    # blocked (the new dep blocks it from now on); a queued task whose
    # newly-added dep is unfinished becomes blocked.
    if tasks.has_unmet_deps(session, task_id):
        if task.status not in {"done", "needs_approval"}:
            task.status = "blocked"
            task.updated_at = now()
    elif task.status == "blocked":
        task.status = "queued"
        task.updated_at = now()
    session.commit()
    return jsonify(_task_dict(session, task)), 201


@bp.delete("/<int:task_id>/dependencies/<int:dep_id>")
def remove_task_dependency(task_id: int, dep_id: int) -> ResponseReturnValue:
    """Remove a dependency edge (Phase 4 T4.1)."""
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if not tasks.remove_dependency(session, task_id, dep_id):
        return jsonify({"error": "dependency not found"}), 404
    # Recompute: a blocked task with no remaining unmet deps goes queued.
    if task.status == "blocked" and not tasks.has_unmet_deps(session, task_id):
        task.status = "queued"
        task.updated_at = now()
    session.commit()
    return jsonify(_task_dict(session, task))


@bp.delete("/<int:task_id>")
def delete_task(task_id: int) -> ResponseReturnValue:
    """Delete a task and everything tied to it (runs, follow-ups, artifacts,
    worktree, branch). Running/queued tasks are cancelled first."""
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404

    repo = session.get(db.Repo, task.repo_id)
    full_name = repo.full_name if repo is not None else None

    # Cancel first so no orphaned agent keeps working on data about to vanish.
    if task.status == "running":
        _queue().cancel(task_id)
    if task.status in ("queued", "running"):
        task.status = "cancelled"
        session.commit()

    # Use the canonical cascade helper so the task + every child row are
    # removed in the right FK order. Returning ``run_ids`` lets the disk
    # cleanup below drop the artifact store dirs and the worktree path.
    run_ids = tasks.delete_tasks_cascade(session, [task_id])
    session.commit()

    # Best-effort disk cleanup (outside the DB transaction). The worktree is
    # removed via GitWorkspace so the mirror's registration is also cleared —
    # leaving a "missing but already registered" worktree behind would break a
    # future task that reuses this task id (GitWorkspaceError on worktree add).
    config: Config = current_app.config["JALEBI_CONFIG"]
    for run_id in run_ids:
        shutil.rmtree(
            artifacts.artifact_store_dir(config.data_dir) / str(run_id), ignore_errors=True
        )
    shutil.rmtree(
        GitWorkspace.review_worktree_path(config.data_dir, task_id), ignore_errors=True
    )
    if full_name is not None:
        git = GitWorkspace(config)
        git.remove_worktree(task_id, full_name)
    else:
        shutil.rmtree(
            GitWorkspace.worktree_path(config.data_dir, task_id), ignore_errors=True
        )
    return jsonify({"deleted": task_id})


@bp.post("/<int:task_id>/publish")
def publish_task(task_id: int) -> ResponseReturnValue:
    """Manually publish a task. Body: ``{mode, branch?, pr_number?}`` (all optional).

    - ``mode="new_pr"`` (default) — push ``jalebi/<id>`` and open/reuse a PR.
    - ``mode="update_pr"`` — push onto an existing PR's head branch (requires
      ``pr_number``, or falls back to ``task.prs_json[0]``).
    - ``mode="push_branch"`` — push onto ``branch`` directly (requires ``branch``).

    Errors:
    - 400 invalid mode / missing required field
    - 404 task or repo not found
    - 409 conflict (merge conflict / closed PR / no-op gate)
    - 412 remote branch moved since last sync (``--force-with-lease`` refused)
    - 502 anything else
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "expected JSON object body"}), 400
    mode = payload.get("mode", "new_pr")
    if mode not in ("new_pr", "update_pr", "push_branch"):
        return jsonify({"error": f"unknown publish mode: {mode!r}"}), 400
    target_branch = payload.get("branch")
    if target_branch is not None and not isinstance(target_branch, str):
        return jsonify({"error": "`branch` must be a string"}), 400
    pr_number = payload.get("pr_number")
    if pr_number is not None and not isinstance(pr_number, int):
        return jsonify({"error": "`pr_number` must be an integer"}), 400
    try:
        result = _queue().publish_task(
            task_id,
            mode=mode,
            target_branch=target_branch,
            pr_number=pr_number,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PublishConflict as exc:
        return jsonify({"error": str(exc), "kind": "conflict"}), 409
    except PublishError as exc:
        return jsonify({"error": str(exc), "kind": "publish"}), 409
    except PushLeaseFailed as exc:
        return jsonify({"error": str(exc), "kind": "lease_failed"}), 412
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"error": str(exc)}), 502
    body: dict[str, object] = {"status": "done", "mode": mode}
    if mode == "push_branch":
        body["branch"] = target_branch
    else:
        body["pr_number"] = result
    return jsonify(body)


@bp.post("/<int:task_id>/followup")
def followup_task(task_id: int) -> ResponseReturnValue:
    """Post a follow-up that resumes the task's last session (PRD F11)."""
    config: Config = current_app.config["JALEBI_CONFIG"]
    payload = request.get_json(silent=True)
    body = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(body, str) or not body.strip():
        return jsonify({"error": 'expected JSON body {"prompt": "<follow-up text>"}'}), 400

    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404

    # Input validation before state checks: malformed overrides are a 400
    # even when the task couldn't resume anyway.
    pat_name = payload.get("pat_name") if isinstance(payload, dict) else None
    if pat_name is not None and not _valid_pat(config, pat_name):
        return jsonify({"error": f"unknown PAT: {pat_name}"}), 400
    model = payload.get("model") if isinstance(payload, dict) else None
    if model is not None and not isinstance(model, str):
        return jsonify({"error": "`model` must be a string"}), 400
    cli = payload.get("cli") if isinstance(payload, dict) else None
    if cli is not None and cli not in available_adapters():
        return jsonify({"error": f"unsupported agent cli: {cli}"}), 400

    if task.status in ("queued", "running"):
        return jsonify({"error": f"cannot follow up on a task in state {task.status}"}), 409
    prev = tasks.latest_resumable_run(session, task_id)
    if prev is None:
        return jsonify({"error": "no resumable session for this task"}), 409

    masker = _masker(session)

    # "Address the reviewers" follow-up (PRD F7.6 / F11): when include_reviews
    # is set, the current PR review comments are fetched (masked) and embedded
    # into the prompt so the fixer can address them without guessing URLs.
    include_reviews = bool(payload.get("include_reviews")) if isinstance(payload, dict) else False
    if include_reviews:
        body = _with_review_comments(session, task, body, masker=masker)

    masked = masker(body.strip())
    # The Followup row (incl. PAT/model overrides) is recorded by the worker when
    # the resume actually runs — not here, to avoid duplicates.
    _queue().enqueue_followup(task_id, masked, pat_name=pat_name, model=model, cli=cli)
    return jsonify(_task_dict(session, task)), 202


def _with_review_comments(session, task: Task, body: str, *, masker) -> str:
    """Fetch the task's PR review comments and embed them into a follow-up prompt.

    Uses the task's own account (no fallback). Best-effort: if the PR can't be
    found or fetching fails, the follow-up proceeds with the user's text alone
    (the agent is told to fetch reviews itself as a fallback).
    """
    config: Config = current_app.config["JALEBI_CONFIG"]
    pr_number = task.pr_number
    if pr_number is None and task.prs_json:
        try:
            pr_number = int(json.loads(task.prs_json)[0])
        except (ValueError, TypeError, IndexError):
            pr_number = None
    if pr_number is None:
        return prompts.build_address_reviewers_prompt(body, [])
    token = secrets.resolve_token(config, task.pat_name)
    if token is None:
        return prompts.build_address_reviewers_prompt(body, [])
    repo = session.get(db.Repo, task.repo_id)
    if repo is None:
        return prompts.build_address_reviewers_prompt(body, [])
    client = GitHubClient(token)
    try:
        reviews = client.list_pr_reviews(repo.full_name, pr_number)
    except Exception:
        reviews = []
    finally:
        client.close()
    masked_reviews = []
    for review in reviews:
        text = review.get("body") or ""
        if text.strip():
            masked_reviews.append({"author": review.get("user") or "unknown", "body": masker(text)})
    return prompts.build_address_reviewers_prompt(body, masked_reviews)


@bp.post("/<int:task_id>/reviewers")
def assign_reviewers(task_id: int) -> ResponseReturnValue:
    """Assign catalog reviewers to a task's PR (PRD F7.1).

    Each reviewer runs as its own ``pr_review`` task. Body: {reviewers: [agent_id]}.
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if not task.pr_number and not task.prs_json:
        return jsonify({"error": "this task has no PR to review"}), 409
    pr_number = task.pr_number
    if pr_number is None and task.prs_json:
        try:
            pr_number = int(json.loads(task.prs_json)[0])
        except (ValueError, TypeError, IndexError):
            pr_number = None
    if pr_number is None:
        return jsonify({"error": "this task has no PR to review"}), 409
    repo = session.get(db.Repo, task.repo_id)
    if repo is None:
        return jsonify({"error": "repo not found"}), 404

    payload = request.get_json(silent=True)
    reviewers = payload.get("reviewers") if isinstance(payload, dict) else None
    if not isinstance(reviewers, list) or not all(isinstance(r, str) for r in reviewers):
        return jsonify({"error": 'expected JSON body {"reviewers": ["<agent_id>", ...]}'}), 400

    masker = _masker(session)
    try:
        created = reviews.assign_reviewers(
            session, repo, pr_number, reviewers,
            queue=_queue(), masker=masker,
        )
    except reviews.ReviewError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify([_task_dict(session, t) for t in created]), 201


@bp.get("/<int:task_id>/runs")
def list_runs(task_id: int) -> ResponseReturnValue:
    """All runs for a task (run selector on the detail page)."""
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(
        [
            tasks.run_to_dict(
                run, artifacts=tasks.list_artifacts(session, run.id) or []
            )
            for run in tasks.runs_for_task(session, task_id)
        ]
    )


@bp.get("/<int:task_id>/artifacts/<int:artifact_id>/content")
def artifact_content(task_id: int, artifact_id: int) -> ResponseReturnValue:
    """Serve an artifact inline (for the in-browser preview)."""
    session = db.get_session()
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        return jsonify({"error": "artifact not found"}), 404
    run = session.get(Run, artifact.run_id)
    if run is None or run.task_id != task_id:
        return jsonify({"error": "artifact not found"}), 404

    config: Config = current_app.config["JALEBI_CONFIG"]
    try:
        path = artifacts.artifact_file(config.data_dir, run.id, artifact.path)
    except ValueError:
        return jsonify({"error": "artifact not found"}), 404
    if not path.is_file():
        return jsonify({"error": "artifact file missing"}), 404
    return send_file(path, as_attachment=False)


@bp.get("/<int:task_id>/artifacts/<int:artifact_id>/download")
def download_artifact(task_id: int, artifact_id: int) -> ResponseReturnValue:
    """Download a captured artifact for a task (PRD F18)."""
    session = db.get_session()
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        return jsonify({"error": "artifact not found"}), 404
    run = session.get(Run, artifact.run_id)
    if run is None or run.task_id != task_id:
        return jsonify({"error": "artifact not found"}), 404

    config: Config = current_app.config["JALEBI_CONFIG"]
    try:
        path = artifacts.artifact_file(config.data_dir, run.id, artifact.path)
    except ValueError:
        return jsonify({"error": "artifact not found"}), 404
    if not path.is_file():
        return jsonify({"error": "artifact file missing"}), 404
    return send_file(path, as_attachment=True, download_name=Path(artifact.path).name)


@bp.get("/<int:task_id>/runs/<int:run_id>/diff")
def run_diff(task_id: int, run_id: int) -> ResponseReturnValue:
    """The run-end diff snapshot for a run (PRD §12 diff viewer)."""
    session = db.get_session()
    run = session.get(Run, run_id)
    if run is None or run.task_id != task_id:
        return jsonify({"error": "run not found"}), 404
    return jsonify({"diff": run.diff_text or ""})


# ---- Phase 4 T1.2 / T1.3 — live diff with optional base + untracked --------


@bp.get("/<int:task_id>/diff")
def live_diff(task_id: int) -> ResponseReturnValue:
    """Live diff of the task's worktree branch.

    Additive query params (all default OFF — zero observable change):
    - ``?base=1`` → cherry-pick-aware merge-base diff (T1.2).
    - ``?untracked=1`` → append synthesized hunks for untracked files (T1.3).

    Returns ``{diff, base, untracked}``. The diff is run through the existing
    masker (a secret the agent wrote to disk is redacted before display).
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    config: Config = current_app.config["JALEBI_CONFIG"]
    worktree = GitWorkspace.worktree_path(config.data_dir, task.id)
    if not (worktree / ".git").is_file():
        return jsonify({"error": "task has no worktree yet"}), 409
    base_ref = tasks.effective_diff_base(task)
    use_base = request.args.get("base") == "1"
    use_untracked = request.args.get("untracked") == "1"
    git = GitWorkspace(config)
    try:
        diff = (
            git.diff_against_base(worktree, base_ref)
            if use_base
            else git.diff_against_target(worktree, base_ref)
        )
        if use_untracked:
            diff = git.diff_with_untracked(worktree, diff)
    except GitWorkspaceError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(
        {"diff": _masker(session)(diff), "base": use_base, "untracked": use_untracked}
    )


# ---- Phase 4 T1.4 — predictive conflict check --------------------------------


@bp.get("/<int:task_id>/merge-check")
def merge_check(task_id: int) -> ResponseReturnValue:
    """Predict conflicts merging ``jalebi/<taskId>`` into the task's target.

    Reads the mirror only (no worktree mutation, no abort dance). Returns
    ``{ok, conflicts: [{kind, path}]}``; refuses 409 when the worktree HEAD
    is not on the canonical branch (T1.5 guard).
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    repo = session.get(db.Repo, task.repo_id)
    if repo is None:
        return jsonify({"error": "repo not found"}), 404
    config: Config = current_app.config["JALEBI_CONFIG"]
    git = GitWorkspace(config)
    try:
        git.assert_publish_branch(task_id)  # T1.5 — refuse a wrong-branch worktree
    except GitWorkspaceError as exc:
        return jsonify({"error": str(exc), "kind": "branch_mismatch"}), 409
    token = secrets.resolve_token(config, task.pat_name)
    try:
        git.ensure_mirror(repo.full_name, repo.clone_url, token)  # idempotent fetch
        base_ref = tasks.effective_diff_base(task)
        conflicts = git.predict_conflicts(repo.full_name, task_id, base_ref)
    except GitWorkspaceError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(
        {
            "conflicts": [{"kind": k, "path": p} for k, p in conflicts],
            "ok": not conflicts,
        }
    )


# ---- Phase 4 T7 — in-worktree file browser (read-only) ----------------------


@bp.get("/<int:task_id>/files")
def task_files(task_id: int) -> ResponseReturnValue:
    """List a directory under the task's worktree (Phase 4 T7).

    ``?path=<rel_dir>`` default root. Read-only. Containment + symlink +
    .git guards live in ``workspace_files``.
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    rel = request.args.get("path", "")
    config: Config = current_app.config["JALEBI_CONFIG"]
    entries, error = workspace_files.list_worktree_dir(config, task.id, rel)
    if error is not None:
        status = 404 if error == "task has no worktree yet" else 400
        return jsonify({"error": error}), status
    return jsonify({"path": rel, "entries": entries})


@bp.get("/<int:task_id>/files/content")
def task_file_content(task_id: int) -> ResponseReturnValue:
    """Serve a text file's content (masked) for inline viewing (Phase 4 T7).

    Binary files → 415 (download via the existing artifact flow).
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    rel = request.args.get("path", "")
    config: Config = current_app.config["JALEBI_CONFIG"]
    content, error = workspace_files.read_worktree_file(config, task.id, rel)
    if error is not None:
        if error == "binary":
            return (
                jsonify(
                    {
                        "error": "binary — use Artifacts to download",
                        "binary": True,
                    }
                ),
                415,
            )
        status = 404 if error in {"task has no worktree yet", "not a file"} else 400
        return jsonify({"error": error}), status
    masked = _masker(session)(content or "")
    return jsonify({"path": rel, "content": masked, "binary": False})


# ---- Phase 4 T3.2 — merge-readiness panel -----------------------------------


@bp.get("/<int:task_id>/publish-check")
def publish_check(task_id: int) -> ResponseReturnValue:
    """Advisory readiness check for the Publish button (Phase 4 T3.2).

    Combines branch-guard + commits-ahead + conflict + PR/CI facts into
    a single response the UI's ``MergeReadinessPanel`` consumes. Advisory
    only — never raises 4xx for a failed check (the panel renders the
    reason; the Publish button remains clickable when ``status`` is
    ``ready`` or ``attention``).

    ``status`` derivation:
      - ``blocked`` — branch mismatch, nothing to publish, or predicted
        conflict (cannot proceed).
      - ``attention`` — everything else passable but CI pending/failed,
        reviews requested, or GitHub not-mergeable (proceed with caution).
      - ``ready`` — every check passes.
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    repo = session.get(db.Repo, task.repo_id)
    if repo is None:
        return jsonify({"error": "repo not found"}), 404

    config: Config = current_app.config["JALEBI_CONFIG"]
    git = GitWorkspace(config)
    worktree = git.worktree_path(config.data_dir, task.id)
    base_ref = tasks.effective_diff_base(task)

    checks: list[dict[str, object]] = []

    # 1. branch — must be on jalebi/<id>.
    try:
        git.assert_publish_branch(task_id)
        checks.append({"name": "branch", "ok": True, "message": "on jalebi branch"})
    except GitWorkspaceError as exc:
        checks.append({"name": "branch", "ok": False, "message": str(exc)})

    # 2. commits — must be ahead of origin/<base_ref> to actually publish.
    ahead = git.commits_ahead(worktree, base_ref)
    checks.append(
        {
            "name": "commits",
            "ok": ahead > 0,
            "ahead": ahead,
            "message": (
                f"{ahead} commit{'s' if ahead != 1 else ''} ahead of origin/{base_ref}"
                if ahead > 0
                else "nothing to publish — HEAD matches origin"
            ),
        }
    )

    # 3. conflict — predict via merge-tree on the mirror.
    token = secrets.resolve_token(config, task.pat_name)
    try:
        git.ensure_mirror(repo.full_name, repo.clone_url, token)
        conflicts = git.predict_conflicts(repo.full_name, task_id, base_ref)
        checks.append(
            {
                "name": "conflict",
                "ok": not conflicts,
                "conflicts": [{"kind": k, "path": p} for k, p in conflicts],
                "message": (
                    "no predicted conflicts"
                    if not conflicts
                    else f"{len(conflicts)} predicted conflict{'s' if len(conflicts) != 1 else ''}"
                ),
            }
        )
    except GitWorkspaceError as exc:
        checks.append(
            {
                "name": "conflict",
                "ok": True,  # can't predict; don't block
                "message": f"conflict check unavailable: {exc}",
            }
        )

    # 4–6. ci / review / mergeable — from the poller's PRFacts (T2).
    poller = current_app.config.get("JALEBI_POLLER")
    pr_facts = (
        poller.pr_facts_for_task(task.repo_id, task.id)
        if poller is not None
        else None
    )

    if pr_facts is None:
        for name in ("ci", "review", "mergeable"):
            checks.append(
                {
                    "name": name,
                    "ok": True,
                    "message": "no PR yet — not applicable",
                }
            )
    else:
        ci_state = pr_facts.get("ci_state")
        if ci_state == "success":
            ci_msg, ci_ok = "CI is green", True
        elif ci_state == "pending":
            ci_msg, ci_ok = "CI is pending", False
        elif ci_state == "failure":
            ci_msg, ci_ok = "CI is failing", False
        else:
            ci_msg, ci_ok = "CI status unknown", True
        checks.append({"name": "ci", "ok": ci_ok, "state": ci_state, "message": ci_msg})

        rd = pr_facts.get("review_decision")
        if rd == "approved":
            rd_msg, rd_ok = "PR is approved", True
        elif rd == "changes_requested":
            rd_msg, rd_ok = "reviewers requested changes", False
        elif rd == "review_required":
            rd_msg, rd_ok = "PR still needs reviews", False
        else:
            rd_msg, rd_ok = "no reviews yet", True
        checks.append(
            {"name": "review", "ok": rd_ok, "decision": rd, "message": rd_msg}
        )

        mergeable = pr_facts.get("mergeable")
        checks.append(
            {
                "name": "mergeable",
                "ok": mergeable is True,
                "mergeable": mergeable,
                "message": (
                    "PR is mergeable"
                    if mergeable is True
                    else "GitHub reports merge conflicts or required checks"
                    if mergeable is False
                    else "GitHub mergeable status unknown"
                ),
            }
        )

    blocked_names = {"branch", "commits", "conflict"}
    blocked = any(not c["ok"] for c in checks if c.get("name") in blocked_names)
    attention = (not blocked) and any(not c["ok"] for c in checks)
    status = "blocked" if blocked else ("attention" if attention else "ready")

    return jsonify({"status": status, "base_ref": base_ref, "checks": checks})


# ---- Phase 4 T6 — open a task's worktree in the configured IDE -------------


@bp.post("/<int:task_id>/open-in-ide")
def open_in_ide(task_id: int) -> ResponseReturnValue:
    """Spawn the configured IDE on the task's worktree (Phase 4 T6).

    Refuses with 409 when ``ide_command`` is empty or doesn't resolve;
    404 when the task has no worktree yet. The command comes from the
    validated setting only — never from a per-request input — so the
    single security boundary is ``ide.validate_ide_command``.
    """
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    command = str(settings.get_setting(session, "ide_command") or "")
    if not command:
        return (
            jsonify(
                {
                    "error": (
                        "IDE not configured — set an IDE command in Settings"
                    )
                }
            ),
            409,
        )
    config: Config = current_app.config["JALEBI_CONFIG"]
    worktree = GitWorkspace.worktree_path(config.data_dir, task.id)
    if not (worktree / ".git").is_file():
        return jsonify({"error": "task has no worktree yet"}), 404
    try:
        ide.open_in_ide(command, worktree)
    except ide.IdeError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "path": str(worktree)})


@bp.get("/<int:task_id>/events")
def task_events(task_id: int) -> ResponseReturnValue:
    """SSE stream of live (masked) events for a task's current run.

    Phase 4 T4.3 — when ``after_seq`` is given, the route backfills from the
    durable ``task_events`` table first (replay across restarts), then the
    in-memory bus takes over. Falls back to memory-only if persistence is not
    wired (standalone ``TaskEvents()`` in tests).

    Every data frame also carries a standard SSE ``id:`` line (the event seq)
    so native ``EventSource`` auto-reconnect resumes from ``Last-Event-ID``
    when the query param is absent. Idle heartbeats are sent as ``ping`` data
    frames (not ``:`` comments, which browsers discard without dispatching)
    so clients can tell a quiet-but-alive stream apart from a stalled socket.
    """
    from jalebi.events import replay_from_db

    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404

    run = tasks.latest_run(session, task_id)
    terminal = run is not None and run.status in TERMINAL_STATUSES
    events = _queue().events
    after_seq = request.args.get("after_seq", type=int)
    if after_seq is None:
        # Native EventSource reconnect: the browser re-sends Last-Event-ID
        # from the last `id:` line it received.
        last_id = request.headers.get("Last-Event-ID")
        try:
            after_seq = int(last_id) if last_id is not None else None
        except (TypeError, ValueError):
            after_seq = None
    run_id = run.id if run is not None else None
    # Phase 4 T4.3 — durable backfill on reconnect, WITHOUT duplicating
    # what the memory bus will also replay (F6): read the DB first, then
    # subscribe the memory bus from the DB high-water mark. Anything
    # published between the DB read and the subscribe is still in the
    # memory buffer (publish writes memory before the DB row), so raising
    # the watermark loses nothing and replays nothing twice.
    db_items: list = []
    resume_from = after_seq
    if after_seq is not None:
        db_items = replay_from_db(session, task_id, run_id, after_seq)
        for _item in db_items:
            _seq = _item.get("seq") if isinstance(_item, dict) else None
            if isinstance(_seq, int) and (resume_from is None or _seq > resume_from):
                resume_from = _seq
    q = events.subscribe(task_id, after_seq=resume_from, run_id=run_id)

    def _frame(item: dict) -> str:
        seq = item.get("seq") if isinstance(item, dict) else None
        prefix = f"id: {seq}\n" if isinstance(seq, int) else ""
        return f"{prefix}data: {json.dumps(item)}\n\n"

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            for item in db_items:
                yield _frame(item)
            if terminal:
                yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                return
            while True:
                try:
                    item = q.get(timeout=15)
                except _queue_module.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                    continue
                if item is None:
                    yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                    return
                yield _frame(item)
        finally:
            events.unsubscribe(task_id, q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
