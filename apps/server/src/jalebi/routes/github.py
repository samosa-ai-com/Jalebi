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


@bp.get("/context")
def context() -> ResponseReturnValue:
    """Return open issues, open PRs and branches for a repo (task-form pickers)."""
    full_name = request.args.get("repo")
    if not full_name or "/" not in full_name:
        return jsonify({"error": 'expected ?repo=owner/name'}), 400
    token = _token()
    if not token:
        return jsonify({"error": "no GitHub token configured"}), 409
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
    """List the named PATs (masked, for the UI dropdowns). Never returns values."""
    config = current_app.config["JALEBI_CONFIG"]
    tokens = secrets.list_github_tokens(config)
    has_legacy = (
        secrets.load_secret(config, secrets.GITHUB_TOKEN_KEY) is not None
        or __import__("os").environ.get(secrets.ENV_GITHUB_TOKEN) is not None
    )
    default = None if has_legacy else (tokens[0]["name"] if tokens else None)
    return jsonify(
        {
            "default": default,
            "items": [{"name": t["name"], "masked": _mask_token(t["token"])} for t in tokens],
        }
    )


@bp.post("/tokens")
def add_token() -> ResponseReturnValue:
    """Add a named PAT (validated first). Body: {name, token}."""
    config = current_app.config["JALEBI_CONFIG"]
    payload = request.get_json(silent=True)
    name = payload.get("name") if isinstance(payload, dict) else None
    token = payload.get("token") if isinstance(payload, dict) else None
    if not name or not name.strip():
        return jsonify({"error": 'expected {"name": "<label>", "token": "<PAT>"}'}), 400
    if not token:
        return jsonify({"error": 'expected {"name": "<label>", "token": "<PAT>"}'}), 400
    name = name.strip()

    client = _client(token)
    try:
        info = client.validate_token()
    except httpx.HTTPError as exc:
        return jsonify({"valid": False, "error": f"GitHub unreachable: {exc}"}), 502
    finally:
        client.close()
    if not info.valid:
        return jsonify({"stored": False, "detail": asdict(info)}), 400

    secrets.add_github_token(config, name, token)
    return jsonify({"stored": True, "name": name, "detail": asdict(info)})


@bp.delete("/tokens/<name>")
def delete_token(name: str) -> ResponseReturnValue:
    config = current_app.config["JALEBI_CONFIG"]
    if name not in secrets.token_names(config):
        return jsonify({"error": f"no such token: {name}"}), 404
    secrets.remove_github_token(config, name)
    return jsonify({"removed": name})


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}…{token[-4:]}"
