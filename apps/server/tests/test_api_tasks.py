import json

import pytest
from flask.testing import FlaskClient

from jalebi import repos, secrets
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
