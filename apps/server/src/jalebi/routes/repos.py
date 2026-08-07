"""Connected-repository routes: list, connect (upsert), disconnect, prune, branches."""

import httpx
from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, repos, secrets
from jalebi.config import Config
from jalebi.git_workspace import GitWorkspace
from jalebi.github import GitHubClient, GitHubError, GitHubNotFound

bp = Blueprint("repos", __name__, url_prefix="/api/repos")


@bp.get("")
def list_repos() -> ResponseReturnValue:
    session = db.get_session()
    return jsonify([repos.repo_to_dict(row) for row in repos.list_repos(session)])


@bp.post("")
def connect_repo() -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    full_name = payload.get("full_name") if isinstance(payload, dict) else None
    if not full_name or "/" not in full_name:
        return jsonify({"error": 'expected JSON body {"full_name": "<owner/repo>"}'}), 400

    config: Config = current_app.config["JALEBI_CONFIG"]
    pat_name = payload.get("pat_name") if isinstance(payload, dict) else None
    known = secrets.token_names(config)
    if pat_name is not None and pat_name != "default" and pat_name not in known:
        return jsonify({"error": f"unknown account: {pat_name}"}), 400
    token = secrets.resolve_token(config, None if pat_name in (None, "default") else pat_name)
    if not token:
        return jsonify({"error": "no GitHub token configured"}), 409

    client = GitHubClient(token)
    try:
        info = client.get_repo(full_name)
    except GitHubNotFound:
        return jsonify({"error": f"repo not found: {full_name}"}), 404
    except (httpx.HTTPError, GitHubError) as exc:
        return jsonify({"error": str(exc)}), 502
    finally:
        client.close()

    session = db.get_session()
    row, created = repos.upsert_repo(
        session,
        full_name=info["full_name"],
        default_branch=info["default_branch"] or "main",
        clone_url=info["clone_url"],
        pat_name=None if pat_name in (None, "default") else pat_name,
    )
    return jsonify(repos.repo_to_dict(row)), (201 if created else 200)


@bp.delete("/<int:repo_id>")
def disconnect_repo(repo_id: int) -> ResponseReturnValue:
    """Disconnect a repo: hide it from the UI (soft — history stays intact)."""
    session = db.get_session()
    row = session.get(db.Repo, repo_id)
    if row is None:
        return jsonify({"error": "repo not found"}), 404
    row.connected = False
    session.commit()
    return jsonify({"disconnected": row.full_name})


@bp.post("/<int:repo_id>/reconnect")
def reconnect_repo(repo_id: int) -> ResponseReturnValue:
    """Reconnect a previously disconnected repo (requires it to still exist)."""
    config: Config = current_app.config["JALEBI_CONFIG"]
    token = secrets.resolve_token(config, None)
    if not token:
        return jsonify({"error": "no GitHub token configured"}), 409

    session = db.get_session()
    row = session.get(db.Repo, repo_id)
    if row is None:
        return jsonify({"error": "repo not found"}), 404

    client = GitHubClient(token)
    try:
        info = client.get_repo(row.full_name)
    except GitHubNotFound:
        return jsonify({"error": f"repo not found on GitHub: {row.full_name}"}), 404
    except (httpx.HTTPError, GitHubError) as exc:
        return jsonify({"error": str(exc)}), 502
    finally:
        client.close()

    row.default_branch = info["default_branch"] or row.default_branch
    row.clone_url = info["clone_url"]
    row.connected = True
    session.commit()
    return jsonify(repos.repo_to_dict(row))


@bp.post("/prune")
def prune_repos() -> ResponseReturnValue:
    """Soft-remove connected repos that no longer exist on GitHub (deleted upstream)."""
    config: Config = current_app.config["JALEBI_CONFIG"]
    if secrets.resolve_token(config, None) is None and not secrets.token_names(config):
        return jsonify({"error": "no GitHub token configured"}), 409

    session = db.get_session()
    removed: list[str] = []
    client_by_token: dict[str, GitHubClient] = {}
    try:
        for row in repos.list_repos(session, connected_only=True):
            token = secrets.resolve_token(config, row.pat_name)
            if not token:
                continue
            client = client_by_token.get(token)
            if client is None:
                client = GitHubClient(token)
                client_by_token[token] = client
            try:
                client.get_repo(row.full_name)
            except GitHubNotFound:
                removed.append(row.full_name)
                row.connected = False
    except (httpx.HTTPError, GitHubError) as exc:
        return jsonify({"error": str(exc)}), 502
    finally:
        for client in client_by_token.values():
            client.close()
    if removed:
        session.commit()
    return jsonify({"removed": removed})


@bp.get("/<int:repo_id>/branches")
def branches(repo_id: int) -> ResponseReturnValue:
    """Branch names for a connected repo (from its mirror)."""
    session = db.get_session()
    row = session.get(db.Repo, repo_id)
    if row is None:
        return jsonify({"error": "repo not found"}), 404
    config = current_app.config["JALEBI_CONFIG"]
    git = GitWorkspace(config)
    try:
        names = git.list_branches(row.full_name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"full_name": row.full_name, "branches": names})
