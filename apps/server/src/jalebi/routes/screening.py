"""Screening routes: configure + run proactive audits (PRD F10)."""

from __future__ import annotations

import json
import logging
import threading

from flask import Blueprint, Response, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, screening
from jalebi.screening import ScreeningError, run_to_dict, screen_to_dict

logger = logging.getLogger(__name__)

bp = Blueprint("screening", __name__, url_prefix="/api/screenings")

# The built-in starter catalog of screens (PRD F10). Each entry is a template
# the user can instantiate against any connected repo.
STARTER_SCREENS: list[dict[str, str]] = [
    {
        "name": "Security posture",
        "cadence_cron": "0 6 * * 1",
        "system_prompt": (
            "Audit this repository's security posture. Look for vulnerabilities, "
            "secrets or credentials committed, injection risks, authorization "
            "gaps, and dependency risk. Report concrete findings with file/line "
            "references where possible."
        ),
    },
    {
        "name": "Dependency hygiene",
        "cadence_cron": "0 6 * * 2",
        "system_prompt": (
            "Audit dependency hygiene: outdated, abandoned, or misconfigured "
            "dependencies, duplicate versions, and dependency files out of sync "
            "with lockfiles. Report concrete findings."
        ),
    },
    {
        "name": "Dead code & cruft",
        "cadence_cron": "0 6 * * 3",
        "system_prompt": (
            "Audit for dead code and cruft: unused exports or imports, dead "
            "branches, unreachable code, TODO/FIXME density, and stale comments. "
            "Report concrete findings with file/line references."
        ),
    },
    {
        "name": "Test coverage gaps",
        "cadence_cron": "0 6 * * 4",
        "system_prompt": (
            "Audit test coverage gaps: critical paths, error paths, and "
            "utilities without tests. Suggest specific files or modules that "
            "should have tests. Report concrete findings."
        ),
    },
    {
        "name": "Docs drift",
        "cadence_cron": "0 6 * * 5",
        "system_prompt": (
            "Audit docs drift: READMEs, AGENTS.md, and doc comments out of sync "
            "with the code they describe, missing docs for public APIs, and "
            "broken doc references. Report concrete findings."
        ),
    },
    {
        "name": "Performance hotspots",
        "cadence_cron": "0 7 * * 6",
        "system_prompt": (
            "Audit for obvious performance hotspots: N+1 query patterns, heavy "
            "loops over unbounded collections, accidental quadratic work, and "
            "unbounded growth. Report concrete findings with file/line."
        ),
    },
    {
        "name": "Code-quality consistency",
        "cadence_cron": "0 7 * * 0",
        "system_prompt": (
            "Audit code-quality consistency: style inconsistencies across "
            "modules, error-handling inconsistencies, naming drift, and "
            "violations of the repository's own conventions. Report concrete "
            "findings."
        ),
    },
]


def _engine():
    return current_app.config["JALEBI_SCREENING"].engine


def _scheduler():
    return current_app.config["JALEBI_SCREENING"]


@bp.get("/templates")
def templates() -> ResponseReturnValue:
    """The starter catalog of screens the UI can instantiate."""
    return jsonify(STARTER_SCREENS)


@bp.get("")
def list_screens_route() -> ResponseReturnValue:
    session = db.get_session()
    rows = screening.list_screens(session)
    result = []
    for s in rows:
        latest = screening.latest_run(session, s.id)
        result.append({**screen_to_dict(s), "latest_run": run_to_dict(latest) if latest else None})
    return jsonify(result)


@bp.post("")
def create_screen_route() -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    repo_id = payload.get("repo_id")
    if not isinstance(repo_id, int):
        return jsonify({"error": "repo_id is required"}), 400
    session = db.get_session()
    try:
        screen = screening.create_screen(
            session,
            repo_id=repo_id,
            name=str(payload.get("name") or ""),
            system_prompt=str(payload.get("system_prompt") or ""),
            cadence_cron=str(payload.get("cadence_cron") or ""),
            scope_branch=payload.get("scope_branch"),
            enabled=bool(payload.get("enabled", True)),
            notify_ntfy=bool(payload.get("notify_ntfy", True)),
        )
    except ScreeningError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(screen_to_dict(screen)), 201


@bp.get("/<int:screen_id>")
def get_screen_route(screen_id: int) -> ResponseReturnValue:
    session = db.get_session()
    screen = screening.get_screen(session, screen_id)
    if screen is None:
        return jsonify({"error": "screen not found"}), 404
    return jsonify(screen_to_dict(screen))


@bp.put("/<int:screen_id>")
def update_screen_route(screen_id: int) -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    session = db.get_session()
    screen = screening.get_screen(session, screen_id)
    if screen is None:
        return jsonify({"error": "screen not found"}), 404
    try:
        screen = screening.update_screen(
            session,
            screen,
            name=payload.get("name"),
            system_prompt=payload.get("system_prompt"),
            cadence_cron=payload.get("cadence_cron"),
            scope_branch=payload.get("scope_branch"),
            enabled=payload.get("enabled"),
            notify_ntfy=payload.get("notify_ntfy"),
        )
    except ScreeningError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(screen_to_dict(screen))


@bp.delete("/<int:screen_id>")
def delete_screen_route(screen_id: int) -> ResponseReturnValue:
    session = db.get_session()
    if not screening.delete_screen(session, screen_id):
        return jsonify({"error": "screen not found"}), 404
    return jsonify({"ok": True})


@bp.get("/<int:screen_id>/runs")
def list_runs_route(screen_id: int) -> ResponseReturnValue:
    session = db.get_session()
    screen = screening.get_screen(session, screen_id)
    if screen is None:
        return jsonify({"error": "screen not found"}), 404
    runs = screening.list_runs(session, screen_id)
    return jsonify([run_to_dict(r) for r in runs])


@bp.post("/<int:screen_id>/run")
def run_screen_route(screen_id: int) -> ResponseReturnValue:
    """Manually run a screen now (ignores baseline dedup), asynchronously."""
    session = db.get_session()
    screen = screening.get_screen(session, screen_id)
    if screen is None:
        return jsonify({"error": "screen not found"}), 404
    force = bool(request.args.get("force", "1") not in ("0", "false", "False"))
    engine = current_app.config["JALEBI_SCREENING"].engine

    def _background() -> None:
        from jalebi import db as _db

        s = _db.Session()
        try:
            fresh = screening.get_screen(s, screen_id)
            if fresh is None:
                logger.warning("screen %s deleted before its background run started", screen_id)
                return
            engine.run_screen(s, fresh, force=force)
        except ScreeningError as exc:
            logger.warning("screening run %s failed: %s", screen_id, exc)
        finally:
            s.close()

    threading.Thread(target=_background, daemon=True).start()
    return jsonify({"ok": True, "screening_id": screen_id, "force": force})


@bp.get("/runs/<int:run_id>/events")
def run_events(run_id: int) -> ResponseReturnValue:
    """SSE stream of live (masked) events for a screening run."""
    engine = _engine()
    events = engine.events
    after_seq = request.args.get("after_seq", type=int)
    q = events.subscribe(run_id, after_seq=after_seq)

    session = db.get_session()
    run = session.get(db.ScreeningRun, run_id)
    terminal = run is not None and run.status in ("done", "failed")

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            if terminal:
                yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                return
            while True:
                try:
                    item = q.get(timeout=15)
                except Exception:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                    return
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            events.unsubscribe(run_id, q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
