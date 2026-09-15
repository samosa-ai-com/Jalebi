"""GitHub integration routes: accounts (PATs), status, repo listing, context.

Every stored PAT is a first-class **account** and all accounts are equal —
there is no "default"/"primary" account and no fallback. Each account has its
own live status and its own repository list. Removing an account deletes its
connected repos and their tasks.
"""

import shutil
from dataclasses import asdict

import httpx
from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from jalebi import artifacts, db, secrets
from jalebi.db import (
    Artifact,
    EventDelivery,
    Followup,
    Repo,
    ReviewAssignment,
    Run,
    Task,
    TriggerRule,
)
from jalebi.git_workspace import GitWorkspace
from jalebi.github import GitHubClient, GitHubError, TokenInfo

bp = Blueprint("github", __name__, url_prefix="/api/github")


def _config():
    return current_app.config["JALEBI_CONFIG"]


def _client(token: str) -> GitHubClient:
    return GitHubClient(token)


def _validate(token: str) -> tuple[TokenInfo | None, str | None]:
    """Return (info, error); ``error`` set when GitHub is unreachable."""
    client = _client(token)
    try:
        return client.validate_token(), None
    except httpx.HTTPError as exc:
        return None, f"GitHub unreachable: {exc}"
    finally:
        client.close()


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}…{token[-4:]}"


def _account_dict(name: str, token: str) -> dict:
    info, error = _validate(token)
    if error is not None:
        return {"name": name, "valid": False, "error": error}
    assert info is not None
    base = asdict(info)
    base.update(
        {
            "name": name,
            "masked": _mask_token(token),
        }
    )
    return base


def _all_tokens() -> list[tuple[str, str]]:
    """[(name, token)] for every named account — all equal, no default."""
    return [
        (item["name"], item["token"]) for item in secrets.list_github_tokens(_config())
    ]


def _meta(info: TokenInfo) -> dict:
    """Storable metadata snapshot of a validated token."""
    return {
        "login": info.login,
        "token_type": info.token_type,
        "granted_scopes": info.granted_scopes,
        "missing_scopes": info.missing_scopes,
        "note": info.note,
        "has_workflow": info.has_workflow,
    }


@bp.get("/repos")
def repos() -> ResponseReturnValue:
    """List repositories across ALL accounts, each tagged with its account name.

    ``?account=<name>`` filters to one account. A failing account contributes
    its repos as an error entry rather than failing the whole page.
    """
    entries = _all_tokens()
    if not entries:
        return jsonify({"error": "no GitHub token configured"}), 409
    account = request.args.get("account")
    if account:
        entries = [e for e in entries if e[0] == account]
        if not entries:
            return jsonify({"error": f"no such account: {account}"}), 404

    results: list[dict] = []
    for name, token in entries:
        client = _client(token)
        try:
            items = client.list_repos()
            for repo in items:
                repo["account"] = name
            results.extend(items)
        except (httpx.HTTPError, GitHubError) as exc:
            results.append({"account": name, "error": str(exc)})
        finally:
            client.close()
    return jsonify(results)


@bp.get("/context")
def context() -> ResponseReturnValue:
    """Return open issues, open PRs and branches for a repo (task-form pickers).

    Uses the named account that owns the repo — there is no default/fallback.
    """
    full_name = request.args.get("repo")
    if not full_name or "/" not in full_name:
        return jsonify({"error": 'expected ?repo=owner/name'}), 400
    account = request.args.get("account")
    token = secrets.resolve_token(_config(), account)
    if not token:
        return jsonify({"error": "no GitHub token configured for this account"}), 409
    client = _client(token)
    try:
        return jsonify(
            {
                "issues": client.list_issues(full_name),
                "prs": client.list_prs(full_name),
                "branches": client.list_branches(full_name),
            }
        )
    except (httpx.HTTPError, GitHubError) as exc:
        return jsonify({"error": str(exc)}), 502
    finally:
        client.close()


@bp.get("/tokens")
def list_tokens() -> ResponseReturnValue:
    """List all accounts with live status. Never returns token values."""
    accounts = [
        _account_dict(name, token) for name, token in _all_tokens()
    ]
    return jsonify({"accounts": accounts})


@bp.post("/tokens")
def add_token() -> ResponseReturnValue:
    """Add a named account (validated first). Body: {name, token}.

    Adding an account whose name already exists is refused (409) — an existing
    account's credential is changed via ``PUT /api/github/tokens/<name>`` so a
    typo can never silently overwrite a token.
    """
    config = _config()
    payload = request.get_json(silent=True)
    name = payload.get("name") if isinstance(payload, dict) else None
    token = payload.get("token") if isinstance(payload, dict) else None
    if not name or not name.strip():
        return jsonify({"error": 'expected {"name": "<label>", "token": "<PAT>"}'}), 400
    if not token:
        return jsonify({"error": 'expected {"name": "<label>", "token": "<PAT>"}'}), 400
    name = name.strip()
    if name in secrets.token_names(config):
        return (
            jsonify(
                {
                    "error": (
                        f'account "{name}" already exists — use '
                        f"PUT /api/github/tokens/{name} to update its token"
                    )
                }
            ),
            409,
        )

    info, error = _validate(token)
    if error is not None:
        return jsonify({"valid": False, "error": error}), 502
    assert info is not None
    if not info.valid:
        return jsonify({"stored": False, "detail": asdict(info)}), 400

    secrets.add_github_token(config, name, token, meta=_meta(info))
    return jsonify({"stored": True, "name": name, "detail": asdict(info)})


