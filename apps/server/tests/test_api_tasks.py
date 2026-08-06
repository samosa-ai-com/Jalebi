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
    assert body["retry_count"] == 1


def test_rerun_running_conflict(client: FlaskClient, repo_id: int) -> None:
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    resp = client.post(f"/api/tasks/{task_id}/rerun")
    assert resp.status_code == 409


def test_publish_not_found(client: FlaskClient) -> None:
    assert client.post("/api/tasks/999/publish").status_code == 404
