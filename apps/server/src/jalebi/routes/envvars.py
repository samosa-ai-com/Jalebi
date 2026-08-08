"""Env-var routes: list/upsert/delete/import (.env parsing), values masked."""

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, envvars
from jalebi.db import Repo

bp = Blueprint("envvars", __name__, url_prefix="/api/envvars")


def _repo_name(session, repo_id: int | None) -> str | None:
    if repo_id is None:
        return None
    repo = session.get(Repo, repo_id)
    return repo.full_name if repo is not None else None


def _list_payload(session, rows) -> list[dict[str, object]]:
    return [
        envvars.env_var_to_dict(r, repo_full_name=_repo_name(session, r.repo_id))
        for r in rows
    ]


@bp.get("")
def list_env_vars() -> ResponseReturnValue:
    session = db.get_session()
    raw = request.args.get("repo_id", type=int)
    rows = envvars.list_env_vars(session, repo_id=raw)
    return jsonify(_list_payload(session, rows))


@bp.post("")
def upsert_env_var() -> ResponseReturnValue:
    session = db.get_session()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    name = payload.get("name")
    value = payload.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        return jsonify({"error": 'expected {"name": "...", "value": "..."}'}), 400
    repo_id = payload.get("repo_id")
    if repo_id is not None and not isinstance(repo_id, int):
        return jsonify({"error": "repo_id must be an integer or null"}), 400
    try:
        row = envvars.upsert_env_var(session, name=name, value=value, repo_id=repo_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        envvars.env_var_to_dict(row, repo_full_name=_repo_name(session, row.repo_id))
    ), 201


@bp.post("/import")
def import_env_file() -> ResponseReturnValue:
    """Parse .env content (KEY=VALUE lines) and upsert each variable."""
    session = db.get_session()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        return jsonify({"error": 'expected {"content": "<.env text>"}'}), 400
    repo_id = payload.get("repo_id")
    if repo_id is not None and not isinstance(repo_id, int):
        return jsonify({"error": "repo_id must be an integer or null"}), 400
    try:
        imported = envvars.import_env_file(session, payload["content"], repo_id=repo_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    rows = envvars.list_env_vars(session, repo_id=repo_id)
    return jsonify({"imported": imported, "env_vars": _list_payload(session, rows)})


@bp.delete("/<int:env_var_id>")
def delete_env_var(env_var_id: int) -> ResponseReturnValue:
    session = db.get_session()
    if not envvars.delete_env_var(session, env_var_id):
        return jsonify({"error": "env var not found"}), 404
    return jsonify({"deleted": env_var_id})
