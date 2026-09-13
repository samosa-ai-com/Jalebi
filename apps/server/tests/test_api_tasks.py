import json

import pytest
from flask.testing import FlaskClient

from jalebi import repos, secrets, tasks
from jalebi.db import Task


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
def _no_env_token(monkeypatch, app):
    # Every account is a named account (no primary/env fallback). Tests run
    # under named account "test".
    monkeypatch.delenv(secrets.ENV_GITHUB_TOKEN, raising=False)
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")


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
    assert body["timeout_minutes"] == 60


def test_create_task_cli_widened(client: FlaskClient, repo_id: int) -> None:
    """The task cli field accepts every registered adapter and rejects unknowns."""
    for cli in ("opencode", "codex", "claude"):
        resp = client.post(
            "/api/tasks",
            json={"repo_id": repo_id, "type": "freeform", "prompt": "do it", "cli": cli},
        )
        assert resp.status_code == 201, f"{cli} should be accepted"
        assert resp.get_json()["cli"] == cli
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "do it", "cli": "gemini"},
    )
    assert resp.status_code == 400
    assert "unsupported agent cli" in resp.get_json()["error"]


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
        pat_name="test",
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
    assert resp.get_json()["timeout_minutes"] == 60


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


def test_rerun_resets_exhausted_retry_budget(
    client: FlaskClient, repo_id: int, session
) -> None:
    """A task that hit the attempt cap gets a fresh recovery budget on rerun —
    otherwise its next failure gives up immediately with zero auto-recovery."""
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    task.retry_count = 3
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun")
    assert resp.status_code == 200
    assert resp.get_json()["retry_count"] == 0


def test_rerun_with_cli_override(client: FlaskClient, repo_id: int, session) -> None:
    """Rerun can override the task's backend CLI."""
    task_id = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x", "cli": "opencode"}
    ).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"cli": "codex"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["cli"] == "codex"


def test_rerun_with_model_override(client: FlaskClient, repo_id: int, session) -> None:
    """Rerun can override the task's model."""
    task_id = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x", "model": "m1"}
    ).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"model": "m2"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["model"] == "m2"


def test_rerun_with_model_clear(client: FlaskClient, repo_id: int, session) -> None:
    """An explicit null model clears a previously pinned model back to default."""
    task_id = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x", "model": "m1"}
    ).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"model": None})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["model"] is None


def test_rerun_backend_change_can_clear_incompatible_model(
    client: FlaskClient, repo_id: int, session
) -> None:
    """Changing backend while leaving model at default clears the old pin."""
    task_id = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "prompt": "x",
            "cli": "opencode",
            "model": "anthropic/claude-3-7-sonnet",
        },
    ).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"cli": "codex", "model": None})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["cli"] == "codex"
    assert body["model"] is None


def test_rerun_with_invalid_cli(client: FlaskClient, repo_id: int, session) -> None:
    """Rerun rejects an unsupported CLI with a 400."""
    task_id = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x"}
    ).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"cli": "nonexistent"})
    assert resp.status_code == 400
    assert "unsupported agent cli" in resp.get_json()["error"]


def test_rerun_with_cli_clear(client: FlaskClient, repo_id: int, session) -> None:
    """An explicit null cli clears a pinned backend back to the default."""
    task_id = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x", "cli": "opencode"}
    ).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"cli": None})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["cli"] is None


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


def test_create_freeform_with_linked_pr_fetches_pr_context(
    app, client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    """A freeform task that links a PR gets PR context + review comments fetched
    and stored (masked), so build_agent_md can embed them.

    Auth contract: the context fetch must run as the EXPLICITLY selected
    account (``pat_name: "acct-a"`` → token ``ghp_a``), never the repo's bound
    account (``test`` → ``ghp_test``) or any fallback.
    """
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "acct-a", "ghp_a")

    seen_tokens: list[str] = []

    class FakeClient:
        def __init__(self, token):
            seen_tokens.append(token)

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
            return [
                {
                    "id": 1,
                    "body": "needs tests",
                    "user": "carol",
                    "state": "COMMENTED",
                    "submitted_at": "x",
                }
            ]

        def close(self): ...

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeClient)
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "fix the issues in this PR",
            "pr_number": 7,
            "pat_name": "acct-a",
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["prs"] == [7]
    # The selected account's token drove the GitHub context fetch (the repo's
    # bound account is "test"/ghp_test — it must NOT be the one used).
    assert seen_tokens == ["ghp_a"]

    row = session.get(Task, body["id"])
    assert row is not None and row.context_json is not None
    ctx = json.loads(row.context_json)
    assert ctx["prs"][0]["number"] == 7
    assert ctx["prs"][0]["body"] == "PR body"
    assert ctx["prs"][0]["head"] == "feature"
    assert ctx["prs"][0]["reviews"] == [{"author": "carol", "body": "needs tests"}]


