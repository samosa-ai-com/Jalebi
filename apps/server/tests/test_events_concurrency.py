"""Concurrency regression tests for the task-63 SQLite lock crash.

Task 63 died this way: two reviewer runs wrote ``task_events`` concurrently,
one flush hit ``database is locked`` on the worker's long-lived session, and
the poisoned session cascaded (``PendingRollbackError``) through the event
publish, the failure marking, and even the logger call — leaving the run
frozen at ``running`` forever.

These tests pin the structural fix: event persistence runs on a dedicated
short-lived session, so a locked event can never poison a worker.
"""

import threading

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from jalebi.db import Base, Repo, Run, Task, TaskEvent
from jalebi.events import TaskEvents


def _file_db(tmp_path):
    """A throwaway file-backed SQLite DB with the catalog/task tables."""
    engine = create_engine(f"sqlite:///{tmp_path}/conc.db")
    Base.metadata.create_all(engine)
    return engine


def _seed(engine):
    """One repo + task + run; returns (task_id, run_id)."""
    factory = sessionmaker(bind=engine)
    session = factory()
    session.add(Repo(full_name="o/r", default_branch="main", clone_url="x", pat_name="t"))
    session.commit()
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run = Run(task_id=task.id, seq=1, status="running")
    session.add(run)
    session.commit()
    ids = (task.id, run.id)
    session.close()
    return ids


def test_concurrent_publish_threads_lose_nothing(tmp_path) -> None:
    """N threads publishing to one run: no exceptions, complete seqs, all rows."""
    engine = _file_db(tmp_path)
    task_id, run_id = _seed(engine)
    factory = sessionmaker(bind=engine)
    bus = TaskEvents(db_session_factory=factory)

    errors: list[BaseException] = []
    threads = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                bus.publish(task_id, {"type": "message", "text": f"{n}-{i}"}, run_id=run_id)
        except BaseException as exc:  # noqa: BLE001 — collected, asserted below
            errors.append(exc)

    for n in range(4):
        t = threading.Thread(target=worker, args=(n,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    session = factory()
    rows = session.query(TaskEvent).filter_by(task_id=task_id, run_id=run_id).all()
    assert len(rows) == 100
    assert sorted(r.seq for r in rows) == list(range(1, 101))
    session.close()


def test_publish_lock_failure_never_poisons_caller(tmp_path) -> None:
    """A locked persistence write must not raise and must not touch the
    caller's session: the event stays live, the run is untouched."""
    engine = _file_db(tmp_path)
    task_id, run_id = _seed(engine)
    factory = sessionmaker(bind=engine)
    bus = TaskEvents(db_session_factory=factory)

    def locked_factory():
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    bus_locked = TaskEvents(db_session_factory=locked_factory)
    caller = factory()
    run = caller.get(Run, run_id)
    assert run is not None
    run.status = "running"  # pending change on the caller session

    # Must not raise, and the caller session must still commit cleanly.
    bus_locked.publish(task_id, {"type": "message", "text": "hi"}, run_id=run_id)
    caller.commit()

    fresh = factory()
    fresh_run = fresh.get(Run, run_id)
    assert fresh_run is not None and fresh_run.status == "running"
    assert fresh.query(TaskEvent).filter_by(task_id=task_id).count() == 0
    caller.close()
    fresh.close()

    # And a healthy bus still persists normally afterwards.
    bus.publish(task_id, {"type": "message", "text": "ok"}, run_id=run_id)
    check = factory()
    assert check.query(TaskEvent).filter_by(task_id=task_id).count() == 1
    check.close()


def test_publish_without_run_id_stays_memory_only(tmp_path) -> None:
    """Legacy callers without a run_id never touch the DB (no session opened)."""
    engine = _file_db(tmp_path)
    opened: list[bool] = []

    def counting_factory():
        opened.append(True)
        return sessionmaker(bind=engine)()

    bus = TaskEvents(db_session_factory=counting_factory)
    bus.publish(7, {"type": "message", "text": "hi"})
    assert opened == []
    assert bus._buffers[7][0]["seq"] == 1
