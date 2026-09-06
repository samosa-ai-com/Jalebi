"""Per-task event bus for SSE streaming of agent events.

Phase 4 T4.3 — every published event is ALSO persisted to ``task_events`` so
a tab reload after a server restart can backfill the full timeline via
``TaskEvents.subscribe(after_seq=N)``. The in-memory ring buffer still
serves live fanout (low latency) — ``task_events`` is the durable record.

Standalone ``TaskEvents()`` (no `` db`` argument) is memory-only and is
what unit tests use; ``create_app`` wires ``TaskEvents(db_session_factory)``
so every publish also inserts a ``task_events`` row.
"""

import json
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

BUFFER_SIZE = 500
# Historic per-(task_id, run_id) cap, enforced only by manual prune
# (``prune_task_events``). Nothing calls it automatically: timeline data is
# never auto-deleted — it grows until pruned via Settings → Data management.
PERSIST_CAP = 2000


class TaskEvents:
    """Broadcasts masked agent events to SSE subscribers, keyed by task id."""

    def __init__(
        self,
        db_session_factory: Callable[[], Session] | None = None,
        prune_callback: Callable[[int], None] | None = None,
    ) -> None:
        self._subs: dict[int, list[queue.Queue]] = {}
        self._buffers: dict[int, list[dict[str, Any]]] = {}
        # Per-run seq counter: scoped by run_id (passed via publish) so a
        # brand-new run can restart at 1 without colliding with the prior
        # run's high seq. ``_seq`` is keyed by ``(task_id, run_id)``.
        self._seq: dict[tuple[int, int | None], int] = {}
        self._lock = threading.Lock()
        self._db_session_factory = db_session_factory
        # prune_callback(run_id) is invoked after every persisted publish.
        # Nothing wires one: timeline auto-prune is disabled by design (the
        # owner prunes manually via Settings → Data management).
        self._prune_callback = prune_callback

    def subscribe(
        self,
        task_id: int,
        after_seq: int | None = None,
        run_id: int | None = None,
    ) -> queue.Queue:
        """Register a subscriber; returns a queue that receives event dicts.

        When ``after_seq`` is given, any buffered event with ``seq > after_seq``
        AND ``run_id == run_id`` is replayed first, then live events follow.
        When ``run_id`` is ``None`` (legacy callers), all buffered events with
        ``seq > after_seq`` are replayed (back-compat for tests).
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(task_id, []).append(q)
            # Backfill only when ``after_seq`` is explicitly provided (a
            # reconnect with a known watermark). A fresh subscriber with no
            # watermark gets no replay — matches pre-T4.3 behavior; T4.3 adds
            # a separate replay path (``events.replay_from_db``) for the
            # post-restart backfill, called explicitly by the SSE route.
            if after_seq is not None:
                for item in self._buffers.get(task_id, []):
                    if item.get("seq", 0) <= after_seq:
                        continue
                    if run_id is not None and item.get("run_id") != run_id:
                        continue
                    q.put(item)
        return q

    def unsubscribe(self, task_id: int, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(task_id)
            if not subs:
                return
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                self._subs.pop(task_id, None)

    def reset(self, task_id: int, run_id: int | None = None) -> None:
        """Start a fresh run-scoped seq + replay buffer (called when a new run begins).

        Without this, a stale tab that has seen a high ``seq`` (from an earlier
        run or a previous server process) would treat the new run's low seqs as
        already-seen and silently drop the whole stream. Per-run scoping keeps
        ``seq`` comparable only within one run.
        """
        with self._lock:
            self._buffers.pop(task_id, None)
            self._seq.pop((task_id, run_id), None)

    def publish(
        self,
        task_id: int,
        payload: dict[str, Any],
        run_id: int | None = None,
        session: Session | None = None,
    ) -> None:
        """Publish an event: bump seq, append to buffer, fan out to subscribers.

        When ``self._db_session_factory`` is wired and ``session`` is provided,
        also insert a ``task_events`` row. Persistence happens under the same
        transaction as the caller (no implicit commit). Per-(task,run) cap is
        trimmed via ``_prune_callback`` if available.
        """
        with self._lock:
            key = (task_id, run_id)
            self._seq[key] = self._seq.get(key, 0) + 1
            payload["seq"] = self._seq[key]
            if run_id is not None:
                payload.setdefault("run_id", run_id)
            buf = self._buffers.setdefault(task_id, [])
            buf.append(payload)
            if len(buf) > BUFFER_SIZE:
                del buf[: len(buf) - BUFFER_SIZE]
            for q in list(self._subs.get(task_id, [])):
                q.put(payload)

        # Persistence (Phase 4 T4.3) — outside the lock; uses the caller's session.
        if self._db_session_factory is not None and session is not None:
            try:
                from jalebi.db import TaskEvent  # local import keeps memory-only
                # = tests free of the db dependency

                event = TaskEvent(
                    task_id=task_id,
                    run_id=run_id,
                    seq=payload["seq"],
                    payload_json=json.dumps(payload, default=str),
                )
                session.add(event)
                session.flush()  # surface FK errors early without committing
            except Exception:
                logger.exception(
                    "task_events persistence failed for task %s (event still live)",
                    task_id,
                )
            if self._prune_callback is not None and run_id is not None:
                try:
                    self._prune_callback(run_id)
                except Exception:
                    logger.exception("task_events prune failed for run %s", run_id)

    def close(self, task_id: int) -> None:
        """Signal all subscribers that the run ended (pushes ``None``)."""
        with self._lock:
            subs = self._subs.pop(task_id, [])
        for q in subs:
            q.put(None)


def replay_from_db(
    session: Session, task_id: int, run_id: int | None, after_seq: int
) -> "list":
    """Return persisted events for ``task_id`` / ``run_id`` with ``seq > after_seq``.

    Used by the SSE route after a restart to backfill the in-memory buffer
    from the durable store. Returns the raw ``TaskEvent`` ORM objects
    (callers access ``.payload_json`` / ``.seq`` / ``.run_id``).
    """
    from jalebi.db import TaskEvent

    rows = (
        session.query(TaskEvent)
        .filter(TaskEvent.task_id == task_id)
        .filter(TaskEvent.seq > after_seq)
        .order_by(TaskEvent.seq.asc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        if run_id is not None and r.run_id != run_id:
            continue
        try:
            payload = json.loads(r.payload_json)
        except (ValueError, TypeError):
            continue
        payload.setdefault("seq", r.seq)
        if r.run_id is not None:
            payload.setdefault("run_id", r.run_id)
        out.append(payload)
    return out


def prune_task_events(session: Session, max_rows: int = PERSIST_CAP) -> int:
    """Trim ``task_events`` rows beyond ``max_rows`` per ``(task_id, run_id)``.

    Returns the number of rows deleted. Safe to call from the start-up loop.
    """
    from sqlalchemy import delete, select

    from jalebi.db import TaskEvent

    # Per-(task_id, run_id) keep the newest ``max_rows``.
    deleted = 0
    keys = (
        session.execute(
            select(TaskEvent.task_id, TaskEvent.run_id).group_by(
                TaskEvent.task_id, TaskEvent.run_id
            )
        )
        .all()
    )
    for task_id, run_id in keys:
        ids_to_drop = (
            session.execute(
                select(TaskEvent.id)
                .filter(TaskEvent.task_id == task_id)
                .filter(
                    TaskEvent.run_id == run_id
                    if run_id is not None
                    else TaskEvent.run_id.is_(None)
                )
                .order_by(TaskEvent.seq.desc())
                .offset(max_rows)
                .limit(10000)
            )
            .scalars()
            .all()
        )
        if ids_to_drop:
            session.execute(delete(TaskEvent).where(TaskEvent.id.in_(ids_to_drop)))
            deleted += len(ids_to_drop)
    session.commit()
    return deleted