def test_create_freeform_linked_pr_truncates_oversized_reviews(
    app, client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    """An oversized review comment is truncated (per-comment and total caps) so
    it can't balloon the worktree AGENTS.md / model context."""
    from jalebi.routes.tasks import MAX_REVIEW_CHARS

    secrets.add_github_token(app.config["JALEBI_CONFIG"], "acct-a", "ghp_a")

    class FakeClient:
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
            return [
                {
                    "id": 1,
                    "body": "x" * 20_000,
                    "user": "carol",
                    "state": "COMMENTED",
                    "submitted_at": "t",
                }
            ]

        def close(self): ...

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeClient)
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "fix the issues",
            "pr_number": 7,
            "pat_name": "acct-a",
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    row = session.get(Task, body["id"])
    ctx = json.loads(row.context_json)
    embedded = ctx["prs"][0]["reviews"][0]["body"]
    assert len(embedded) <= MAX_REVIEW_CHARS + 100
    assert "review truncated" in embedded


def test_create_task_with_env_vars(client: FlaskClient, repo_id: int) -> None:
    """A task stores its selected env-var names; the API reflects them."""
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "x",
            "env_vars": ["DATABASE_URL", "API_KEY"],
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["env_vars"] == ["DATABASE_URL", "API_KEY"]


def test_create_task_rejects_bad_env_vars(client: FlaskClient, repo_id: int) -> None:
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x", "env_vars": "not-a-list"},
    )
    assert resp.status_code == 400


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


def test_delete_task_cascade_handles_all_children(client, session, repo_id: int) -> None:
    """Deleting a task removes every child row (Followup, ReviewAssignment,
    Artifact, Run). Locks the Step-45/L14 single-source cascade in place —
    if a future change adds a new child table to ``db.py`` without updating
    ``tasks.delete_tasks_cascade``, this test trips (the new row is left
    orphaned under the deleted task)."""
    from sqlalchemy import select

    from jalebi import catalog
    from jalebi.db import Artifact, Followup, ReviewAssignment, Run, Task, now

    catalog.create_agent(
        session, id="auditor-cascade", name="A", kind="reviewer",
        personality_md="x", enabled=True,
    )
    task_id = client.post(
        "/api/tasks", json={"repo_id": repo_id, "prompt": "x"}
    ).get_json()["id"]
    run = Run(
        task_id=task_id, seq=1, session_id="s1", status="running",
        started_at=now(),
    )
    session.add(run)
    session.commit()
    session.add(Followup(task_id=task_id, run_id=run.id, body="more"))
    session.add(Artifact(run_id=run.id, path="a.txt", size=1))
    session.add(ReviewAssignment(
        task_id=task_id, agent_id="auditor-cascade", pr_number=1,
        repo_id=repo_id, status="queued", created_at=now(),
    ))
    session.commit()

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200

    assert session.get(Task, task_id) is None
    assert session.execute(
        select(Run).where(Run.id == run.id)
    ).scalar_one_or_none() is None
    assert session.execute(
        select(Followup).where(Followup.task_id == task_id)
    ).scalars().all() == []
    assert session.execute(
        select(Artifact).where(Artifact.run_id == run.id)
    ).scalars().all() == []
    assert session.execute(
        select(ReviewAssignment).where(ReviewAssignment.task_id == task_id)
    ).scalars().all() == []


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


def test_create_task_requires_account(app, client, session) -> None:
    """A task without any account is a config error — there is no default."""
    from jalebi import repos

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name=None,
    )
    resp = client.post(
        "/api/tasks",
        json={"repo_id": row.id, "type": "freeform", "prompt": "do it"},
    )
    assert resp.status_code == 400
    assert "account" in resp.get_json()["error"]


def test_create_task_rejects_unknown_pat(app, client, session) -> None:
    from jalebi import repos

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    resp = client.post(
        "/api/tasks",
        json={"repo_id": row.id, "type": "freeform", "prompt": "do it", "pat_name": "default"},
    )
    assert resp.status_code == 400
    assert "unknown PAT" in resp.get_json()["error"]


