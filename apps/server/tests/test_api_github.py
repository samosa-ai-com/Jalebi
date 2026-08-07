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


def test_context_requires_repo_param(client: FlaskClient) -> None:
    resp = client.get("/api/github/context")
    assert resp.status_code == 400


def test_context_returns_issues_prs_branches(client: FlaskClient, app, monkeypatch) -> None:
    _store_token(app)

    class ContextClient(FakeClient):
        def list_issues(self, full_name):
            return [{"number": 1, "title": "bug", "html_url": "u", "state": "open"}]

        def list_prs(self, full_name):
            return [{"number": 7, "title": "feature", "html_url": "u", "state": "open"}]

        def list_branches(self, full_name):
            return ["main", "dev"]

    monkeypatch.setattr(routes_github, "GitHubClient", ContextClient)
    resp = client.get("/api/github/context?repo=octocat/hello")
    assert resp.status_code == 200
    body = resp.get_json()
    assert [i["number"] for i in body["issues"]] == [1]
    assert [p["number"] for p in body["prs"]] == [7]
    assert body["branches"] == ["main", "dev"]
    assert "ghp_test" not in resp.get_data(as_text=True)


def test_tokens_list_and_add_remove(client: FlaskClient, app, monkeypatch) -> None:
    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)

    resp = client.post("/api/github/tokens", json={"name": "work", "token": "ghp_work"})
    assert resp.status_code == 200
    assert resp.get_json()["stored"] is True

    listed = client.get("/api/github/tokens").get_json()
    assert listed["accounts"][0]["name"] == "work"
    assert listed["accounts"][0]["login"] == "octocat"
    assert listed["accounts"][0]["valid"] is True
    assert "ghp_work" not in client.get("/api/github/tokens").get_data(as_text=True)

    resp = client.delete("/api/github/tokens/work")
    assert resp.status_code == 200
    assert client.get("/api/github/tokens").get_json()["accounts"] == []


def test_tokens_default_account_listed_first(client: FlaskClient, app, monkeypatch) -> None:
    secrets.store_secret(app.config["JALEBI_CONFIG"], secrets.GITHUB_TOKEN_KEY, "ghp_default")
    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)

    listed = client.get("/api/github/tokens").get_json()
    assert listed["default"] == "default"
    assert listed["accounts"][0]["name"] == "default"
    assert listed["accounts"][0]["is_default"] is True


def test_add_token_invalid_rejected(client: FlaskClient, monkeypatch) -> None:
    class RejectingClient:
        def __init__(self, token: str):
            self.token = token

        def validate_token(self) -> TokenInfo:
            return TokenInfo(valid=False, error="Bad credentials")

        def close(self) -> None:
            pass

    monkeypatch.setattr(routes_github, "GitHubClient", RejectingClient)
    resp = client.post("/api/github/tokens", json={"name": "work", "token": "ghp_bad"})
    assert resp.status_code == 400
    assert resp.get_json()["stored"] is False


def test_add_token_missing_fields(client: FlaskClient) -> None:
    resp = client.post("/api/github/tokens", json={"name": "work"})
    assert resp.status_code == 400


