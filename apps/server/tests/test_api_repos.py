import pytest
from flask.testing import FlaskClient

from jalebi import secrets
from jalebi.github import GitHubNotFound
from jalebi.routes import repos as routes_repos

REPO_INFO = {
    "full_name": "octocat/hello",
    "default_branch": "main",
    "clone_url": "https://github.com/octocat/hello.git",
    "private": False,
}


class FakeGitHubClient:
    def __init__(self, token: str):
        self.token = token

    def get_repo(self, full_name: str) -> dict:
        if full_name == "octocat/hello":
            return dict(REPO_INFO)
        raise GitHubNotFound(full_name)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch, app):
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


def test_get_repos_empty(client: FlaskClient) -> None:
    resp = client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_connect_requires_account(client: FlaskClient) -> None:
    resp = client.post("/api/repos", json={"full_name": "octocat/hello"})
    assert resp.status_code == 400
    assert "account" in resp.get_json()["error"]


def test_connect_missing_full_name(client: FlaskClient) -> None:
    resp = client.post("/api/repos", json={})
    assert resp.status_code == 400


def test_connect_not_found(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    resp = client.post("/api/repos", json={"full_name": "octocat/nope", "pat_name": "test"})
    assert resp.status_code == 404


def test_connect_creates_and_lists(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)

    resp = client.post("/api/repos", json={"full_name": "octocat/hello", "pat_name": "test"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["full_name"] == "octocat/hello"
    assert body["default_branch"] == "main"

    listed = client.get("/api/repos").get_json()
    assert len(listed) == 1
    assert listed[0]["full_name"] == "octocat/hello"
    assert listed[0]["webhook_registered"] is False


def test_connect_update_returns_200(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)

    connect = {"full_name": "octocat/hello", "pat_name": "test"}
    assert client.post("/api/repos", json=connect).status_code == 201
    resp = client.post("/api/repos", json=connect)
    assert resp.status_code == 200
    assert len(client.get("/api/repos").get_json()) == 1


def test_disconnect_repo(client: FlaskClient, app, monkeypatch, session) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    _CONNECT = {"full_name": "octocat/hello", "pat_name": "test"}
    created = client.post("/api/repos", json=_CONNECT).get_json()
    assert client.get("/api/repos").get_json() != []
    resp = client.delete(f"/api/repos/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/repos").get_json() == []


def test_disconnect_missing_repo(client: FlaskClient) -> None:
    resp = client.delete("/api/repos/999")
    assert resp.status_code == 404


def test_list_include_disconnected(client: FlaskClient, app, monkeypatch, session) -> None:
    """Disconnected repos stay hidden by default; ?include_disconnected=1 reveals them."""
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    _CONNECT = {"full_name": "octocat/hello", "pat_name": "test"}
    created = client.post("/api/repos", json=_CONNECT).get_json()
    assert client.delete(f"/api/repos/{created['id']}").status_code == 200
    assert client.get("/api/repos").get_json() == []
    rows = client.get("/api/repos?include_disconnected=1").get_json()
    assert [r["full_name"] for r in rows] == ["octocat/hello"]
    assert rows[0]["connected"] is False


def test_prune_removes_deleted_repos(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    client.post("/api/repos", json={"full_name": "octocat/hello", "pat_name": "test"})
    # create a stale connected repo that no longer exists on GitHub
    app.app_context().push()
    from jalebi import db

    s = db.Session()
    from jalebi.db import Repo

    s.add(Repo(full_name="octocat/gone", default_branch="main", clone_url="u", pat_name="test"))
    s.commit()
    s.close()
    resp = client.post("/api/repos/prune")
    assert resp.status_code == 200
    names = [r["full_name"] for r in client.get("/api/repos").get_json()]
    assert names == ["octocat/hello"]


def test_prune_requires_token(client: FlaskClient, app) -> None:
    """With no accounts at all, prune refuses."""
    from jalebi import secrets as sec

    # Remove the autouse account so there are zero tokens.
    sec.remove_github_token(app.config["JALEBI_CONFIG"], "test")
    resp = client.post("/api/repos/prune")
    assert resp.status_code == 409


def test_branches_for_connected_repo(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    _CONNECT = {"full_name": "octocat/hello", "pat_name": "test"}
    created = client.post("/api/repos", json=_CONNECT).get_json()
    monkeypatch.setattr(
        routes_repos.GitWorkspace, "list_branches", lambda self, full_name: ["main", "dev"]
    )
    monkeypatch.setattr(
        routes_repos.GitWorkspace, "ensure_mirror", lambda self, *a, **k: None
    )
    resp = client.get(f"/api/repos/{created['id']}/branches")
    assert resp.status_code == 200
    assert resp.get_json()["branches"] == ["main", "dev"]


def test_disconnect_is_soft_and_hidden(client: FlaskClient, app, monkeypatch, session) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    _CONNECT = {"full_name": "octocat/hello", "pat_name": "test"}
    created = client.post("/api/repos", json=_CONNECT).get_json()

    resp = client.delete(f"/api/repos/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/repos").get_json() == []

    # row still exists (history preserved), just marked disconnected
    from jalebi import db

    row = session.get(db.Repo, created["id"])
    assert row is not None
    assert row.connected is False


def test_reconnect(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    _CONNECT = {"full_name": "octocat/hello", "pat_name": "test"}
    created = client.post("/api/repos", json=_CONNECT).get_json()
    client.delete(f"/api/repos/{created['id']}")

    resp = client.post(f"/api/repos/{created['id']}/reconnect")
    assert resp.status_code == 200
    names = [r["full_name"] for r in client.get("/api/repos").get_json()]
    assert names == ["octocat/hello"]


def test_reconnect_404_upstream(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    _CONNECT = {"full_name": "octocat/hello", "pat_name": "test"}
    created = client.post("/api/repos", json=_CONNECT).get_json()
    client.delete(f"/api/repos/{created['id']}")
    # FakeGitHubClient only knows octocat/hello; reconnect succeeds for it, so use a
    # repo the client doesn't know to hit the 404 path.
    resp = client.post("/api/repos/999/reconnect")
    assert resp.status_code == 404


def test_connect_with_pat_name(client: FlaskClient, app, monkeypatch) -> None:
    from jalebi import secrets as sec

    sec.add_github_token(app.config["JALEBI_CONFIG"], "work", "ghp_work")
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)

    resp = client.post("/api/repos", json={"full_name": "octocat/hello", "pat_name": "work"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["pat_name"] == "work"

    listed = client.get("/api/repos").get_json()
    assert listed[0]["pat_name"] == "work"


def test_connect_unknown_pat_rejected(client: FlaskClient, app) -> None:
    resp = client.post("/api/repos", json={"full_name": "octocat/hello", "pat_name": "nope"})
    assert resp.status_code == 400
    assert "account" in resp.get_json()["error"]


def test_prune_continues_on_transient_error(client, app, monkeypatch, session) -> None:
    """One repo erroring must not abort the whole prune sweep."""
    import httpx
    from sqlalchemy import select

    from jalebi import repos as repos_svc
    from jalebi.db import Repo


    class PartialClient:
        def __init__(self, token):
            self.token = token

        def get_repo(self, full_name):
            if full_name == "octocat/deleted":
                raise GitHubNotFound(full_name)
            if full_name == "octocat/flaky":
                raise httpx.HTTPError("boom")
            return dict(REPO_INFO)

        def close(self):
            pass

    monkeypatch.setattr(routes_repos, "GitHubClient", PartialClient)
    repos_svc.upsert_repo(
        session, full_name="octocat/deleted", default_branch="main", clone_url="x", pat_name="test"
    )
    repos_svc.upsert_repo(
        session, full_name="octocat/flaky", default_branch="main", clone_url="x", pat_name="test"
    )
    repos_svc.upsert_repo(
        session, full_name="octocat/hello", default_branch="main", clone_url="x", pat_name="test"
    )

    resp = client.post("/api/repos/prune")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["removed"] == ["octocat/deleted"]
    assert len(body["errors"]) == 1

    session.expire_all()
    names = {r.full_name: r.connected for r in session.execute(select(Repo)).scalars()}
    assert names["octocat/deleted"] is False
    assert names["octocat/flaky"] is True
    assert names["octocat/hello"] is True


def test_reconnect_uses_repo_bound_account(client, app, monkeypatch, session) -> None:
    """Reconnecting a named-account repo resolves that account's token."""
    from jalebi import repos as repos_svc
    from jalebi import secrets as sec

    sec.add_github_token(app.config["JALEBI_CONFIG"], "work", "ghp_work")

    captured: list[str] = []

    class CapturingClient:
        def __init__(self, token):
            captured.append(token)

        def get_repo(self, full_name):
            return dict(REPO_INFO)

        def close(self):
            pass

    monkeypatch.setattr(routes_repos, "GitHubClient", CapturingClient)
    row, _ = repos_svc.upsert_repo(
        session,
        full_name="octocat/hello",
        default_branch="main",
        clone_url="https://x",
        pat_name="work",
    )
    row.connected = False
    session.commit()

    resp = client.post(f"/api/repos/{row.id}/reconnect")
    assert resp.status_code == 200
    assert captured == ["ghp_work"]


def test_connect_empty_pat_rejected(client, app, monkeypatch) -> None:
    """An explicit empty pat_name is rejected — an account is required."""
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)

    resp = client.post("/api/repos", json={"full_name": "octocat/hello", "pat_name": ""})
    assert resp.status_code == 400
    assert "account" in resp.get_json()["error"]


def test_toggle_check_runs_enabled(client, app, monkeypatch, session) -> None:
    """PATCH /api/repos/<id> toggles check_runs_enabled (PRD F15)."""


    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    created = client.post(
        "/api/repos", json={"full_name": "octocat/hello", "pat_name": "test"}
    ).get_json()
    rid = created["id"]
    assert created["check_runs_enabled"] is False

    resp = client.patch(f"/api/repos/{rid}", json={"check_runs_enabled": True})
    assert resp.status_code == 200
    assert resp.get_json()["check_runs_enabled"] is True

    resp = client.patch(f"/api/repos/{rid}", json={"check_runs_enabled": False})
    assert resp.status_code == 200
    assert resp.get_json()["check_runs_enabled"] is False

    # Non-bool rejected
    resp = client.patch(f"/api/repos/{rid}", json={"check_runs_enabled": "yes"})
    assert resp.status_code == 400

    # Unknown repo
    resp = client.patch("/api/repos/999999", json={"check_runs_enabled": True})
    assert resp.status_code == 404

    # Empty payload rejected
    resp = client.patch(f"/api/repos/{rid}", json={})
    assert resp.status_code == 400


def test_toggle_poll_fallback(client, app, monkeypatch) -> None:
    """PATCH /api/repos/<id> also toggles poll_fallback (Phase 4 T2.1)."""
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    created = client.post(
        "/api/repos", json={"full_name": "octocat/hello", "pat_name": "test"}
    ).get_json()
    rid = created["id"]
    assert created["poll_fallback"] is False

    resp = client.patch(f"/api/repos/{rid}", json={"poll_fallback": True})
    assert resp.status_code == 200
    assert resp.get_json()["poll_fallback"] is True

    resp = client.patch(f"/api/repos/{rid}", json={"poll_fallback": False})
    assert resp.status_code == 200
    assert resp.get_json()["poll_fallback"] is False

    # Non-bool rejected.
    resp = client.patch(f"/api/repos/{rid}", json={"poll_fallback": "yes"})
    assert resp.status_code == 400
    assert "poll_fallback" in resp.get_json()["error"]

    # Unknown repo
    resp = client.patch("/api/repos/999999", json={"poll_fallback": True})
    assert resp.status_code == 404

    # Empty payload still rejected.
    resp = client.patch(f"/api/repos/{rid}", json={})
    assert resp.status_code == 400
