"""Phase 4 T4.1 — task dependency graph."""

import pytest
from flask.testing import FlaskClient

from jalebi import tasks as tasks_svc
from jalebi.db import Task, TaskDependency


def _make_task(session, app, status: str = "queued") -> Task:
    from jalebi import repos

    repos.upsert_repo(
        session,
        full_name=f"owner/r{app.config['JALEBI_CONFIG'].data_dir.name}-{status}",
        default_branch="main",
        clone_url="https://example.invalid/r.git",
        pat_name="test",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=1, prompt="x"
    )
    task.status = status
    session.commit()
    return task


def test_add_dependency_persists_pair(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app)
    tasks_svc.add_dependency(session, a.id, b.id)
    session.commit()
    rows = (
        session.query(TaskDependency)
        .filter_by(task_id=a.id, depends_on_id=b.id)
        .all()
    )
    assert len(rows) == 1


def test_add_dependency_self_ref_rejected(session, app) -> None:
    a = _make_task(session, app)
    with pytest.raises(ValueError, match="cannot depend on itself"):
        tasks_svc.add_dependency(session, a.id, a.id)


def test_add_dependency_direct_cycle_rejected(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app)
    # b depends on a already, so adding a depends on b is a direct cycle.
    tasks_svc.add_dependency(session, b.id, a.id)
    session.commit()
    with pytest.raises(ValueError, match="cycle"):
        tasks_svc.add_dependency(session, a.id, b.id)


def test_add_dependency_transitive_cycle_rejected(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app)
    c = _make_task(session, app)
    # a -> b -> c; adding c -> a must cycle-detect.
    tasks_svc.add_dependency(session, a.id, b.id)
    tasks_svc.add_dependency(session, b.id, c.id)
    session.commit()
    with pytest.raises(ValueError, match="cycle"):
        tasks_svc.add_dependency(session, c.id, a.id)


def test_has_unmet_deps_false_when_all_satisfied(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app, status="done")
    tasks_svc.add_dependency(session, a.id, b.id)
    session.commit()
    assert tasks_svc.has_unmet_deps(session, a.id) is False


def test_has_unmet_deps_true_when_unfinished(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app)  # queued
    tasks_svc.add_dependency(session, a.id, b.id)
    session.commit()
    assert tasks_svc.has_unmet_deps(session, a.id) is True


def test_dependencies_and_dependents_for(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app)
    c = _make_task(session, app)
    tasks_svc.add_dependency(session, a.id, b.id)
    tasks_svc.add_dependency(session, c.id, b.id)
    session.commit()
    assert tasks_svc.dependencies_for(session, a.id) == [b.id]
    assert sorted(tasks_svc.dependents_for(session, b.id)) == sorted([a.id, c.id])


def test_dep_dict_exposes_four_fields(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app, status="done")
    tasks_svc.add_dependency(session, a.id, b.id)
    session.commit()
    d = tasks_svc.dep_dict(session, a.id)
    assert set(d.keys()) == {"depends_on", "blocked_by", "blocking", "blocked"}
    assert d["depends_on"] == [b.id]
    assert d["blocked_by"] == []  # b is done → not blocking
    assert d["blocking"] == []  # no one depends on `a`
    assert d["blocked"] is False


def test_cascade_unblock_flips_blocked_to_queued(session, app, monkeypatch) -> None:
    """When a dependency's status flips to ``done``, a blocked dependent
    becomes queued + gets enqueued for run."""
    a = _make_task(session, app, status="done")
    b = _make_task(session, app, status="blocked")
    tasks_svc.add_dependency(session, b.id, a.id)
    session.commit()

    enqueued: list[int] = []

    class _FakeQ:
        def enqueue(self, task_id: int) -> None:
            enqueued.append(task_id)

    q = _FakeQ()
    monkeypatch.setattr(tasks_svc, "_cascade_unblock_uses_queue", q) if False else None

    # Call cascade via queue helper directly (no Queue instance in tests).
    from jalebi.queue import TaskQueue

    queue = TaskQueue.__new__(TaskQueue)
    queue.enqueue = q.enqueue  # type: ignore[assignment]
    queue._cascade_unblock(session, a.id)
    session.refresh(b)
    assert b.status == "queued"
    assert enqueued == [b.id]


def test_delete_tasks_cascade_drops_dep_edges(session, app) -> None:
    a = _make_task(session, app)
    b = _make_task(session, app)
    # Two independent edges that both touch `a` (so deleting `a` must drop
    # both). Use a third task `c` to avoid a cycle in the second edge.
    c = _make_task(session, app)
    tasks_svc.add_dependency(session, a.id, b.id)
    tasks_svc.add_dependency(session, c.id, a.id)
    session.commit()
    # Delete tasks should drop both directions of any edges touching them.
    tasks_svc.delete_tasks_cascade(session, [a.id])
    rows = session.query(TaskDependency).all()
    assert rows == []


def test_rerun_blocked_task_returns_409(client: FlaskClient, session, app) -> None:
    a = _make_task(session, app, status="blocked")
    b = _make_task(session, app)  # queued → unmet dep
    tasks_svc.add_dependency(session, a.id, b.id)
    session.commit()
    resp = client.post(f"/api/tasks/{a.id}/rerun")
    assert resp.status_code == 409
    assert "blocked" in resp.get_json()["error"].lower()


def test_dep_route_post_and_delete(client: FlaskClient, session, app) -> None:
    a = _make_task(session, app, status="queued")
    b = _make_task(session, app, status="done")
    # POST a new dep.
    resp = client.post(
        f"/api/tasks/{a.id}/dependencies", json={"depends_on_id": b.id}
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert b.id in body["depends_on"]
    # b is done, so blocked_by should be empty (not blocking).
    assert body["blocked_by"] == []
    assert body["blocked"] is False

    # DELETE it.
    resp = client.delete(f"/api/tasks/{a.id}/dependencies/{b.id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["depends_on"] == []


def test_dep_route_404_for_unknown_task(client: FlaskClient) -> None:
    resp = client.post("/api/tasks/9999/dependencies", json={"depends_on_id": 1})
    assert resp.status_code == 404


def test_dep_route_400_for_self_ref(client: FlaskClient, session, app) -> None:
    a = _make_task(session, app)
    resp = client.post(
        f"/api/tasks/{a.id}/dependencies", json={"depends_on_id": a.id}
    )
    assert resp.status_code == 400
    assert "self" in resp.get_json()["error"].lower()
