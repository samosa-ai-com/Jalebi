"""App-data management routes: usage, backups, vacuum, prune (Settings → Data)."""

from flask import Blueprint, current_app, jsonify, request, send_file
from flask.typing import ResponseReturnValue

from jalebi import data_mgmt, db
from jalebi.config import Config

bp = Blueprint("data", __name__, url_prefix="/api/data")


@bp.get("/usage")
def usage() -> ResponseReturnValue:
    """Storage sizes + row counts for the Data management dashboard."""
    config: Config = current_app.config["JALEBI_CONFIG"]
    session = db.get_session()
    return jsonify(data_mgmt.usage(session, config.data_dir))


@bp.get("/backups")
def list_backups() -> ResponseReturnValue:
    config: Config = current_app.config["JALEBI_CONFIG"]
    return jsonify(data_mgmt.list_backups(config.data_dir))


@bp.post("/backups")
def create_backup() -> ResponseReturnValue:
    """Snapshot the live DB (SQLite backup API — safe on a running server)."""
    config: Config = current_app.config["JALEBI_CONFIG"]
    try:
        info = data_mgmt.create_backup(config.data_dir)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"backup failed: {exc}"}), 500
    return jsonify(info), 201


@bp.get("/backups/<name>/download")
def download_backup(name: str) -> ResponseReturnValue:
    config: Config = current_app.config["JALEBI_CONFIG"]
    try:
        path = data_mgmt.backup_file(config.data_dir, name)
    except ValueError:
        return jsonify({"error": "unknown backup"}), 404
    return send_file(str(path), as_attachment=True, download_name=name)


@bp.delete("/backups/<name>")
def delete_backup(name: str) -> ResponseReturnValue:
    config: Config = current_app.config["JALEBI_CONFIG"]
    if not data_mgmt.delete_backup(config.data_dir, name):
        return jsonify({"error": "unknown backup"}), 404
    return jsonify({"deleted": name})


@bp.post("/backups/<name>/restore")
def restore_backup(name: str) -> ResponseReturnValue:
    """Preview (default) or execute a restore from a backup.

    Body: ``{"dry_run": true}`` (default) returns integrity + idle checks
    without writing anything. Execute with ``{"dry_run": false, "confirm":
    "RESTORE"}``: refuses while tasks are queued/running or screening runs
    are queued/running (409) or the backup fails integrity (400); otherwise
    swaps the live DB (safety snapshot first, rollback on failure) and
    re-syncs clock + queue.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    config: Config = current_app.config["JALEBI_CONFIG"]
    session = db.get_session()
    ok, detail = data_mgmt.backup_integrity(config.data_dir, name)
    if not ok and detail == "unknown backup":
        return jsonify({"error": "unknown backup"}), 404
    preview = {
        "backup": name,
        "integrity_ok": ok,
        "integrity_detail": detail,
        "busy_tasks": data_mgmt.busy_task_count(session),
        "busy_screenings": data_mgmt.busy_screening_count(session),
    }
    if payload.get("dry_run", True) or payload.get("confirm") != "RESTORE":
        return jsonify({"dry_run": True, "preview": preview})
    if not ok:
        return jsonify({"error": detail or "backup failed integrity check"}), 400
    try:
        result = data_mgmt.restore_backup(
            config, session, name, queue=current_app.config.get("JALEBI_QUEUE")
        )
    except data_mgmt.RestoreBusy as exc:
        return jsonify({"error": str(exc)}), 409
    except data_mgmt.RestoreError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"dry_run": False, "preview": preview, **result})


@bp.post("/vacuum")
def vacuum() -> ResponseReturnValue:
    """Checkpoint the WAL + rebuild the DB file; returns size before/after.

    409 when a live writer holds the DB (retry when idle); 404 when there is
    no database yet.
    """
    config: Config = current_app.config["JALEBI_CONFIG"]
    try:
        return jsonify(data_mgmt.vacuum(config.data_dir))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except data_mgmt.VacuumBusy as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": f"vacuum failed: {exc}"}), 500


@bp.post("/prune")
def prune() -> ResponseReturnValue:
    """Preview (``{"dry_run": true}``) or execute a prune.

    Body: ``{"older_than_days": 30, "scopes": ["tasks","orphans","deliveries","logs"],
    "dry_run": true, "confirm": "DELETE"}``. Execution requires
    ``dry_run: false`` + ``confirm: "DELETE"``; anything else returns the
    preview. Execution never touches queued/running/blocked tasks.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    older_than_days = payload.get("older_than_days", 30)
    if not isinstance(older_than_days, int) or older_than_days < 1:
        return jsonify({"error": "`older_than_days` must be an int >= 1"}), 400
    scopes = payload.get("scopes", ["tasks", "orphans", "deliveries", "logs"])
    if not isinstance(scopes, list) or not scopes or any(
        s not in data_mgmt.VALID_PRUNE_SCOPES for s in scopes
    ):
        return (
            jsonify(
                {"error": f"`scopes` must be a non-empty subset of {data_mgmt.VALID_PRUNE_SCOPES}"}
            ),
            400,
        )
    config: Config = current_app.config["JALEBI_CONFIG"]
    session = db.get_session()
    preview = data_mgmt.prune_preview(session, config.data_dir, older_than_days)
    if payload.get("dry_run", True) or payload.get("confirm") != "DELETE":
        return jsonify({"dry_run": True, "preview": preview})
    removed = data_mgmt.prune_execute(
        session, config.data_dir, older_than_days, list(scopes), config=config
    )
    return jsonify({"dry_run": False, "removed": removed, "preview": preview})
