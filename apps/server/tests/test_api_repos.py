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
def _no_env_token(monkeypatch):
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)


def test_get_repos_empty(client: FlaskClient) -> None:
    resp = client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_connect_requires_token(client: FlaskClient) -> None:
    resp = client.post("/api/repos", json={"full_name": "octocat/hello"})
    assert resp.status_code == 409


def test_connect_missing_full_name(client: FlaskClient) -> None:
    resp = client.post("/api/repos", json={})
    assert resp.status_code == 400


def test_connect_not_found(client: FlaskClient, app, monkeypatch) -> None:
    secrets.store_secret(app.config["JALEBI_CONFIG"], secrets.GITHUB_TOKEN_KEY, "ghp_test")
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)
    resp = client.post("/api/repos", json={"full_name": "octocat/nope"})
    assert resp.status_code == 404


def test_connect_creates_and_lists(client: FlaskClient, app, monkeypatch) -> None:
    secrets.store_secret(app.config["JALEBI_CONFIG"], secrets.GITHUB_TOKEN_KEY, "ghp_test")
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)

    resp = client.post("/api/repos", json={"full_name": "octocat/hello"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["full_name"] == "octocat/hello"
    assert body["default_branch"] == "main"

    listed = client.get("/api/repos").get_json()
    assert len(listed) == 1
    assert listed[0]["full_name"] == "octocat/hello"
    assert listed[0]["webhook_registered"] is False


def test_connect_update_returns_200(client: FlaskClient, app, monkeypatch) -> None:
    secrets.store_secret(app.config["JALEBI_CONFIG"], secrets.GITHUB_TOKEN_KEY, "ghp_test")
    monkeypatch.setattr(routes_repos, "GitHubClient", FakeGitHubClient)

    assert client.post("/api/repos", json={"full_name": "octocat/hello"}).status_code == 201
    resp = client.post("/api/repos", json={"full_name": "octocat/hello"})
    assert resp.status_code == 200
    assert len(client.get("/api/repos").get_json()) == 1
