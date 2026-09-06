"""Phase 4 T4.3 — durable SSE timeline (task_events table)."""

import json

from jalebi.db import TaskEvent
from jalebi.events import TaskEvents, prune_task_events, replay_from_db


def test_publish_persists_task_events_row(session) -> None:
    """A publish with a session arg inserts a task_events row (Phase 4 T4.3)."""
    from jalebi import repos
    from jalebi.db import Run, Task

    # Need a real task + run for the FK.
    repos.upsert_repo(
        session,
        full_name="owner/r",
        default_branch="main",
        clone_url="https://x/r.git",
        pat_name="test",
    )
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run = Run(task_id=task.id, seq=1, status="running")
    session.add(run)
    session.commit()

    bus = TaskEvents(db_session_factory=lambda: session)
    bus.publish(
        task.id,
        {"type": "message", "text": "hello"},
        run_id=run.id,
        session=session,
    )
    session.commit()
    rows = session.query(TaskEvent).filter_by(task_id=task.id).all()
    assert len(rows) == 1
    payload = json.loads(rows[0].payload_json)
    assert payload["text"] == "hello"
    assert payload["seq"] == 1
    assert rows[0].run_id == run.id


def test_publish_without_session_does_not_persist(session) -> None:
    """Standalone (no session) → memory-only — no task_events row is written."""
    bus = TaskEvents(db_session_factory=lambda: session)  # wired but unused
    bus.publish(7, {"type": "message", "text": "hi"})
    rows = session.query(TaskEvent).filter_by(task_id=7).all()
    assert rows == []


def test_replay_from_db_after_fresh_instance(session) -> None:
    """After a restart, ``replay_from_db`` returns the persisted events."""
    from jalebi import repos
    from jalebi.db import Run, Task

    repos.upsert_repo(
        session,
        full_name="owner/r2",
        default_branch="main",
        clone_url="https://x/r2.git",
        pat_name="test",
    )
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run = Run(task_id=task.id, seq=1, status="running")
    session.add(run)
    session.commit()

    bus = TaskEvents(db_session_factory=lambda: session)
    for i in range(3):
        bus.publish(
            task.id,
            {"type": "message", "text": str(i)},
            run_id=run.id,
            session=session,
        )
    session.commit()

    # Simulate restart: instantiate a fresh in-memory bus (no db wired).
    fresh = TaskEvents()
    replayed = replay_from_db(
        session, task_id=task.id, run_id=run.id, after_seq=0
    )
    texts = [r["text"] for r in replayed]
    assert texts == ["0", "1", "2"]
    # Default fanout: fresh bus has no buffer.
    q = fresh.subscribe(task.id)
    assert q.empty()


def test_replay_filters_by_run_id(session) -> None:
    from jalebi import repos
    from jalebi.db import Run, Task

    repos.upsert_repo(
        session,
        full_name="owner/r3",
        default_branch="main",
        clone_url="https://x/r3.git",
        pat_name="test",
    )
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run1 = Run(task_id=task.id, seq=1, status="done")
    session.add(run1)
    session.commit()
    run2 = Run(task_id=task.id, seq=2, status="done")
    session.add(run2)
    session.commit()

    bus = TaskEvents(db_session_factory=lambda: session)
    bus.publish(task.id, {"type": "message", "text": "a"}, run_id=run1.id, session=session)
    bus.publish(task.id, {"type": "message", "text": "b"}, run_id=run2.id, session=session)
    session.commit()
    replayed = replay_from_db(session, task_id=task.id, run_id=run2.id, after_seq=0)
    texts = [r["text"] for r in replayed]
    assert texts == ["b"]


def test_replay_respects_after_seq(session) -> None:
    from jalebi import repos
    from jalebi.db import Run, Task

    repos.upsert_repo(
        session,
        full_name="owner/r4",
        default_branch="main",
        clone_url="https://x/r4.git",
        pat_name="test",
    )
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run = Run(task_id=task.id, seq=1, status="done")
    session.add(run)
    session.commit()

    bus = TaskEvents(db_session_factory=lambda: session)
    for i in range(3):
        bus.publish(
            task.id,
            {"type": "message", "text": str(i)},
            run_id=run.id,
            session=session,
        )
    session.commit()
    replayed = replay_from_db(session, task_id=task.id, run_id=run.id, after_seq=1)
    texts = [r["text"] for r in replayed]
    assert texts == ["1", "2"]


def test_prune_task_events_caps_rows(session) -> None:
    from jalebi import repos
    from jalebi.db import Run, Task

    repos.upsert_repo(
        session,
        full_name="owner/r5",
        default_branch="main",
        clone_url="https://x/r5.git",
        pat_name="test",
    )
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run = Run(task_id=task.id, seq=1, status="done")
    session.add(run)
    session.commit()

    bus = TaskEvents(db_session_factory=lambda: session)
    for i in range(5):
        bus.publish(
            task.id,
            {"type": "message", "text": str(i)},
            run_id=run.id,
            session=session,
        )
    session.commit()
    deleted = prune_task_events(session, max_rows=3)
    assert deleted == 2
    remaining = session.query(TaskEvent).count()
    assert remaining == 3


def test_reset_scopes_seq_per_run(session) -> None:
    bus = TaskEvents()
    bus.publish(7, {"type": "message", "text": "first-run"}, run_id=1)
    bus.publish(7, {"type": "message", "text": "first-run-2"}, run_id=1)
    assert bus._seq[(7, 1)] == 2
    # Reset for a new run on the same task.
    bus.reset(7, run_id=1)
    assert (7, 1) not in bus._seq
    bus.publish(7, {"type": "message", "text": "second-run"}, run_id=2)
    assert bus._seq[(7, 2)] == 1


def test_no_automatic_event_prune(session, config) -> None:
    """Timeline data is never auto-deleted: publishing never trims the table."""
    from jalebi import db as db_mod
    from jalebi import repos
    from jalebi.db import Run, Task
    from jalebi.queue import TaskQueue

    repos.upsert_repo(
        session,
        full_name="owner/r6",
        default_branch="main",
        clone_url="https://x/r6.git",
        pat_name="test",
    )
    task = Task(type="freeform", repo_id=1, prompt="x")
    session.add(task)
    session.commit()
    run = Run(task_id=task.id, seq=1, status="running")
    session.add(run)
    session.commit()

    q = TaskQueue(config, db_session_factory=db_mod.get_session)
    assert q.events._prune_callback is None
    for i in range(5):
        q.events.publish(
            task.id,
            {"type": "message", "text": str(i)},
            run_id=run.id,
            session=session,
        )
    session.commit()
    assert session.query(TaskEvent).filter_by(task_id=task.id).count() == 5
