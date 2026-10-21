"""Project board sync routes: central Projects V2 board status + manual sync-now.

Global board configuration (URL, board account, on/off) lives in the standard
``/api/settings`` keys ``project_sync_enabled`` / ``project_url`` /
``project_pat_name`` — this blueprint only exposes sync *actions* so there is
no second settings surface.
"""

from __future__ import annotations

import logging

import httpx
from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue
from sqlalchemy import func, select

from jalebi import db, masking, secrets
from jalebi.db import ProjectItem, Repo
from jalebi.github import GitHubError
from jalebi.project_sync import ProjectSyncError, board_config

logger = logging.getLogger(__name__)

bp = Blueprint("project_sync", __name__, url_prefix="/api/project-sync")


def _config():
    return current_app.config["JALEBI_CONFIG"]


def _masker(session) -> object:
    from jalebi import settings

    raw = settings.get_setting(session, "secret_patterns") or []
    patterns = [str(p) for p in raw if isinstance(p, str)] if isinstance(raw, list) else []
    return masking.build_masker(secrets.all_token_values(_config()), patterns)


@bp.get("/status")
def status() -> ResponseReturnValue:
    """Board sync status: config state, resolved board id, per-repo counts."""
    session = db.get_session()
    cfg = board_config(session, _config())
    total_items = session.execute(select(func.count(ProjectItem.id))).scalar() or 0
    synced_repos = (
        session.execute(
            select(func.count(Repo.id)).where(
                Repo.connected.is_(True), Repo.project_sync_enabled.is_(True)
            )
        ).scalar()
        or 0
    )
    out: dict[str, object] = {
        "configured": cfg is not None,
        "url": cfg["url"] if cfg else None,
        "pat_name": cfg["pat_name"] if cfg else None,
        "synced_repos": synced_repos,
        "total_items": total_items,
        "project_id": None,
        "error": None,
    }
    if cfg is not None:
        try:
            engine = current_app.config["JALEBI_PROJECT_SYNC"].engine
            out["project_id"] = engine.project_id(session)
        except (GitHubError, ProjectSyncError, httpx.HTTPError) as exc:
            out["error"] = _masker(session)(str(exc))[:500]
    return jsonify(out)


@bp.post("/sync-now")
def sync_now() -> ResponseReturnValue:
    """Manually sync one repo now (``{"repo_id": N, "dry_run": true?}``).

    ``dry_run`` reports what *would* be added without touching GitHub or the
    watermark table — the safe way to preview before enabling the scheduler.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("repo_id"), int):
        return jsonify({"error": "expected a JSON object with integer repo_id"}), 400
    dry_run = payload.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return jsonify({"error": "dry_run must be a boolean"}), 400
    session = db.get_session()
    repo = session.get(Repo, payload["repo_id"])
    if repo is None:
        return jsonify({"error": "repo not found"}), 404
    engine = current_app.config["JALEBI_PROJECT_SYNC"].engine
    try:
        return jsonify(engine.sync_repo(session, repo, dry_run=dry_run))
    except ProjectSyncError as exc:
        return jsonify({"error": _masker(session)(str(exc))[:500]}), 409
    except (GitHubError, httpx.HTTPError) as exc:
        return jsonify({"error": _masker(session)(str(exc))[:500]}), 502
