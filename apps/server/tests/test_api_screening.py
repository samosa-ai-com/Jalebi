"""Screening API tests (PRD F10)."""

import threading
import time

import pytest

from jalebi import repos, secrets

FULL_NAME = "owner/screenapi"


@pytest.fixture
def repo_row(session):
    row, _ = repos.upsert_repo(
        session, full_name=FULL_NAME, default_branch="main",
        clone_url="https://example.com/owner/screenapi.git", pat_name="test",
    )
    return row


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")
    yield
    secrets.remove_github_token(app.config["JALEBI_CONFIG"], "test")


def _payload(repo_row, **overrides):
    body = dict(
        repo_id=repo_row.id,
        name="Security posture",
        system_prompt="Audit security.",
        cadence_cron="0 6 * * 1",
        notify_ntfy=True,
    )
    body.update(overrides)
    return body


def test_templates_endpoint(client):
    res = client.get("/api/screenings/templates")
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "system_prompt" in data[0]


def test_create_and_list_screen(client, repo_row):
    res = client.post("/api/screenings", json=_payload(repo_row))
    assert res.status_code == 201
    screen = res.get_json()
    assert screen["name"] == "Security posture"

    res = client.get("/api/screenings")
    assert res.status_code == 200
    assert any(s["id"] == screen["id"] for s in res.get_json())
    assert all("latest_run" in s for s in res.get_json())


def test_create_validates(client, repo_row):
    res = client.post("/api/screenings", json={})
    assert res.status_code == 400
    res = client.post("/api/screenings", json=_payload(repo_row, cadence_cron="bogus"))
    assert res.status_code == 400
    assert "cadence_cron" in res.get_json()["error"]


def test_get_update_delete(client, repo_row):
    created = client.post("/api/screenings", json=_payload(repo_row)).get_json()
    sid = created["id"]

    res = client.get(f"/api/screenings/{sid}")
    assert res.status_code == 200
    assert res.get_json()["id"] == sid

    res = client.put(f"/api/screenings/{sid}", json={"enabled": False, "notify_ntfy": False})
    assert res.status_code == 200
    body = res.get_json()
    assert body["enabled"] is False
    assert body["notify_ntfy"] is False

    res = client.put(f"/api/screenings/{sid}", json={"cadence_cron": "bogus"})
    assert res.status_code == 400

    res = client.delete(f"/api/screenings/{sid}")
    assert res.status_code == 200
    assert client.get(f"/api/screenings/{sid}").status_code == 404
    assert client.delete(f"/api/screenings/{sid}").status_code == 404


def test_runs_endpoint_empty_then_run(client, repo_row, monkeypatch):
    created = client.post("/api/screenings", json=_payload(repo_row)).get_json()
    sid = created["id"]

    res = client.get(f"/api/screenings/{sid}/runs")
    assert res.status_code == 200
    assert res.get_json() == []

    # Run now is async: stub the engine's run_screen so nothing executes.
    monkeypatch.setattr(
        "jalebi.screening.ScreeningEngine.run_screen",
        lambda self, session, screen, *, force=False: None,
    )
    res = client.post(f"/api/screenings/{sid}/run")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    # Give the daemon thread a beat to finish its (stubbed) work.
    time.sleep(0.05)
    assert threading.active_count() >= 1


def test_unknown_screen_404(client):
    assert client.get("/api/screenings/999").status_code == 404
    assert client.get("/api/screenings/999/runs").status_code == 404
    assert client.put("/api/screenings/999", json={}).status_code == 404
    assert client.delete("/api/screenings/999").status_code == 404
    assert client.post("/api/screenings/999/run").status_code == 404
