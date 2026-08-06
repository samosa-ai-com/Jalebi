"""GitHub integration routes: token status, token provisioning, repo listing."""

from dataclasses import asdict

import httpx
from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import secrets
from jalebi.github import GitHubClient, GitHubError, TokenInfo

bp = Blueprint("github", __name__, url_prefix="/api/github")


def _token() -> str | None:
    config = current_app.config["JALEBI_CONFIG"]
    return secrets.load_github_token(config)


def _client(token: str) -> GitHubClient:
    return GitHubClient(token)


@bp.get("/status")
def status() -> ResponseReturnValue:
    token = _token()
    if not token:
        return jsonify({"valid": False, "error": "no GitHub token configured"}), 409
    client = _client(token)
    try:
        info: TokenInfo = client.validate_token()
    except httpx.HTTPError as exc:
        return jsonify({"valid": False, "error": f"GitHub unreachable: {exc}"}), 502
    finally:
        client.close()
    return jsonify(asdict(info))


@bp.put("/token")
def set_token() -> ResponseReturnValue:
    config = current_app.config["JALEBI_CONFIG"]
    payload = request.get_json(silent=True)
    token = payload.get("token") if isinstance(payload, dict) else None
    if not token:
        return jsonify({"error": 'expected JSON body {"token": "<PAT>"}'}), 400

    client = _client(token)
    try:
        info = client.validate_token()
    except httpx.HTTPError as exc:
        return jsonify({"valid": False, "error": f"GitHub unreachable: {exc}"}), 502
    finally:
        client.close()

    if not info.valid:
        return jsonify({"stored": False, "detail": asdict(info)}), 400

    secrets.store_secret(config, secrets.GITHUB_TOKEN_KEY, token)
    return jsonify({"stored": True, "detail": asdict(info)})


@bp.get("/repos")
def repos() -> ResponseReturnValue:
    token = _token()
    if not token:
        return jsonify({"error": "no GitHub token configured"}), 409
    client = _client(token)
    try:
        items = client.list_repos()
    except (httpx.HTTPError, GitHubError) as exc:
        return jsonify({"error": str(exc)}), 502
    finally:
        client.close()
    return jsonify(items)
