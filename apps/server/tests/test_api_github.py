import pytest
from flask.testing import FlaskClient

from jalebi import secrets
from jalebi.config import Config
from jalebi.github import TokenInfo
from jalebi.routes import github as routes_github

VALID_INFO = TokenInfo(
    valid=True,
    login="octocat",
    token_type="classic",
    granted_scopes=["repo"],
    missing_scopes=[],
)


class FakeClient:
    def __init__(self, token: str):
        self.token = token

    def validate_token(self) -> TokenInfo:
        return VALID_INFO

    def list_repos(self) -> list[dict]:
        return [
            {
                "full_name": "octocat/hello",
                "private": False,
                "default_branch": "main",
                "clone_url": "https://github.com/octocat/hello.git",
                "html_url": "https://github.com/octocat/hello",
            }
        ]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)


def _app_config(app) -> Config:
    return app.config["JALEBI_CONFIG"]


def _store_token(app, token: str = "ghp_test") -> None:
    secrets.store_secret(_app_config(app), secrets.GITHUB_TOKEN_KEY, token)


def test_status_no_token(client: FlaskClient) -> None:
    resp = client.get("/api/github/status")
    assert resp.status_code == 409
    assert resp.get_json()["valid"] is False


def test_status_with_token(client: FlaskClient, app, monkeypatch) -> None:
    _store_token(app)
    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    resp = client.get("/api/github/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["valid"] is True
    assert body["login"] == "octocat"
    assert body["granted_scopes"] == ["repo"]
    assert "ghp_test" not in resp.get_data(as_text=True)


def test_put_token_stores_and_returns_detail(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    resp = client.put("/api/github/token", json={"token": "ghp_newtoken"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stored"] is True
    assert body["detail"]["login"] == "octocat"
    assert "ghp_newtoken" not in resp.get_data(as_text=True)
    assert secrets.load_secret(_app_config(app), secrets.GITHUB_TOKEN_KEY) == "ghp_newtoken"


def test_put_token_invalid_rejected(client: FlaskClient, monkeypatch) -> None:
    class RejectingClient:
        def __init__(self, token: str):
            self.token = token

        def validate_token(self) -> TokenInfo:
            return TokenInfo(valid=False, error="Bad credentials")

        def close(self) -> None:
            pass

    monkeypatch.setattr(routes_github, "GitHubClient", RejectingClient)
    resp = client.put("/api/github/token", json={"token": "ghp_bad"})
    assert resp.status_code == 400
    assert resp.get_json()["stored"] is False


def test_put_token_missing_body(client: FlaskClient) -> None:
    resp = client.put("/api/github/token", json={})
    assert resp.status_code == 400


def test_repos_no_token(client: FlaskClient) -> None:
    resp = client.get("/api/github/repos")
    assert resp.status_code == 409


def test_repos_with_token(client: FlaskClient, app, monkeypatch) -> None:
    _store_token(app)
    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    resp = client.get("/api/github/repos")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body[0]["full_name"] == "octocat/hello"
    assert "ghp_test" not in resp.get_data(as_text=True)
