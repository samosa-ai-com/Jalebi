"""Task routes: create, list, detail, cancel, rerun, publish, delete, live events."""

import json
import queue as _queue_module
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
from flask import Blueprint, Response, current_app, jsonify, request, send_file
from flask.typing import ResponseReturnValue

from jalebi import artifacts, db, masking, prompts, reviews, secrets, settings, tasks
from jalebi.adapters import available_adapters
from jalebi.catalog import agent_by_slug
from jalebi.config import Config
from jalebi.db import Artifact, Run, Task, now
from jalebi.git_workspace import GitWorkspace, PushLeaseFailed
from jalebi.github import GitHubClient, GitHubError
from jalebi.queue import PublishConflict, PublishError, TaskQueue

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")

TERMINAL_STATUSES = {"done", "failed", "timed_out", "cancelled", "needs_approval", "interrupted"}


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
    return tasks.task_to_dict(
        task,
        run=run,
        followups=tasks.list_followups(session, task.id),
        artifacts=tasks.list_artifacts(session, run.id) if run is not None else None,
        repo_full_name=_repo_name(session, task.repo_id),
        reviewers=reviewers,
    )


def _valid_pat(config, name: str | None) -> bool:
    """A PAT/account name is valid only if it's a stored named account."""
    return name in secrets.token_names(config)


def _masker(session) -> Callable[[str], str]:
    config: Config = current_app.config["JALEBI_CONFIG"]
    patterns = settings.get_setting(session, "secret_patterns") or []
    patterns = [str(p) for p in patterns] if isinstance(patterns, list) else []
    values = secrets.all_token_values(config)
    return masking.build_masker(values, patterns)


def _fetch_context(
    session,
    repo_id: int,
    type_: str,
    issue_number: int | None,
    pr_number: int | None,
    pat_name: str | None = None,
):
    """Fetch issue/PR context from GitHub for structured task types (masked)."""
    if type_ not in ("issue_fix", "pr_review"):
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
        if type_ == "issue_fix" and issue_number is not None:
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
        if type_ == "pr_review" and pr_number is not None:
            pr = client.get_pr(repo.full_name, pr_number)
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

    model = payload.get("model")

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

    try:
        context = _fetch_context(
            session, repo_id, type_, issue_number, pr_number, pat_name=effective_pat
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

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
        if repo is None:
            return jsonify({"error": "repo not found"}), 400
        try:
            created = reviews.assign_reviewers(
                session, repo, int(pr_number), [str(r) for r in reviewers],
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
            issues=[int(issue_number)] if issue_number is not None else None,
            prs=[int(pr_number)] if pr_number is not None else None,
            context=context,
            env_vars=env_vars,
            timeout_minutes=timeout_minutes,
            publish_mode=publish_mode,
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
    if task.status == "queued":
        task.status = "cancelled"
        task.updated_at = now()
        session.commit()
        # Also flag any in-flight pickup: if the worker has already registered
        # this task's _RunState (but not yet committed "running"), queue.cancel
        # sets state.reason so the worker kills the process instead of running
        # a task the user saw as cancelled.
        _queue().cancel(task_id)
        return jsonify({"status": "cancelled"})
    if task.status == "running":
        killed = _queue().cancel(task_id)
        return jsonify({"status": "cancelling", "killed": killed})
    return jsonify({"error": f"cannot cancel task in state {task.status}"}), 409


@bp.post("/<int:task_id>/rerun")
def rerun_task(task_id: int) -> ResponseReturnValue:
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    if task.status in ("queued", "running"):
        return jsonify({"error": f"cannot rerun task in state {task.status}"}), 409
    task.status = "queued"
    task.updated_at = now()
    session.commit()
    _queue().enqueue(task.id)
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
    if task.status in ("queued", "running"):
        return jsonify({"error": f"cannot follow up on a task in state {task.status}"}), 409
    prev = tasks.latest_resumable_run(session, task_id)
    if prev is None:
        return jsonify({"error": "no resumable session for this task"}), 409

    # Optional follow-up account override: when omitted, the follow-up resumes
    # under the task's own account (no default/fallback exists).
    pat_name = payload.get("pat_name") if isinstance(payload, dict) else None
    if pat_name is not None and not _valid_pat(config, pat_name):
        return jsonify({"error": f"unknown PAT: {pat_name}"}), 400
    model = payload.get("model") if isinstance(payload, dict) else None
    # Optional backend override: a backend different from the task's own starts
    # a fresh session seeded with the prior conversation (see queue._run_followup).
    cli = payload.get("cli") if isinstance(payload, dict) else None
    if cli is not None and cli not in available_adapters():
        return jsonify({"error": f"unsupported agent cli: {cli}"}), 400

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


@bp.get("/<int:task_id>/events")
def task_events(task_id: int) -> ResponseReturnValue:
    """SSE stream of live (masked) events for a task's current run."""
    session = db.get_session()
    task = tasks.get_task(session, task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404

    run = tasks.latest_run(session, task_id)
    terminal = run is not None and run.status in TERMINAL_STATUSES
    events = _queue().events
    after_seq = request.args.get("after_seq", type=int)
    q = events.subscribe(task_id, after_seq=after_seq)

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            if terminal:
                yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                return
            while True:
                try:
                    item = q.get(timeout=15)
                except _queue_module.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                    return
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            events.unsubscribe(task_id, q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
