"""Hardening tests: recovery, settings validation, masking, worker pool, publish
error paths, and live-behavior fixes (PRD F3/F16/F17)."""

import json
import subprocess
import time

import pytest

from jalebi import repos, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.db import Run, utcnow
from jalebi.git_workspace import GitWorkspace

FULL_NAME = "owner/repo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def __init__(self):
        self.pid = 424242

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class FakeHandle:
    def __init__(self, events, session_id="ses_fake"):
        self._events = list(events)
        self.session_id = session_id
        self.proc = FakeProc()

    def events(self):
        yield from self._events


class BlockingHandle(FakeHandle):
    def events(self):
        yield AgentEvent(type="message", text="working")
        while self.proc.poll() is None and not getattr(self, "killed", False):
            time.sleep(0.02)
        yield AgentEvent(type="error", text="killed")


class FakeGitHubClient:
    def __init__(self, token: str):
        self.token = token

    def find_pr_by_head(self, full_name, head) -> int | None:
        return None

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        return 42

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr(
        "jalebi.queue.secrets.load_github_token", lambda config: "ghp_test"
    )


@pytest.fixture
def git_remote(tmp_path) -> str:
    remote = tmp_path / "remote.git"
    src = tmp_path / "src"
    _git(["init", "--bare", str(remote)])
    _git(["init", str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "file.txt").write_text("hello\n")
    _git(["-C", str(src), "add", "file.txt"])
    _git(["-C", str(src), "commit", "-m", "initial"])
    _git(["-C", str(src), "branch", "-M", "main"])
    _git(["-C", str(src), "remote", "add", "origin", str(remote)])
    _git(["-C", str(src), "push", "-u", "origin", "main"])
    _git(["-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])
    return str(remote)


@pytest.fixture
def repo_row(session, git_remote):
    row, _ = repos.upsert_repo(
        session, full_name=FULL_NAME, default_branch="main", clone_url=git_remote
    )
    return row


@pytest.fixture
def q(app):
    return app.config["JALEBI_QUEUE"]


def _install_adapter(monkeypatch, handle) -> None:
    class FakeAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            return handle

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: FakeAdapter())


def _no_publish(session) -> None:
    settings.set_setting(session, "auto_publish", False)


def _seed_commit(q, task_id: int, clone_url: str) -> None:
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, clone_url)
    wt = git.create_worktree(task_id, FULL_NAME, "main")
    (wt / "f.txt").write_text("changed\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "f.txt"])
    _git(["-C", str(wt), "commit", "-m", "change"])


# -- restart recovery -----------------------------------------------------


def test_recover_marks_interrupted_and_kills_orphan(q, session, repo_row) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    run = Run(
        task_id=task.id,
        seq=1,
        status="running",
        pid=99999999,  # nonexistent pid -> _kill_pid is a safe no-op
        started_at=utcnow(),
    )
    session.add(run)
    task.status = "running"
    session.commit()

    queued = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="q it")
    queued.status = "queued"
    session.commit()

    recovered = q.recover()
    assert recovered >= 2
    session.expire_all()
    fresh_task = tasks.get_task(session, task.id)
    fresh_queued = tasks.get_task(session, queued.id)
    fresh_run = tasks.latest_run(session, task.id)
    assert fresh_task is not None and fresh_queued is not None and fresh_run is not None
    assert fresh_task.status == "interrupted"
    assert fresh_queued.status == "queued"
    assert fresh_run.status == "interrupted"
    assert fresh_run.finished_at is not None


# -- worker pool entry + live concurrency ---------------------------------


def test_worker_pool_processes_enqueued_task(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q.start(2)
    try:
        q.enqueue(task.id)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            session.expire_all()
            t = tasks.get_task(session, task.id)
            if t is not None and t.status == "done":
                break
            time.sleep(0.1)
        fresh = tasks.get_task(session, task.id)
        assert fresh is not None and fresh.status == "done"
    finally:
        q.stop()


def test_set_concurrency_resizes_pool(q) -> None:
    q.start(2)
    assert q._live_workers() == 2
    assert q.set_concurrency(4) == 4
    assert q.set_concurrency(1) == 1
    q.stop()
    assert q._live_workers() == 0


def test_set_concurrency_zero_pauses_and_resumes(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    q.start(0)
    assert q._live_workers() == 0
    assert q.set_concurrency(1) == 1
    try:
        q.enqueue(task.id)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            session.expire_all()
            t = tasks.get_task(session, task.id)
            if t is not None and t.status == "done":
                break
            time.sleep(0.1)
        fresh = tasks.get_task(session, task.id)
        assert fresh is not None and fresh.status == "done"
    finally:
        q.stop()


# -- manual publish + 502 -------------------------------------------------


def test_manual_publish_success(q, session, repo_row, monkeypatch) -> None:
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row.clone_url)
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    pr_number = q.publish_task(task.id)
    assert pr_number == 42
    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"
    assert fresh.pr_number == 42


def test_publish_route_502_when_github_fails(app, session, repo_row, monkeypatch) -> None:
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(app.config["JALEBI_QUEUE"], task.id, repo_row.clone_url)

    class BoomGitHub(FakeGitHubClient):
        def find_pr_by_head(self, full_name, head) -> int | None:
            raise RuntimeError("github down")

        def create_pr(self, full_name, *, title, body, head, base) -> int:
            raise RuntimeError("github down")

    monkeypatch.setattr("jalebi.queue.GitHubClient", BoomGitHub)
    client = app.test_client()
    resp = client.post(f"/api/tasks/{task.id}/publish")
    assert resp.status_code == 502
    assert "github down" in resp.get_json()["error"]


# -- github-unreachable 502 arms ------------------------------------------


def test_github_status_502_when_unreachable(app, monkeypatch) -> None:
    import httpx

    class BrokenClient:
        def __init__(self, token):
            pass

        def validate_token(self):
            raise httpx.ConnectError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr("jalebi.routes.github.GitHubClient", BrokenClient)
    resp = app.test_client().get("/api/github/status")
    assert resp.status_code == 502


def test_github_repos_502_when_unreachable(app, monkeypatch) -> None:
    import httpx

    from jalebi import secrets

    secrets.store_secret(app.config["JALEBI_CONFIG"], secrets.GITHUB_TOKEN_KEY, "ghp_test")

    class BrokenClient:
        def __init__(self, token):
            pass

        def list_repos(self):
            raise httpx.ConnectError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr("jalebi.routes.github.GitHubClient", BrokenClient)
    resp = app.test_client().get("/api/github/repos")
    # A failing account degrades gracefully: 200 with an error entry per account.
    assert resp.status_code == 200
    body = resp.get_json()
    assert any("error" in repo for repo in body)
    assert "connection refused" in body[0]["error"]


# -- settings validation ---------------------------------------------------


def test_settings_reject_invalid_values(app) -> None:
    client = app.test_client()
    for key, value in [
        ("auto_publish", "false"),
        ("concurrency", "4"),
        ("concurrency", -1),
        ("default_timeout_minutes", 0),
        ("secret_patterns", "not-a-list"),
        ("retry_policy", {"auto_retry": "yes"}),
        ("agent_cli", "codex"),
    ]:
        resp = client.post("/api/settings", json={"key": key, "value": value})
        assert resp.status_code == 400, f"{key} should be rejected"


def test_settings_unknown_key_rejected(app) -> None:
    resp = app.test_client().post("/api/settings", json={"key": "nope", "value": 1})
    assert resp.status_code == 400


def test_settings_concurrency_updates_pool(app) -> None:
    client = app.test_client()
    q = app.config["JALEBI_QUEUE"]
    q.start(2)
    try:
        resp = client.post("/api/settings", json={"key": "concurrency", "value": 1})
        assert resp.status_code == 200
        assert q._live_workers() == 1
    finally:
        q.stop()


# -- default timeout + masking with patterns ------------------------------


def test_create_task_uses_default_timeout_setting(app, session, repo_row) -> None:
    settings.set_setting(session, "default_timeout_minutes", 7)
    resp = app.test_client().post(
        "/api/tasks",
        json={"repo_id": repo_row.id, "type": "freeform", "prompt": "do it"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["timeout_minutes"] == 7


def test_prompt_masked_with_secret_patterns(app, session, repo_row) -> None:
    settings.set_setting(session, "secret_patterns", ["AKIA[0-9A-Z]{16}"])
    resp = app.test_client().post(
        "/api/tasks",
        json={
            "repo_id": repo_row.id,
            "type": "freeform",
            "prompt": "rotate AKIA0123456789ABCDEF now",
        },
    )
    assert resp.status_code == 201
    assert "AKIA0123456789ABCDEF" not in resp.get_json()["prompt"]
    assert "***" in resp.get_json()["prompt"]


# -- tool_call persistence -------------------------------------------------


def test_tool_call_events_persisted(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="working"),
                AgentEvent(type="tool_call", data={"tool": "edit", "status": "done"}),
                AgentEvent(type="done"),
            ]
        ),
    )
    q._run_task(task.id)
    session.expire_all()
    run = tasks.latest_run(session, task.id)
    assert run is not None
    types = [s["type"] for s in json.loads(run.steps_json or "[]")]
    assert types == ["message", "tool_call", "done"]


# -- auto-retry ------------------------------------------------------------


def test_auto_retry_failed_task_once(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    settings.set_setting(session, "retry_policy", {"auto_retry": True})
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    calls = {"n": 0}

    class FlakyAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeHandle([AgentEvent(type="error", text="boom")])
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: FlakyAdapter())

    # First run fails -> auto-retry enqueues a second run.
    q._run_task(task.id)
    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "queued"
    assert fresh.retry_count == 1

    q._run_task(task.id)
    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"
    assert len(tasks.runs_for_task(session, task.id)) == 2
