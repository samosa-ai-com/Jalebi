"""In-app persistent notifications service (PRD task-notification backend).

Records notification rows when tasks complete, fail, or need input.
Supports unread-only filtering, count queries, mark-as-read, deduplication,
and bounded table retention (pruning oldest rows beyond the newest 500).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jalebi import clock
from jalebi.db import Notification, Task, now

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

KINDS: tuple[str, ...] = ("task_done", "task_failed", "needs_input")
MAX_NOTIFICATIONS: int = 500


def notification_to_dict(
    notif: Notification,
    task: Task | None = None,
) -> dict[str, object]:
    """Serialize a Notification row to a dictionary.

    Includes task metadata (repo_id, type, status, pr_number) if the task row
    exists, or gracefully omits those keys if the task row is gone.
    """
    d: dict[str, object] = {
        "id": notif.id,
        "task_id": notif.task_id,
        "run_id": notif.run_id,
        "kind": notif.kind,
        "title": notif.title,
        "body": notif.body,
        "read_at": clock.to_iso(notif.read_at) if notif.read_at is not None else None,
        "created_at": clock.to_iso(notif.created_at) if notif.created_at is not None else None,
    }
    if task is not None:
        d["repo_id"] = task.repo_id
        d["type"] = task.type
        d["status"] = task.status
        d["pr_number"] = task.pr_number
    return d


def notify(
    session: Session,
    task_id: int,
    run_id: int | None,
    kind: str,
    title: str,
    body: str | None = None,
) -> Notification | None:
    """Insert a notification row, skipping if an unread row with same (task_id, kind) exists.

    After insertion, prunes table to the newest 500 rows.
    """
    if kind not in KINDS:
        raise ValueError(f"Invalid notification kind: {kind!r}. Expected one of {KINDS}")

    existing = session.execute(
        select(Notification.id).where(
            Notification.task_id == task_id,
            Notification.kind == kind,
            Notification.read_at.is_(None),
        ).limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        return None

    row = Notification(
        task_id=task_id,
        run_id=run_id,
        kind=kind,
        title=title,
        body=body,
        read_at=None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    prune_notifications(session, limit=MAX_NOTIFICATIONS)
    return row


def prune_notifications(session: Session, limit: int = MAX_NOTIFICATIONS) -> int:
    """Prune the notifications table to keep only the newest `limit` rows (delete older ids)."""
    cutoff_id = session.execute(
        select(Notification.id)
        .order_by(Notification.id.desc())
        .offset(limit)
        .limit(1)
    ).scalar_one_or_none()

    if cutoff_id is not None:
        res = session.execute(
            delete(Notification).where(Notification.id <= cutoff_id)
        )
        session.commit()
        return int(res.rowcount or 0)
    return 0


def list_notifications(
    session: Session,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict[str, object]]:
    """List notifications newest-first as dicts."""
    stmt = (
        select(Notification, Task)
        .outerjoin(Task, Notification.task_id == Task.id)
        .order_by(Notification.id.desc())
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)

    rows = session.execute(stmt).all()
    return [notification_to_dict(notif, task) for notif, task in rows]


def unread_count(session: Session) -> int:
    """Return the number of unread notifications."""
    return int(
        session.execute(
            select(sa.func.count(Notification.id)).where(Notification.read_at.is_(None))
        ).scalar()
        or 0
    )


def mark_read(session: Session, notif_id: int) -> Notification | None:
    """Mark a notification as read (setting read_at=now). Returns the row or None if not found."""
    notif = session.get(Notification, notif_id)
    if notif is None:
        return None
    if notif.read_at is None:
        notif.read_at = now()
        session.commit()
        session.refresh(notif)
    return notif


def mark_all_read(session: Session) -> int:
    """Mark all unread notifications as read. Returns the count of rows updated."""
    unread_rows = list(
        session.execute(
            select(Notification).where(Notification.read_at.is_(None))
        ).scalars()
    )
    if not unread_rows:
        return 0
    t = now()
    for row in unread_rows:
        row.read_at = t
    session.commit()
    return len(unread_rows)
