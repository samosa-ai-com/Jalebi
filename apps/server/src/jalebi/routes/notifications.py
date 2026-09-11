"""Notification routes: list, unread count, mark read, mark all read."""

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, notifications
from jalebi.db import Task

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")


@bp.get("")
def list_notifications() -> ResponseReturnValue:
    """List notifications with optional unread filtering and bounded limit."""
    unread_only_param = request.args.get("unread_only", "false").lower()
    unread_only = unread_only_param in ("true", "1")

    raw_limit = request.args.get("limit")
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            limit = 50
        limit = max(1, min(limit, 100))
    else:
        limit = 50

    session = db.get_session()
    items = notifications.list_notifications(session, unread_only=unread_only, limit=limit)
    return jsonify(items)


@bp.get("/unread-count")
def get_unread_count() -> ResponseReturnValue:
    """Return the total number of unread notifications."""
    session = db.get_session()
    return jsonify({"unread": notifications.unread_count(session)})


@bp.post("/<notif_id>/read")
def mark_read(notif_id: str) -> ResponseReturnValue:
    """Mark a single notification as read and return the updated row."""
    try:
        nid = int(notif_id)
    except (ValueError, TypeError):
        return jsonify({"error": "notification not found"}), 404

    session = db.get_session()
    row = notifications.mark_read(session, nid)
    if row is None:
        return jsonify({"error": "notification not found"}), 404

    task = session.get(Task, row.task_id) if row.task_id else None
    return jsonify(notifications.notification_to_dict(row, task))


@bp.post("/read-all")
def mark_all_read() -> ResponseReturnValue:
    """Mark all unread notifications as read."""
    session = db.get_session()
    marked = notifications.mark_all_read(session)
    return jsonify({"marked": marked})