def test_followup_uses_task_account_by_default(app, client, session, monkeypatch) -> None:
    from jalebi import repos, tasks

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    from jalebi.db import Run, now

    run = Run(
        task_id=task.id,
        seq=1,
        session_id="ses_1",
        status="done",
        started_at=now(),
        finished_at=now(),
    )
    session.add(run)
    session.commit()
    task.status = "done"
    session.commit()

    q = app.config["JALEBI_QUEUE"]
    enqueued: list = []
    monkeypatch.setattr(
        q,
        "enqueue_followup",
        lambda tid, body, pat_name=None, model=None, cli=None: enqueued.append(
            (tid, body, pat_name)
        ),
    )
    resp = client.post(
        f"/api/tasks/{task.id}/followup",
        json={"prompt": "more"},
    )
    assert resp.status_code == 202
    # No pat_name in the request → the follow-up inherits the task's account.
    assert enqueued == [(task.id, "more", None)]


def test_followup_rejects_unknown_pat(app, client, session, monkeypatch) -> None:
    from jalebi import repos, tasks

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url="https://github.com/owner/repo.git",
        pat_name="test",
    )
    task = tasks.create_task(session, type_="freeform", repo_id=row.id, prompt="do it")
    from jalebi.db import Run, now

    run = Run(
        task_id=task.id,
        seq=1,
        session_id="ses_1",
        status="done",
        started_at=now(),
        finished_at=now(),
    )
    session.add(run)
    session.commit()
    task.status = "done"
    session.commit()

    resp = client.post(
        f"/api/tasks/{task.id}/followup",
        json={"prompt": "more", "pat_name": "default"},
    )
    assert resp.status_code == 400
    assert "unknown PAT" in resp.get_json()["error"]


def test_run_diff_endpoint(client: FlaskClient, session) -> None:
    from jalebi import tasks as tasks_svc
    from jalebi.db import Run, now

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/diffrepo",
        default_branch="main",
        clone_url="https://github.com/owner/diffrepo.git",
        pat_name="test",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")
    run = Run(task_id=task.id, seq=1, status="done", started_at=now(), diff_text="+a\n-b\n")
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
    no_diff = Run(task_id=other.id, seq=1, status="done", started_at=now())
    session.add(no_diff)
    session.commit()
    resp = client.get(f"/api/tasks/{other.id}/runs/{no_diff.id}/diff")
    assert resp.status_code == 200
    assert resp.get_json()["diff"] == ""

    # run dict exposes has_diff (and does not ship the raw diff text)
    detail = client.get(f"/api/tasks/{task.id}").get_json()
    assert detail["run"]["has_diff"] is True
    assert "diff_text" not in detail["run"]
    # waiting_input is always present (derived, read-time) on run + task dicts
    assert "waiting_input" in detail
    assert "waiting_input" in detail["run"]
    assert detail["waiting_input"] is False  # no steps → no waiting signal


def test_task_detail_exposes_waiting_input_true(client: FlaskClient, session) -> None:
    from jalebi import clock
    from jalebi import tasks as tasks_svc
    from jalebi.db import Run, now

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/waiting",
        default_branch="main",
        clone_url="https://github.com/owner/waiting.git",
        pat_name="test",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")
    task.status = "done"
    run = Run(
        task_id=task.id,
        seq=1,
        status="done",
        started_at=now(),
        finished_at=now(),
        steps_json=json.dumps(
            [
                {
                    "type": "message",
                    "text": "I have a plan; **waiting for explicit approval** before proceeding.",
                    "ts": clock.to_iso(now()),
                }
            ]
        ),
    )
    session.add(run)
    session.commit()

    detail = client.get(f"/api/tasks/{task.id}").get_json()
    assert detail["waiting_input"] is True
    assert detail["run"]["waiting_input"] is True


def test_task_detail_waiting_input_false_for_normal_done_run(
    client: FlaskClient, session
) -> None:
    from jalebi import clock
    from jalebi import tasks as tasks_svc
    from jalebi.db import Run, now

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/normal",
        default_branch="main",
        clone_url="https://github.com/owner/normal.git",
        pat_name="test",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")
    task.status = "done"
    run = Run(
        task_id=task.id,
        seq=1,
        status="done",
        started_at=now(),
        finished_at=now(),
        steps_json=json.dumps(
            [{"type": "message", "text": "All done.", "ts": clock.to_iso(now())}]
        ),
    )
    session.add(run)
    session.commit()

    detail = client.get(f"/api/tasks/{task.id}").get_json()
    assert detail["waiting_input"] is False
    assert detail["run"]["waiting_input"] is False


