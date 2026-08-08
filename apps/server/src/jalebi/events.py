"""Per-task in-process event bus for SSE streaming of live agent events.

Every published event is stamped with a monotonically increasing ``seq`` (per
task) and retained in a small replay buffer, so a subscriber that connects or
reconnects after the fact can backfill anything it missed (``?after_seq=``)
instead of losing the gap between teardown and re-subscribe.
"""

import queue
import threading
from typing import Any

BUFFER_SIZE = 500  # match the persisted steps cap (queue.MAX_STEPS)


class TaskEvents:
    """Broadcasts masked agent events to SSE subscribers, keyed by task id."""

    def __init__(self) -> None:
        self._subs: dict[int, list[queue.Queue]] = {}
        self._buffers: dict[int, list[dict[str, Any]]] = {}
        self._seq: dict[int, int] = {}
        self._lock = threading.Lock()

    def subscribe(self, task_id: int, after_seq: int | None = None) -> queue.Queue:
        """Register a subscriber; returns a queue that receives event dicts.

        When ``after_seq`` is given, any buffered event with ``seq > after_seq``
        is replayed first, then live events follow. A ``None`` item means the run
        ended and the stream should close.
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(task_id, []).append(q)
            if after_seq is not None:
                for item in self._buffers.get(task_id, []):
                    if item.get("seq", 0) > after_seq:
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

    def reset(self, task_id: int) -> None:
        """Start a fresh run-scoped seq + replay buffer (called when a new run begins).

        Without this, a stale tab that has seen a high ``seq`` (from an earlier
        run or a previous server process) would treat the new run's low seqs as
        already-seen and silently drop the whole stream. Per-run scoping keeps
        ``seq`` comparable only within one run.
        """
        with self._lock:
            self._buffers.pop(task_id, None)
            self._seq[task_id] = 0

    def publish(self, task_id: int, payload: dict[str, Any]) -> None:
        with self._lock:
            self._seq[task_id] = self._seq.get(task_id, 0) + 1
            payload["seq"] = self._seq[task_id]
            buf = self._buffers.setdefault(task_id, [])
            buf.append(payload)
            if len(buf) > BUFFER_SIZE:
                del buf[: len(buf) - BUFFER_SIZE]
            for q in list(self._subs.get(task_id, [])):
                q.put(payload)

    def close(self, task_id: int) -> None:
        """Signal all subscribers that the run ended (pushes ``None``)."""
        with self._lock:
            subs = self._subs.pop(task_id, [])
        for q in subs:
            q.put(None)
