import pytest
from flask.testing import FlaskClient

from jalebi import repos, secrets


@pytest.fixture
def repo_id(session) -> int:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
    )
    return row.id


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)


def test_create_task(client: FlaskClient, repo_id: int) -> None:
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "implement x", "model": "m1"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["prompt"] == "implement x"
    assert body["model"] == "m1"
    assert body["timeout_minutes"] == 30


def test_create_task_requires_repo(client: FlaskClient) -> None:
    resp = client.post("/api/tasks", json={"prompt": "x"})
    assert resp.status_code == 400


def test_create_task_unknown_repo(client: FlaskClient) -> None:
    resp = client.post("/api/tasks", json={"repo_id": 999, "prompt": "x"})
    assert resp.status_code == 400


def test_create_task_disconnected_repo_rejected(client: FlaskClient, session) -> None:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/disc",
        default_branch="main",
        clone_url="https://github.com/owner/disc.git",
    )
    client.delete(f"/api/repos/{row.id}")  # soft-disconnect
    resp = client.post("/api/tasks", json={"repo_id": row.id, "prompt": "x"})
    assert resp.status_code == 400
    assert "disconnected" in resp.get_json()["error"]


def test_create_task_empty_prompt(client: FlaskClient, repo_id: int) -> None:
    resp = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "  "})
    assert resp.status_code == 400


def test_create_task_invalid_type(client: FlaskClient, repo_id: int) -> None:
    resp = client.post("/api/tasks", json={"repo_id": repo_id, "type": "bogus", "prompt": "x"})
    assert resp.status_code == 400


def test_create_task_bad_timeout(client: FlaskClient, repo_id: int) -> None:
    resp = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x", "timeout_minutes": "ten"}
    )
    assert resp.status_code == 201
    assert resp.get_json()["timeout_minutes"] == 30


def test_list_and_detail(client: FlaskClient, repo_id: int) -> None:
    client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "first"})
    client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "second"})

    listed = client.get("/api/tasks").get_json()
    assert len(listed) == 2
    task_id = listed[0]["id"]
    detail = client.get(f"/api/tasks/{task_id}").get_json()
    assert detail["status"] == "queued"
    assert detail["run"] is None


def test_detail_not_found(client: FlaskClient) -> None:
    assert client.get("/api/tasks/999").status_code == 404


def test_cancel_queued(client: FlaskClient, repo_id: int) -> None:
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    resp = client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"


def test_cancel_terminal_conflict(client: FlaskClient, repo_id: int) -> None:
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    client.post(f"/api/tasks/{task_id}/cancel")
    resp = client.post(f"/api/tasks/{task_id}/cancel")
    assert resp.status_code == 409


def test_rerun(client: FlaskClient, repo_id: int) -> None:
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    client.post(f"/api/tasks/{task_id}/cancel")
    resp = client.post(f"/api/tasks/{task_id}/rerun")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "queued"
    # Manual reruns do not consume the auto-retry budget.
    assert body["retry_count"] == 0


def test_rerun_running_conflict(client: FlaskClient, repo_id: int) -> None:
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    resp = client.post(f"/api/tasks/{task_id}/rerun")
    assert resp.status_code == 409


def test_publish_not_found(client: FlaskClient) -> None:
    assert client.post("/api/tasks/999/publish").status_code == 404


def test_create_task_publish_mode_defaults_by_type(
    app, client: FlaskClient, repo_id: int, monkeypatch
) -> None:
    """issue_fix auto-publishes on done; freeform defaults to manual publish."""
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "acct-a", "ghp_a")

    class FakeClient:
        def __init__(self, token): ...
        def get_issue(self, full_name, number):
            return {"number": number, "title": "t", "body": "b", "html_url": "u"}
        def close(self): ...

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeClient)
    fix = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "issue_fix",
            "prompt": "fix",
            "issue_number": 1,
            "pat_name": "acct-a",
        },
    )
    assert fix.status_code == 201
    assert fix.get_json()["publish_mode"] == "auto"

    ff = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "explore"},
    )
    assert ff.status_code == 201
    assert ff.get_json()["publish_mode"] == "manual"


def test_create_task_publish_mode_override(client: FlaskClient, repo_id: int) -> None:
    """An explicit publish_mode always wins over the type default."""
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x", "publish_mode": "auto"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["publish_mode"] == "auto"


def test_create_task_rejects_huge_prompt(client: FlaskClient, repo_id: int) -> None:
    """Prompts travel via argv; an oversized one must be refused, not truncated."""
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x" * 40_000},
    )
    assert resp.status_code == 400
    assert "too long" in resp.get_json()["error"]