def test_task_detail_waiting_input_false_when_no_run(
    client: FlaskClient, session
) -> None:
    from jalebi import tasks as tasks_svc

    row, _ = repos.upsert_repo(
        session,
        full_name="owner/norun",
        default_branch="main",
        clone_url="https://github.com/owner/norun.git",
        pat_name="test",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")

    detail = client.get(f"/api/tasks/{task.id}").get_json()
    assert detail["run"] is None
    assert detail["waiting_input"] is False


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
        pat_name="test",
    )
    task = tasks_svc.create_task(session, type_="freeform", repo_id=row.id, prompt="x")
    row.connected = False
    session.commit()

    resp = client.get(f"/api/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.get_json()["repo_full_name"] == "owner/disc"


# ---- Phase 4 T1.2 / T1.3 — GET /api/tasks/<id>/diff -------------------------


def _seed_git_remote(tmp_path) -> str:
    """Same helper as test_publish_modes.git_remote, duplicated to keep this
    file's fixture surface small."""
    import subprocess as _sp

    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _sp.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True)
    _sp.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    _sp.run(["git", "-C", str(work), "config", "user.email", "s@e.com"], check=True)
    _sp.run(["git", "-C", str(work), "config", "user.name", "S"], check=True)
    (work / "f.txt").write_text("seed\n")
    _sp.run(["git", "-C", str(work), "add", "f.txt"], check=True)
    _sp.run(["git", "-C", str(work), "commit", "-m", "seed"], check=True)
    _sp.run(["git", "-C", str(work), "push", "-u", "origin", "main"], check=True)
    return str(remote)


def test_live_diff_default_against_target(
    client: FlaskClient, session, tmp_path, app
) -> None:
    """Without ``?base=1`` the live diff falls back to diff_against_target."""
    import subprocess as _sp

    from jalebi import tasks as tasks_svc
    from jalebi.config import Config
    from jalebi.git_workspace import GitWorkspace

    remote = _seed_git_remote(tmp_path)
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url=remote,
        pat_name="test",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x"
    )
    config: Config = app.config["JALEBI_CONFIG"]
    ws = GitWorkspace(config)
    ws.ensure_mirror("owner/repo", remote)
    wt = ws.create_worktree(task.id, "owner/repo", "main")
    _sp.run(
        ["git", "-C", str(wt), "config", "user.email", "a@b.com"], check=True
    )
    _sp.run(["git", "-C", str(wt), "config", "user.name", "A"], check=True)
    (wt / "new.txt").write_text("agent work\n")
    _sp.run(["git", "-C", str(wt), "add", "new.txt"], check=True)
    _sp.run(["git", "-C", str(wt), "commit", "-m", "agent"], check=True)

    resp = client.get(f"/api/tasks/{task.id}/diff")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["base"] is False
    assert body["untracked"] is False
    assert "new.txt" in body["diff"]


def test_live_diff_untracked_param(
    client: FlaskClient, session, tmp_path, app
) -> None:
    """``?untracked=1`` appends a synthetic add-hunk for the untracked file."""

    from jalebi import tasks as tasks_svc
    from jalebi.config import Config
    from jalebi.git_workspace import GitWorkspace

    remote = _seed_git_remote(tmp_path)
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url=remote,
        pat_name="test",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x"
    )
    config: Config = app.config["JALEBI_CONFIG"]
    ws = GitWorkspace(config)
    ws.ensure_mirror("owner/repo", remote)
    wt = ws.create_worktree(task.id, "owner/repo", "main")
    (wt / "scratch.txt").write_text("untracked scratch\n")

    resp = client.get(f"/api/tasks/{task.id}/diff?untracked=1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["untracked"] is True
    assert "+++ b/scratch.txt" in body["diff"]


def test_live_diff_no_worktree_returns_409(client: FlaskClient, session, repo_id) -> None:
    """A task with no worktree yet returns 409 (not 500)."""
    from jalebi import tasks as tasks_svc

    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=repo_id, prompt="x"
    )
    resp = client.get(f"/api/tasks/{task.id}/diff")
    assert resp.status_code == 409


# ---- Phase 4 T1.4 — GET /api/tasks/<id>/merge-check -------------------------


