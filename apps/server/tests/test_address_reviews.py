"""Creation-time address_reviews flag (freeform + linked PR)."""

import pytest
from flask.testing import FlaskClient

from jalebi import prompts, repos, secrets, tasks
from jalebi.db import Repo, Task


@pytest.fixture
def repo_id(session) -> int:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    return row.id


@pytest.fixture(autouse=True)
def _named_token(monkeypatch, app):
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


class _PrClient:
    """Fake GitHub client returning one PR with one review comment."""

    def __init__(self, token): ...

    def get_pr(self, full_name, number):
        return {
            "number": number,
            "title": "PR title",
            "body": "PR body",
            "html_url": "u",
            "state": "open",
            "base": "main",
            "head": "feature",
            "author": "bob",
        }

    def list_pr_reviews(self, full_name, number):
        return [{"id": 1, "body": "needs tests", "user": "carol"}]

    def close(self): ...


class _NoReviewClient(_PrClient):
    def list_pr_reviews(self, full_name, number):
        return []


def test_flag_requires_bool(client: FlaskClient, repo_id: int) -> None:
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x", "address_reviews": "yes"},
    )
    assert resp.status_code == 400
    assert "boolean" in resp.get_json()["error"]


def test_flag_requires_linked_pr(client: FlaskClient, repo_id: int) -> None:
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x", "address_reviews": True},
    )
    assert resp.status_code == 400
    assert "pr_number" in resp.get_json()["error"]


def test_flag_rejected_for_non_freeform(client: FlaskClient, repo_id: int, session) -> None:
    repo = session.get(Repo, repo_id)
    assert repo is not None
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "pr_review",
            "prompt": "x",
            "pr_number": 7,
            "address_reviews": True,
        },
    )
    assert resp.status_code == 400
    assert "freeform" in resp.get_json()["error"]


def test_service_rejects_flag_for_non_freeform(session, repo_id: int) -> None:
    with pytest.raises(ValueError, match="freeform"):
        tasks.create_task(
            session, type_="pr_review", repo_id=repo_id, prompt="x", address_reviews=True
        )


def test_service_rejects_flag_without_linked_pr(session, repo_id: int) -> None:
    with pytest.raises(ValueError, match="linked PR"):
        tasks.create_task(
            session, type_="freeform", repo_id=repo_id, prompt="x", address_reviews=True
        )


def test_flag_embeds_fetched_reviews(
    client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", _PrClient)
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "fix it",
            "pr_number": 7,
            "address_reviews": True,
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["address_reviews"] is True
    row = session.get(Task, body["id"])
    assert row is not None and row.address_reviews is True
    repo = session.get(Repo, repo_id)
    md = prompts.build_agent_md(row, repo)
    assert "### PR review comments to address" in md
    assert "needs tests" in md


def test_flag_without_fetched_reviews_adds_self_fetch_fallback(
    client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    """No reviews at creation (none yet, or fetch failed) → the instruction is
    still guaranteed; the agent pulls the live comments itself."""
    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", _NoReviewClient)
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "fix it",
            "pr_number": 7,
            "address_reviews": True,
        },
    )
    assert resp.status_code == 201
    row = session.get(Task, resp.get_json()["id"])
    repo = session.get(Repo, repo_id)
    md = prompts.build_agent_md(row, repo)
    assert "### PR review comments to address" in md
    assert "/pulls/7/reviews" in md


def test_no_flag_no_reviews_no_section(session, repo_id: int) -> None:
    """Unflagged tasks keep the exact legacy behavior (no instruction)."""
    repo = session.get(Repo, repo_id)
    assert repo is not None
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_id,
        prompt="fix it",
        prs=[7],
        context={
            "prs": [
                {
                    "number": 7,
                    "title": "T",
                    "body": "B",
                    "html_url": "u",
                    "base": "main",
                    "head": "f",
                    "state": "open",
                    "author": "bob",
                    "reviews": [],
                }
            ]
        },
    )
    assert task.address_reviews is False
    md = prompts.build_agent_md(task, repo)
    assert "PR review comments to address" not in md


def test_task_dict_exposes_flag(client: FlaskClient, repo_id: int) -> None:
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["address_reviews"] is False
