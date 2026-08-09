"""Tests for the reviewer-workflow API (assign reviewers, reviewers on create,
address-reviewers follow-up)."""

import pytest

from jalebi import repos, secrets
from jalebi.db import Run, Task


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


@pytest.fixture(autouse=True)
def _fake_github(monkeypatch):
    """The task-create route fetches PR context for pr_review — stub it out."""

    class FakeClient:
        def __init__(self, token):
            self.token = token

        def get_pr(self, full_name, number):
            return {
                "number": number,
                "title": f"PR {number}",
                "body": "body",
                "html_url": "u",
                "state": "open",
                "base": "main",
                "head": "h",
                "author": "bob",
            }

        def close(self):
            pass

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeClient)


@pytest.fixture
def repo(session) -> int:
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    return row.id


def _reviewer(client, slug: str = "auditor") -> None:
    res = client.post(
        "/api/agents",
        json={
            "id": slug,
            "name": slug.title(),
            "kind": "reviewer",
            "personality_md": "Review carefully.",
            "custom_instructions": "Check correctness.",
            "enabled": True,
        },
    )
    assert res.status_code == 201, res.get_json()


def _pr_review_task(client, repo_id: int, pr_number: int = 11) -> int:
    res = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "pr_review",
            "prompt": "review it",
            "pr_number": pr_number,
        },
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def test_assign_reviewers_endpoint(client, repo) -> None:
    _reviewer(client, "auditor-a")
    _reviewer(client, "auditor-b")
    task_id = _pr_review_task(client, repo, 5)

    res = client.post(
        f"/api/tasks/{task_id}/reviewers",
        json={"reviewers": ["auditor-a", "auditor-b"]},
    )
    assert res.status_code == 201
    created = res.get_json()
    assert len(created) == 2
    for t in created:
        assert t["type"] == "pr_review"
        assert t["agent_id"] in ("auditor-a", "auditor-b")
        assert t["publish_mode"] == "manual"
    # The original task's PR card lists the reviewers via the task payload.
    original = client.get(f"/api/tasks/{task_id}").get_json()
    assert len(original["reviewers"]) == 2


def test_assign_reviewers_validates_kind(client, repo) -> None:
    client.post(
        "/api/agents",
        json={"id": "general-agent", "name": "G", "kind": "general", "enabled": True},
    )
    task_id = _pr_review_task(client, repo, 6)
    res = client.post(f"/api/tasks/{task_id}/reviewers", json={"reviewers": ["general-agent"]})
    assert res.status_code == 400
    assert "not 'reviewer'" in res.get_json()["error"]


def test_assign_reviewers_requires_pr(client, repo) -> None:
    _reviewer(client)
    # A freeform task has no PR — assigning reviewers must 409.
    res = client.post(
        "/api/tasks",
        json={"repo_id": repo, "type": "freeform", "prompt": "do it"},
    )
    assert res.status_code == 201
    freeform_id = res.get_json()["id"]
    res = client.post(f"/api/tasks/{freeform_id}/reviewers", json={"reviewers": ["auditor"]})
    assert res.status_code == 409


def test_create_task_with_reviewers(client, repo) -> None:
    _reviewer(client, "auditor-a")
    _reviewer(client, "auditor-b")
    res = client.post(
        "/api/tasks",
        json={
            "repo_id": repo,
            "type": "pr_review",
            "pr_number": 8,
            "prompt": "review it",
            "reviewers": ["auditor-a", "auditor-b"],
        },
    )
    assert res.status_code == 201
    created = res.get_json()
    assert len(created) == 2


def test_reviewers_only_valid_for_pr_review(client, repo) -> None:
    _reviewer(client)
    res = client.post(
        "/api/tasks",
        json={
            "repo_id": repo,
            "type": "freeform",
            "prompt": "do it",
            "reviewers": ["auditor"],
        },
    )
    assert res.status_code == 400
    assert "only valid for pr_review" in res.get_json()["error"]


def test_followup_address_reviewers_uses_pr_number_column(
    client, repo, session, monkeypatch
) -> None:
    """A fix task whose PR lives only in pr_number (after auto-publish) still gets
    its review comments embedded for the address-reviewers follow-up."""
    from jalebi import tasks as tasks_service
    from jalebi.db import Run

    task = tasks_service.create_task(
        session,
        type_="issue_fix",
        repo_id=repo,
        prompt="fix it",
        issues=[1],
    )
    task.pr_number = 99  # auto-published — no prs_json
    task.status = "done"
    session.add(Run(task_id=task.id, seq=1, status="done", session_id="ses_1"))
    session.commit()
    task_id = task.id

    captured: dict = {}

    class RecordingQueue:
        def enqueue_followup(self, task_id, body, pat_name=None, model=None):
            captured["body"] = body

    monkeypatch.setattr("jalebi.routes.tasks._queue", lambda: RecordingQueue())

    class FakeGithub:
        def __init__(self, token):
            pass

        def list_pr_reviews(self, full_name, pr_number):
            return [{"id": 1, "body": "Please fix the off-by-one.", "user": "bob"}]

        def close(self):
            pass

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeGithub)

    res = client.post(
        f"/api/tasks/{task_id}/followup",
        json={"prompt": "address the reviewers", "include_reviews": True},
    )
    assert res.status_code == 202, res.get_json()
    assert "Please fix the off-by-one." in captured["body"]


def test_followup_address_reviewers_embeds_reviews(client, repo, session, monkeypatch) -> None:
    """include_reviews fetches the PR's review comments and embeds them (masked)."""
    _reviewer(client)
    task_id = _pr_review_task(client, repo, 12)

    # Give the task a resumable session (needed by the followup route) and a
    # terminal status (a follow-up on a queued task is refused).
    task = session.get(Task, task_id)
    assert task is not None
    task.status = "done"
    run = Run(task_id=task_id, seq=1, status="done", session_id="ses_1")
    session.add(run)
    session.commit()

    captured: dict = {}

    class RecordingQueue:
        def enqueue_followup(self, task_id, body, pat_name=None, model=None):
            captured["body"] = body

    monkeypatch.setattr("jalebi.routes.tasks._queue", lambda: RecordingQueue())

    class FakeGithub:
        def __init__(self, token):
            pass

        def list_pr_reviews(self, full_name, pr_number):
            return [
                {"id": 1, "body": "Use a parameterized query here.", "user": "bob"},
                {"id": 2, "body": "ghp_secret_review_should_be_masked", "user": "carol"},
            ]

        def close(self):
            pass

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeGithub)
    # Register a secret so masking redacts the value in the embedded review.
    secrets.add_github_token(
        client.application.config["JALEBI_CONFIG"],
        "mask-secret",
        "ghp_secret_review_should_be_masked",
    )

    res = client.post(
        f"/api/tasks/{task_id}/followup",
        json={"prompt": "address the reviewers", "include_reviews": True},
    )
    assert res.status_code == 202, res.get_json()
    body = captured["body"]
    assert "address the reviewers" in body
    assert "## PR review comments to address" in body
    assert "Use a parameterized query here." in body
    assert "BEGIN UNTRUSTED DATA" in body
    # The secret value inside a review body is masked before embedding.
    assert "ghp_secret_review_should_be_masked" not in body
    assert "***" in body