def test_merge_check_clean_and_conflicting(
    client: FlaskClient, session, tmp_path, app
) -> None:
    import subprocess as _sp

    from jalebi import tasks as tasks_svc
    from jalebi.config import Config
    from jalebi.git_workspace import GitWorkspace

    remote = _seed_git_remote(tmp_path)
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url=remote,
        pat_name="test",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x"
    )
    config: Config = app.config["JALEBI_CONFIG"]
    ws = GitWorkspace(config)
    ws.ensure_mirror("owner/repo", remote)
    wt = ws.create_worktree(task.id, "owner/repo", "main")

    # Clean merge: an unrelated file lands on main.
    src2 = tmp_path / "src2"
    _sp.run(["git", "clone", remote, str(src2)], check=True, capture_output=True)
    _sp.run(["git", "-C", str(src2), "config", "user.email", "a@b.com"], check=True)
    _sp.run(["git", "-C", str(src2), "config", "user.name", "A"], check=True)
    (src2 / "other.txt").write_text("target advanced\n")
    _sp.run(["git", "-C", str(src2), "add", "other.txt"], check=True)
    _sp.run(["git", "-C", str(src2), "commit", "-m", "advance"], check=True)
    _sp.run(["git", "-C", str(src2), "push", "origin", "main"], check=True)

    resp = client.get(f"/api/tasks/{task.id}/merge-check")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["conflicts"] == []

    # Conflicting case: same file diverges on both sides.
    (wt / "f.txt").write_text("agent side\n")
    _sp.run(["git", "-C", str(wt), "add", "f.txt"], check=True)
    _sp.run(["git", "-C", str(wt), "commit", "-m", "agent"], check=True)
    (src2 / "f.txt").write_text("remote side\n")
    _sp.run(["git", "-C", str(src2), "add", "f.txt"], check=True)
    _sp.run(["git", "-C", str(src2), "commit", "-m", "remote"], check=True)
    _sp.run(["git", "-C", str(src2), "push", "origin", "main"], check=True)

    resp = client.get(f"/api/tasks/{task.id}/merge-check")
    body = resp.get_json()
    assert body["ok"] is False
    assert {"kind": "content", "path": "f.txt"} in body["conflicts"]


def test_merge_check_missing_task(client: FlaskClient, session) -> None:
    resp = client.get("/api/tasks/9999/merge-check")
    assert resp.status_code == 404


# ---- Phase 4 T2.2 — attention field on task dict ----------------------------


def test_task_dict_exposes_attention_default_off(client: FlaskClient, repo_id) -> None:
    """Without any poller facts seeded, attention falls back to the pure
    task.status derivation."""
    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["attention"] == "working"

    # Detail endpoint likewise.
    detail = client.get(f"/api/tasks/{body['id']}").get_json()
    assert detail["attention"] == "working"


def test_task_dict_attention_from_poller_facts(client: FlaskClient, app, repo_id) -> None:
    """A task whose poller has facts consumes them via ``pr_facts_for_task``."""

    resp = client.post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "x"},
    )
    task_id = resp.get_json()["id"]
    # Manually mark the task as `done` to exercise the terminal-with-facts branch.
    from jalebi.db import Session as DbSession
    from jalebi.db import Task

    session = DbSession()
    try:
        task = session.get(Task, task_id)
        task.status = "done"
        session.commit()
    finally:
        session.close()

    poller = app.config["JALEBI_POLLER"]
    poller.record_facts(
        repo_id,
        task_id,
        pr_number=42,
        facts={
            "ci_state": "success",
            "review_decision": "approved",
            "mergeable": True,
            "last_seen_at": "2026-08-17T00:00:00Z",
        },
    )
    detail = client.get(f"/api/tasks/{task_id}").get_json()
    assert detail["attention"] == "ready_to_merge"


def test_task_dict_attention_working_no_poller(app, repo_id) -> None:
    """With ``JALEBI_POLLER`` missing entirely (no app-key set), the route
    returns the no-PR-facts branch (working for queued)."""

    app.config.pop("JALEBI_POLLER", None)
    # Use the app's test client so the route sees the popped config.
    resp = app.test_client().post(
        "/api/tasks",
        json={"repo_id": repo_id, "type": "freeform", "prompt": "y"},
    )
    body = resp.get_json()
    assert body["attention"] == "working"


# ---- Phase 4 T3.2 — GET /api/tasks/<id>/publish-check -----------------------


def _seed_pr_facts(app, repo_id, task_id, *, ci, review, mergeable):
    """Inject PRFacts via the running poller (default-OFF tests skip the tick)."""
    poller = app.config["JALEBI_POLLER"]
    poller.record_facts(
        repo_id,
        task_id,
        pr_number=99,
        facts={
            "ci_state": ci,
            "review_decision": review,
            "mergeable": mergeable,
            "last_seen_at": "2026-08-17T00:00:00Z",
        },
    )


