"""Per-task in-process event bus for SSE streaming of live agent events."""

import queue
import threading
from typing import Any


class TaskEvents:
    """Broadcasts masked agent events to SSE subscribers, keyed by task id."""

    def __init__(self) -> None:
        self._subs: dict[int, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, task_id: int) -> queue.Queue:
        """Register a subscriber; returns a queue that receives event dicts.

        A ``None`` item means the run ended and the stream should close.
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(task_id, []).append(q)
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

    def publish(self, task_id: int, payload: dict[str, Any]) -> None:
        with self._lock:
            for q in list(self._subs.get(task_id, [])):
                q.put(payload)

    def close(self, task_id: int) -> None:
        """Signal all subscribers that the run ended (pushes ``None``)."""
        with self._lock:
            subs = self._subs.pop(task_id, [])
        for q in subs:
            q.put(None)