def test_delete_task(client: FlaskClient, repo_id: int) -> None:
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == task_id
    assert client.get(f"/api/tasks/{task_id}").status_code == 404
    # Deleting twice is a clean 404.
    assert client.delete(f"/api/tasks/{task_id}").status_code == 404


def test_create_task_inherits_repo_pat(app, client, session) -> None:
    from jalebi import repos, secrets

    secrets.add_github_token(app.config["JALEBI_CONFIG"], "acct-b", "ghp_b")
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="acct-b",
    )
    resp = client.post(
        "/api/tasks",
        json={"repo_id": row.id, "type": "freeform", "prompt": "do it"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["pat_name"] == "acct-b"


def test_create_task_explicit_pat_overrides_repo(app, client, session) -> None:
    from jalebi import repos, secrets

    secrets.add_github_token(app.config["JALEBI_CONFIG"], "acct-b", "ghp_b")
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "acct-c", "ghp_c")
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="acct-b",
    )
    resp = client.post(
        "/api/tasks",
        json={"repo_id": row.id, "type": "freeform", "prompt": "do it", "pat_name": "acct-c"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["pat_name"] == "acct-c"


def test_create_task_accepts_default_pat(app, client, session) -> None:
    from jalebi import repos

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
    )
    resp = client.post(
        "/api/tasks",
        json={"repo_id": row.id, "type": "freeform", "prompt": "do it", "pat_name": "default"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["pat_name"] == "default"


def test_followup_accepts_default_pat(app, client, session, monkeypatch) -> None:
    from jalebi import repos, tasks

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    from jalebi.db import Run, utcnow

    run = Run(
        task_id=task.id,
        seq=1,
        session_id="ses_1",
        status="done",
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    session.add(run)
    session.commit()
    task.status = "done"
    session.commit()

    q = app.config["JALEBI_QUEUE"]
    monkeypatch.setattr(
        q, "enqueue_followup", lambda tid, body, pat_name=None, model=None: None
    )
    resp = client.post(
        f"/api/tasks/{task.id}/followup",
        json={"prompt": "more", "pat_name": "default"},
    )
    assert resp.status_code == 202


def test_run_diff_endpoint(client: FlaskClient, session) -> None:
    from jalebi import tasks as tasks_svc
    from jalebi.db import Run, utcnow

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/diffrepo",
        default_branch="main",
        clone_url="https://github.com/owner/diffrepo.git",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")
    run = Run(task_id=task.id, seq=1, status="done", started_at=utcnow(), diff_text="+a\n-b\n")
    session.add(run)
    session.commit()
    run_id = run.id

    resp = client.get(f"/api/tasks/{task.id}/runs/{run_id}/diff")
    assert resp.status_code == 200
    assert resp.get_json()["diff"] == "+a\n-b\n"

    resp = client.get(f"/api/tasks/{task.id}/runs/999/diff")
    assert resp.status_code == 404

    # A run belonging to a DIFFERENT task must 404 too (ownership check).
    other = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="y")
    resp = client.get(f"/api/tasks/{other.id}/runs/{run_id}/diff")
    assert resp.status_code == 404

    # A run with no diff returns "" (not an error).
    no_diff = Run(task_id=other.id, seq=1, status="done", started_at=utcnow())
    session.add(no_diff)
    session.commit()
    resp = client.get(f"/api/tasks/{other.id}/runs/{no_diff.id}/diff")
    assert resp.status_code == 200
    assert resp.get_json()["diff"] == ""

    # run dict exposes has_diff (and does not ship the raw diff text)
    detail = client.get(f"/api/tasks/{task.id}").get_json()
    assert detail["run"]["has_diff"] is True
    assert "diff_text" not in detail["run"]


def test_rerun_interrupted(client: FlaskClient, session, repo_id: int) -> None:
    """A task that was interrupted mid-run can be re-run."""
    from jalebi import tasks as tasks_svc

    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    t = tasks_svc.get_task(session, task_id)
    assert t is not None
    t.status = "interrupted"
    session.commit()

    resp = client.post(f"/api/tasks/{task_id}/rerun")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "queued"


def test_disconnected_repo_task_detail_keeps_repo_name(client, session) -> None:
    """A task on a soft-disconnected repo still shows repo_full_name in detail (T-11)."""
    from jalebi import tasks as tasks_svc

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/disc",
        default_branch="main",
        clone_url="https://github.com/owner/disc.git",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")
    row.connected = False
    session.commit()

    resp = client.get(f"/api/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.get_json()["repo_full_name"] == "owner/disc"