def _setup_publish_check_task(app, client, session, tmp_path):
    """Reusable fixture for publish-check tests: worktree + 1 commit ahead."""
    import subprocess as _sp

    from jalebi import tasks as tasks_svc
    from jalebi.config import Config
    from jalebi.git_workspace import GitWorkspace

    remote = _seed_git_remote(tmp_path)
    row, _ = repos.upsert_repo(
        session,
        full_name="owner/repo",
        default_branch="main",
        clone_url=remote,
        pat_name="test",
    )
    task = tasks_svc.create_task(
        session, type_="freeform", repo_id=row.id, prompt="x"
    )
    task.status = "done"
    session.commit()
    config: Config = app.config["JALEBI_CONFIG"]
    ws = GitWorkspace(config)
    ws.ensure_mirror("owner/repo", remote)
    wt = ws.create_worktree(task.id, "owner/repo", "main")
    # One commit ahead so the `commits` check passes.
    _sp.run(["git", "-C", str(wt), "config", "user.email", "a@b.com"], check=True)
    _sp.run(["git", "-C", str(wt), "config", "user.name", "A"], check=True)
    (wt / "new.txt").write_text("agent\n")
    _sp.run(["git", "-C", str(wt), "add", "new.txt"], check=True)
    _sp.run(["git", "-C", str(wt), "commit", "-m", "agent"], check=True)
    return task, ws, wt


def test_publish_check_returns_ready(app, session, client, tmp_path) -> None:
    task, _ws, _wt = _setup_publish_check_task(app, client, session, tmp_path)
    _seed_pr_facts(
        app,
        task.repo_id,
        task.id,
        ci="success",
        review="approved",
        mergeable=True,
    )
    resp = client.get(f"/api/tasks/{task.id}/publish-check")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["base_ref"] == "main"
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["branch"]["ok"] is True
    assert by_name["commits"]["ok"] is True
    assert by_name["commits"]["ahead"] == 1
    assert by_name["conflict"]["ok"] is True
    assert by_name["ci"]["ok"] is True
    assert by_name["review"]["ok"] is True
    assert by_name["mergeable"]["ok"] is True


def test_publish_check_blocks_on_branch_mismatch(
    app, session, client, tmp_path
) -> None:
    import subprocess as _sp

    task, _ws, wt = _setup_publish_check_task(app, client, session, tmp_path)
    # Move HEAD off jalebi.
    _sp.run(["git", "-C", str(wt), "checkout", "main"], check=True)
    resp = client.get(f"/api/tasks/{task.id}/publish-check")
    body = resp.get_json()
    assert body["status"] == "blocked"
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["branch"]["ok"] is False
    assert "branch mismatch" in by_name["branch"]["message"]


def test_publish_check_blocks_on_nothing_to_publish(
    app, session, client, tmp_path
) -> None:
    """A task that was published / re-based ahead=0 → blocked (commits ahead = 0)."""
    import subprocess as _sp

    task, _ws, wt = _setup_publish_check_task(app, client, session, tmp_path)
    # Reset the branch to origin/main so HEAD has no unique commits.
    _sp.run(["git", "-C", str(wt), "reset", "--hard", "origin/main"], check=True)
    resp = client.get(f"/api/tasks/{task.id}/publish-check")
    body = resp.get_json()
    assert body["status"] == "blocked"
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["commits"]["ok"] is False
    assert by_name["commits"]["ahead"] == 0


def test_publish_check_blocks_on_predicted_conflict(
    app, session, client, tmp_path
) -> None:
    import subprocess as _sp

    task, _ws, _wt = _setup_publish_check_task(app, client, session, tmp_path)
    # `_setup_publish_check_task` already seeded the bare remote + work clone.
    # Use the same path it created to push a conflicting change to origin/main.
    remote_url = str(tmp_path / "remote.git")
    src2 = tmp_path / "src2_conflict"
    _sp.run(["git", "clone", remote_url, str(src2)], check=True)
    _sp.run(["git", "-C", str(src2), "config", "user.email", "a@b.com"], check=True)
    _sp.run(["git", "-C", str(src2), "config", "user.name", "A"], check=True)
    (src2 / "new.txt").write_text("remote side\n")
    _sp.run(["git", "-C", str(src2), "add", "new.txt"], check=True)
    _sp.run(["git", "-C", str(src2), "commit", "-m", "remote side"], check=True)
    _sp.run(["git", "-C", str(src2), "push", "origin", "main"], check=True)
    resp = client.get(f"/api/tasks/{task.id}/publish-check")
    body = resp.get_json()
    assert body["status"] == "blocked"
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["conflict"]["ok"] is False


