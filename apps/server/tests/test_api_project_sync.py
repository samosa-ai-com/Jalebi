"""Project board sync HTTP routes (issue #23). Fake GitHub, fake PATs only."""

import pytest
from sqlalchemy import update

from jalebi import repos, secrets, settings
from jalebi.db import Repo


@pytest.fixture(autouse=True)
def _tokens(app):
    cfg = app.config["JALEBI_CONFIG"]
    secrets.add_github_token(cfg, "board-acct", "ghp_board")
    secrets.add_github_token(cfg, "repo-acct", "ghp_repo")
    yield
    secrets.remove_github_token(cfg, "board-acct")
    secrets.remove_github_token(cfg, "repo-acct")


@pytest.fixture()
def repo_id(session):
    repo, _ = repos.upsert_repo(
        session,
        full_name="owner/sync-repo",
        default_branch="main",
        clone_url="https://example.com/owner/sync-repo.git",
        pat_name="repo-acct",
    )
    session.commit()
    return repo.id


class FakeClient:
    issues: list[dict] = []
    prs: list[dict] = []

    def __init__(self, token: str):
        self.token = token

    def close(self) -> None:
        pass

    def get_projectv2_id(self, url: str) -> str:
        return "PVT_board"

    def add_project_item(self, project_id: str, node_id: str) -> str:
        return f"PVTI_{node_id}"

    def list_issues(self, full_name: str, since=None):
        return list(FakeClient.issues)

    def list_prs(self, full_name: str):
        return list(FakeClient.prs)


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    FakeClient.issues = []
    FakeClient.prs = []
    monkeypatch.setattr("jalebi.project_sync.GitHubClient", FakeClient)


def _configure(session):
    settings.set_setting(session, "project_sync_enabled", True)
    settings.set_setting(
        session, "project_url", "https://github.com/orgs/octo-org/projects/5"
    )
    settings.set_setting(session, "project_pat_name", "board-acct")


def test_status_unconfigured(client):
    res = client.get("/api/project-sync/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["configured"] is False
    assert body["total_items"] == 0


def test_status_configured_resolves_board(client, session):
    _configure(session)
    res = client.get("/api/project-sync/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["configured"] is True
    assert body["project_id"] == "PVT_board"


def test_sync_now_bad_body(client):
    res = client.post("/api/project-sync/sync-now", json={})
    assert res.status_code == 400


def test_sync_now_repo_not_found(client):
    res = client.post("/api/project-sync/sync-now", json={"repo_id": 9999})
    assert res.status_code == 404


def test_sync_now_unconfigured_board(client, session, repo_id):
    res = client.post("/api/project-sync/sync-now", json={"repo_id": repo_id})
    assert res.status_code == 409


def test_sync_now_network_error_is_502(client, session, repo_id, monkeypatch):
    import httpx

    _configure(session)
    session.execute(
        update(Repo).where(Repo.id == repo_id).values(project_sync_enabled=True)
    )
    session.commit()

    def boom(self, url: str) -> str:
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(FakeClient, "get_projectv2_id", boom)
    res = client.post("/api/project-sync/sync-now", json={"repo_id": repo_id})
    assert res.status_code == 502


def test_sync_now_item_failure_is_resilient_200(client, session, repo_id, monkeypatch):
    import httpx

    _configure(session)
    session.execute(
        update(Repo).where(Repo.id == repo_id).values(project_sync_enabled=True)
    )
    session.commit()
    FakeClient.issues = [{"number": 1, "node_id": "I_1"}]

    def boom(self, project_id: str, node_id: str) -> str:
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(FakeClient, "add_project_item", boom)
    res = client.post("/api/project-sync/sync-now", json={"repo_id": repo_id})
    assert res.status_code == 200
    assert res.get_json()["added"] == 0


def test_sync_now_dry_run_then_real(client, session, repo_id):
    _configure(session)
    session.execute(
        update(Repo).where(Repo.id == repo_id).values(project_sync_enabled=True)
    )
    session.commit()
    FakeClient.issues = [{"number": 1, "node_id": "I_1"}]
    res = client.post(
        "/api/project-sync/sync-now", json={"repo_id": repo_id, "dry_run": True}
    )
    assert res.status_code == 200
    assert res.get_json()["would_add"] == [{"type": "issue", "number": 1}]
    res = client.post("/api/project-sync/sync-now", json={"repo_id": repo_id})
    assert res.status_code == 200
    assert res.get_json()["added"] == 1
    res = client.get("/api/project-sync/status")
    assert res.get_json()["total_items"] == 1