@bp.put("/tokens/<name>")
def update_token(name: str) -> ResponseReturnValue:
    """Replace an existing account's PAT without touching any of its data.

    Update preserves the account *name*, so every binding (``repos.pat_name``,
    ``tasks.pat_name``, trigger/screening ownership) stays intact — no repos,
    tasks, or history are deleted. The token is validated before storing and its
    metadata is refreshed. ``previous_login`` vs ``login`` lets the UI flag an
    accidental identity change (the account now authenticates as a different
    GitHub user). Body: {"token": "<PAT>"}.
    """
    config = _config()
    previous = secrets.token_meta(config, name)
    if previous is None:
        return jsonify({"error": f"no such token: {name}"}), 404

    payload = request.get_json(silent=True)
    token = payload.get("token") if isinstance(payload, dict) else None
    if not token:
        return jsonify({"error": 'expected {"token": "<PAT>"}'}), 400

    info, error = _validate(token)
    if error is not None:
        return jsonify({"valid": False, "error": error}), 502
    assert info is not None
    if not info.valid:
        return jsonify({"stored": False, "detail": asdict(info)}), 400

    secrets.update_github_token(config, name, token, meta=_meta(info))
    return jsonify(
        {
            "updated": name,
            "detail": asdict(info),
            "previous_login": previous.get("login"),
            "login": info.login,
        }
    )


@bp.delete("/tokens/<name>")
def delete_token(name: str) -> ResponseReturnValue:
    """Remove an account AND delete everything tied to it.

    Deleting an account deletes its connected repos and the tasks on them
    (runs, follow-ups, artifacts, worktrees, mirrors). Running/queued tasks for
    the account are cancelled first.
    """
    config = _config()
    if name not in secrets.token_names(config):
        return jsonify({"error": f"no such token: {name}"}), 404

    session = db.get_session()

    # Every repo bound to the account is deleted — connected OR soft-disconnected.
    # (A disconnected repo's tasks would otherwise silently fall back to the
    # primary token after the account is gone.)
    repos = list(
        session.execute(select(Repo).where(Repo.pat_name == name)).scalars()
    )
    repo_ids = [r.id for r in repos]

    tasks = list(
        session.execute(
            select(Task).where(
                (Task.pat_name == name) | Task.repo_id.in_(repo_ids)
            )
        ).scalars()
    )
    task_ids = [t.id for t in tasks]

    # Cancel queued/running tasks so no orphaned agent keeps working on data
    # that is about to be deleted.
    queue = current_app.config["JALEBI_QUEUE"]
    for task in tasks:
        if task.status == "running":
            queue.cancel(task.id)
        if task.status in ("queued", "running"):
            task.status = "cancelled"

    # Cascade-delete children first (FK order): followups → artifacts → runs → tasks → repos.
    runs = list(
        session.execute(select(Run).where(Run.task_id.in_(task_ids))).scalars()
        if task_ids
        else []
    )
    run_ids = [r.id for r in runs]
    if task_ids:
        session.execute(sa_delete(Followup).where(Followup.task_id.in_(task_ids)))
        session.execute(sa_delete(ReviewAssignment).where(ReviewAssignment.task_id.in_(task_ids)))
        # Assignments whose PR lives on a deleted repo but whose reviewer task is
        # on another account would dangle — drop them too.
        if repo_ids:
            session.execute(
                sa_delete(ReviewAssignment).where(ReviewAssignment.repo_id.in_(repo_ids))
            )
    if run_ids:
        session.execute(sa_delete(Artifact).where(Artifact.run_id.in_(run_ids)))
        session.execute(sa_delete(Run).where(Run.id.in_(run_ids)))
    if task_ids:
        session.execute(sa_delete(Task).where(Task.id.in_(task_ids)))
    if repo_ids:
        # Webhook artifacts of the repo: delete deliveries first (they reference
        # both the repo and the rules), then the rules, then the repo.
        session.execute(
            sa_delete(EventDelivery).where(EventDelivery.repo_id.in_(repo_ids))
        )
        session.execute(sa_delete(TriggerRule).where(TriggerRule.repo_id.in_(repo_ids)))
        session.execute(sa_delete(Repo).where(Repo.id.in_(repo_ids)))

    secrets.remove_github_token(config, name)
    session.commit()

    # Best-effort disk cleanup (outside the DB transaction).
    for run_id in run_ids:
        shutil.rmtree(
            artifacts.artifact_store_dir(config.data_dir) / str(run_id), ignore_errors=True
        )
    for task_id in task_ids:
        shutil.rmtree(
            GitWorkspace.worktree_path(config.data_dir, task_id), ignore_errors=True
        )
        shutil.rmtree(
            GitWorkspace.review_worktree_path(config.data_dir, task_id), ignore_errors=True
        )
    for repo in repos:
        shutil.rmtree(GitWorkspace.mirror_path(config.data_dir, repo.full_name), ignore_errors=True)

    return jsonify(
        {
            "removed": name,
            "repos_affected": [r.full_name for r in repos],
            "tasks_affected": len(tasks),
        }
    )