def test_publish_check_attention_on_ci_failure(
    app, session, client, tmp_path
) -> None:
    task, _ws, _wt = _setup_publish_check_task(app, client, session, tmp_path)
    _seed_pr_facts(
        app,
        task.repo_id,
        task.id,
        ci="failure",
        review="approved",
        mergeable=True,
    )
    resp = client.get(f"/api/tasks/{task.id}/publish-check")
    body = resp.get_json()
    assert body["status"] == "attention"
    by_name = {c["name"]: c for c in body["checks"]}
    assert by_name["ci"]["ok"] is False
    assert by_name["branch"]["ok"] is True  # branch is fine


def test_publish_check_404_when_task_missing(client: FlaskClient) -> None:
    resp = client.get("/api/tasks/99999/publish-check")
    assert resp.status_code == 404


def test_pr_head_source_helpers() -> None:
    from jalebi import tasks as task_svc

    assert task_svc.pr_head_source_number("pr/7/head") == 7
    assert task_svc.pr_head_source_number("main") is None
    assert task_svc.is_pr_head_source("pr/7/head") is True
    assert task_svc.is_pr_head_source("main") is False


def _fake_pr_client(monkeypatch, *, base="main"):
    class FakeClient:
        def __init__(self, token): ...

        def get_pr(self, full_name, number):
            return {
                "number": number,
                "title": "PR title",
                "body": "PR body",
                "html_url": "u",
                "state": "open",
                "base": base,
                "head": "feat/x",
                "head_repo": "fork/repo",
                "is_fork": True,
                "author": "bob",
            }

        def list_pr_reviews(self, full_name, number):
            return []

        def close(self): ...

    monkeypatch.setattr("jalebi.routes.tasks.GitHubClient", FakeClient)


def test_create_freeform_pr_head_source_sets_target_to_pr_base(
    client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    _fake_pr_client(monkeypatch, base="develop")
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "address reviews",
            "pr_number": 7,
            "source_branch": "pr/7/head",
        },
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["source_branch"] == "pr/7/head"
    assert body["target_branch"] == "develop"
    assert body["prs"] == [7]


def test_create_pr_head_source_must_match_pr_number(
    client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    _fake_pr_client(monkeypatch)
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "address reviews",
            "pr_number": 8,
            "source_branch": "pr/7/head",
        },
    )
    assert resp.status_code == 400
    assert "must match" in resp.get_json()["error"]


def test_create_pr_head_source_rejected_for_issue_fix(
    client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    _fake_pr_client(monkeypatch)
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "issue_fix",
            "prompt": "fix it",
            "issue_number": 3,
            "pr_number": 7,
            "source_branch": "pr/7/head",
        },
    )
    assert resp.status_code == 400
    assert "freeform" in resp.get_json()["error"]


def test_create_task_rejects_garbage_pr_number(
    client: FlaskClient, repo_id: int, session, monkeypatch
) -> None:
    """F8: a non-numeric pr_number is a 400, never an unhandled 500."""
    _fake_pr_client(monkeypatch)
    for bad in ("abc", "7.5", 3.5, True):
        resp = client.post(
            "/api/tasks",
            json={
                "repo_id": repo_id,
                "type": "freeform",
                "prompt": "x",
                "pr_number": bad,
                "source_branch": "pr/7/head",
            },
        )
        assert resp.status_code == 400, bad
        assert "integer" in resp.get_json()["error"]
    # Sanity: numeric strings still accepted.
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "freeform",
            "prompt": "x",
            "pr_number": "7",
            "source_branch": "pr/7/head",
        },
    )
    assert resp.status_code == 201


def test_create_task_rejects_garbage_issue_number(
    client: FlaskClient, repo_id: int, session
) -> None:
    """F8: a non-numeric issue_number is a 400, never an unhandled 500."""
    resp = client.post(
        "/api/tasks",
        json={
            "repo_id": repo_id,
            "type": "issue_fix",
            "prompt": "x",
            "issue_number": "abc",
        },
    )
    assert resp.status_code == 400
    assert "integer" in resp.get_json()["error"]