def test_repos_tagged_by_account(client: FlaskClient, app, monkeypatch) -> None:
    secrets.store_secret(app.config["JALEBI_CONFIG"], secrets.GITHUB_TOKEN_KEY, "ghp_default")
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "work", "ghp_work")

    class MultiClient:
        def __init__(self, token: str):
            self.token = token

        def list_repos(self):
            if self.token == "ghp_work":
                return [
                    {
                        "full_name": "acct2/other",
                        "private": True,
                        "default_branch": "main",
                        "clone_url": "u2",
                        "html_url": "h2",
                    }
                ]
            return [
                {
                    "full_name": "acct1/hello",
                    "private": False,
                    "default_branch": "main",
                    "clone_url": "u1",
                    "html_url": "h1",
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr(routes_github, "GitHubClient", MultiClient)
    body = client.get("/api/github/repos").get_json()
    by_account = {r["full_name"]: r.get("account") for r in body}
    assert by_account == {"acct1/hello": "default", "acct2/other": "work"}

    only_work = client.get("/api/github/repos?account=work").get_json()
    assert [r["full_name"] for r in only_work] == ["acct2/other"]


def test_add_token_default_name_rejected(client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    resp = client.post("/api/github/tokens", json={"name": "default", "token": "ghp_x"})
    assert resp.status_code == 400
    assert "reserved" in resp.get_json()["error"]


def test_delete_token_reports_affected_repos_and_tasks(
    client: FlaskClient, app, monkeypatch, session
) -> None:
    from jalebi import db, repos
    from jalebi import tasks as tasks_svc
    from jalebi.db import Run, utcnow

    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    client.post("/api/github/tokens", json={"name": "work", "token": "ghp_work"})
    row, _ = repos.upsert_repo(
        session,
        full_name="octocat/hello",
        default_branch="main",
        clone_url="https://github.com/octocat/hello.git",
        pat_name="work",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x", pat_name="work"
    )
    run = Run(
        task_id=task.id,
        seq=1,
        status="done",
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    session.add(run)
    session.commit()
    repo_id, task_id, run_id = row.id, task.id, run.id

    resp = client.delete("/api/github/tokens/work")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["removed"] == "work"
    assert body["repos_affected"] == ["octocat/hello"]
    assert body["tasks_affected"] == 1

    # The account's repos and tasks are actually deleted (with their runs).
    session.expire_all()
    assert session.get(db.Repo, repo_id) is None
    assert tasks_svc.get_task(session, task_id) is None
    assert session.get(Run, run_id) is None


def test_delete_token_deletes_tasks_on_repos_bound_to_account(
    client: FlaskClient, app, monkeypatch, session
) -> None:
    """A task on the account's repo is deleted even if it used another PAT."""
    from jalebi import db, repos
    from jalebi import tasks as tasks_svc

    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    client.post("/api/github/tokens", json={"name": "work", "token": "ghp_work"})
    row, _ = repos.upsert_repo(
        session,
        full_name="octocat/hello",
        default_branch="main",
        clone_url="https://github.com/octocat/hello.git",
        pat_name="work",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x", pat_name=None
    )
    repo_id, task_id = row.id, task.id

    client.delete("/api/github/tokens/work")

    session.expire_all()
    assert tasks_svc.get_task(session, task_id) is None
    assert session.get(db.Repo, repo_id) is None


def test_delete_token_includes_disconnected_repos(client, app, monkeypatch, session) -> None:
    """Deleting an account also removes its soft-disconnected repos + tasks."""
    from jalebi import db, repos
    from jalebi import tasks as tasks_svc

    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    client.post("/api/github/tokens", json={"name": "work", "token": "ghp_work"})
    row, _ = repos.upsert_repo(
        session,
        full_name="octocat/disc",
        default_branch="main",
        clone_url="https://github.com/octocat/disc.git",
        pat_name="work",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x", pat_name="work"
    )
    repo_id, task_id = row.id, task.id
    row.connected = False  # soft-disconnect AFTER the task exists
    session.commit()

    resp = client.delete("/api/github/tokens/work")
    assert resp.status_code == 200
    assert resp.get_json()["repos_affected"] == ["octocat/disc"]
    assert resp.get_json()["tasks_affected"] == 1

    session.expire_all()
    assert session.get(db.Repo, repo_id) is None
    assert tasks_svc.get_task(session, task_id) is None


def test_delete_token_cancels_running_tasks_first(client, app, monkeypatch, session) -> None:
    """Account deletion flags in-flight runs as cancelled before deleting rows."""
    from jalebi import repos
    from jalebi import tasks as tasks_svc
    from jalebi.queue import _RunState

    monkeypatch.setattr(routes_github, "GitHubClient", FakeClient)
    client.post("/api/github/tokens", json={"name": "work", "token": "ghp_work"})
    row, _ = repos.upsert_repo(
        session,
        full_name="octocat/hello",
        default_branch="main",
        clone_url="https://github.com/octocat/hello.git",
        pat_name="work",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x", pat_name="work"
    )
    task.status = "running"
    session.commit()

    q = app.config["JALEBI_QUEUE"]
    state = _RunState(None)
    q._running[task.id] = state

    resp = client.delete("/api/github/tokens/work")
    assert resp.status_code == 200
    # The queue's in-flight state was cancelled (the worker will wind down).
    assert state.reason == "cancelled"
    # Verify the row is gone via a fresh session (the local identity map is stale).
    from jalebi import db

    with db.Session() as s2:
        assert tasks_svc.get_task(s2, task.id) is None
