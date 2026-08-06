"""Connected-repository routes: list and connect (upsert) repos."""

import httpx
from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, repos, secrets
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

    config = current_app.config["JALEBI_CONFIG"]
    token = secrets.load_github_token(config)
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
    )
    return jsonify(repos.repo_to_dict(row)), (201 if created else 200)