def test_cancel_task_allows_needs_approval(
    client: FlaskClient, repo_id: int, session
) -> None:
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_id, prompt="test cancel"
    )
    task.status = "needs_approval"
    session.commit()

    resp = client.post(f"/api/tasks/{task.id}/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"

    task_resp = client.get(f"/api/tasks/{task.id}")
    assert task_resp.status_code == 200
    assert task_resp.get_json()["status"] == "cancelled"
    assert task_resp.get_json()["attention"] == "done"


def test_dismiss_attention_endpoint(
    client: FlaskClient, repo_id: int, session
) -> None:
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_id, prompt="test dismiss"
    )
    task.status = "failed"
    session.commit()

    # Before dismissing, failed terminal without PR facts evaluates to needs_you
    task_resp = client.get(f"/api/tasks/{task.id}")
    assert task_resp.get_json()["attention"] == "needs_you"

    # Dismiss attention
    resp = client.post(f"/api/tasks/{task.id}/dismiss-attention")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["attention"] == "done"

    # Confirm subsequent GET also returns done
    task_resp2 = client.get(f"/api/tasks/{task.id}")
    assert task_resp2.get_json()["attention"] == "done"


def test_clear_attention_dismissal_helper(
    client: FlaskClient, repo_id: int, session
) -> None:
    """clear_attention_dismissal removes the flag (True once, False after)."""
    from jalebi import attention as attention_mod

    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_id, prompt="test rearm"
    )
    task.status = "failed"
    session.commit()

    assert tasks.clear_attention_dismissal(session, task) is False
    tasks.dismiss_task_attention(session, task.id)
    assert attention_mod._is_attention_dismissed(task) is True
    assert tasks.clear_attention_dismissal(session, task) is True
    session.commit()
    assert attention_mod._is_attention_dismissed(task) is False
    assert tasks.clear_attention_dismissal(session, task) is False


def test_prepare_run_rearms_dismissed_attention(
    client: FlaskClient, repo_id: int, session, app
) -> None:
    """A new run (rerun/follow-up path via _prepare_run) clears a prior
    dismissal so the new run's attention is live again."""
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_id, prompt="test rearm run"
    )
    task.status = "failed"
    session.commit()

    resp = client.post(f"/api/tasks/{task.id}/dismiss-attention")
    assert resp.status_code == 200
    assert resp.get_json()["attention"] == "done"

    # The route commits in its own session; refresh so this session sees it
    # (mirrors the worker, which always loads the task fresh).
    session.refresh(task)
    queue = app.config["JALEBI_QUEUE"]
    queue._prepare_run(session, task, cli="opencode")
    task_resp = client.get(f"/api/tasks/{task.id}")
    # Running with no PR facts → working (NOT stuck at dismissed done).
    assert task_resp.get_json()["attention"] == "working"


def test_create_rejects_non_string_model(client: FlaskClient, repo_id: int) -> None:
    """A non-string model (e.g. a stale object from a form bug) is a 400."""
    resp = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x", "model": 5})
    assert resp.status_code == 400


def test_followup_rejects_non_string_model(client: FlaskClient, repo_id: int, session) -> None:
    """Follow-up model overrides must be strings."""
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/followup", json={"prompt": "y", "model": []})
    assert resp.status_code == 400


def test_rerun_rejects_non_string_model(client: FlaskClient, repo_id: int, session) -> None:
    """Rerun model overrides must be strings (explicit null still clears)."""
    task_id = client.post("/api/tasks", json={"repo_id": repo_id, "prompt": "x"}).get_json()["id"]
    from jalebi import tasks as tasks_svc
    task = tasks_svc.get_task(session, task_id)
    task.status = "failed"
    session.commit()
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"model": {}})
    assert resp.status_code == 400
    resp = client.post(f"/api/tasks/{task_id}/rerun", json={"model": None})
    assert resp.status_code == 200


def test_task_review_nitpick_mode_serialized(client: FlaskClient, repo_id: int, session) -> None:
    """Tasks serialize review_nitpick_mode from their context_json when present."""
    task = tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_id,
        prompt="review",
        prs=[1],
        context={"prs": [{"number": 1}], "review_nitpick_mode": False},
    )
    resp = client.get(f"/api/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.get_json()["review_nitpick_mode"] is False

    task2 = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_id,
        prompt="do work",
    )
    resp2 = client.get(f"/api/tasks/{task2.id}")
    assert resp2.status_code == 200
    assert resp2.get_json()["review_nitpick_mode"] is None

