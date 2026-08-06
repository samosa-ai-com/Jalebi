import json
import subprocess
import threading
import time

import pytest

from jalebi import repos, settings, tasks
from jalebi.adapters.types import AgentEvent

FULL_NAME = "owner/repo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return 0


class FakeHandle:
    def __init__(self, events, session_id: str = "ses_fake"):
        self._events = list(events)
        self.session_id = session_id
        self.proc = FakeProc()

    def events(self):
        yield from self._events


class BlockingHandle(FakeHandle):
    def __init__(self, events=None, session_id: str = "ses_fake"):
        super().__init__(events or [], session_id)

    def events(self):
        yield AgentEvent(type="message", text="working")
        while not self.proc.killed:
            time.sleep(0.02)
        yield AgentEvent(type="error", text="killed")


class FakeGitHubClient:
    def __init__(self, token: str):
        self.token = token

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        return 42

    def close(self) -> None:
        pass


class FailingGitHubClient(FakeGitHubClient):
    def create_pr(self, full_name, *, title, body, head, base) -> int:
        raise RuntimeError("PR create failed")


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


def _fresh_task(session, task_id):
    session.expire_all()
    task = tasks.get_task(session, task_id)
    assert task is not None
    return task


def _latest_run(session, task_id):
    run = tasks.latest_run(session, task_id)
    assert run is not None
    return run


def test_success_marks_done_and_persists_run(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle([AgentEvent(type="message", text="working"), AgentEvent(type="done")]),
    )
    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    run = _latest_run(session, task.id)
    assert run.status == "done"
    assert run.session_id == "ses_fake"
    step_types = [s["type"] for s in json.loads(run.steps_json or "[]")]
    assert step_types == ["message", "done"]


def test_failure_marks_failed(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="oops"),
                AgentEvent(type="error", text="boom"),
            ]
        ),
    )
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "failed"
    assert _latest_run(session, task.id).status == "failed"


def test_masking_applied_to_stored_steps(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="the token is ghp_test leaked"),
                AgentEvent(type="done"),
            ]
        ),
    )
    q._run_task(task.id)

    run = _latest_run(session, task.id)
    steps_json = json.dumps(json.loads(run.steps_json or "[]"))
    assert "ghp_test" not in steps_json
    assert "***" in steps_json


def test_timeout_marks_timed_out(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="do it", timeout_minutes=0
    )
    _install_adapter(monkeypatch, BlockingHandle())
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "timed_out"
    assert _latest_run(session, task.id).status == "timed_out"


def test_cancel_running_task(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, BlockingHandle())

    thread = threading.Thread(target=q._run_task, args=(task.id,))
    thread.start()
    time.sleep(0.3)
    assert q.cancel(task.id) is True
    thread.join(timeout=5)

    assert _fresh_task(session, task.id).status == "cancelled"
    assert _latest_run(session, task.id).status == "cancelled"


def test_cancelled_queued_task_is_skipped(q, session, repo_row, monkeypatch) -> None:
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    task.status = "cancelled"
    session.commit()

    class MustNotRun:
        def start(self, *args, **kwargs):
            raise AssertionError("adapter must not run for cancelled task")

        def resume(self, *args, **kwargs):
            raise AssertionError("adapter must not run for cancelled task")

        def list_models(self):
            raise AssertionError("adapter must not run for cancelled task")

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: MustNotRun())
    q._run_task(task.id)
    assert _fresh_task(session, task.id).status == "cancelled"


def test_publish_opens_pr_and_sets_number(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(
        session, type_="issue_fix", repo_id=repo_row.id, prompt="fix issue #12"
    )
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 42
    refs = _git(["-C", repo_row.clone_url, "show-ref", "--heads"]).splitlines()
    assert any(f"refs/heads/jalebi/{task.id}" in line for line in refs)


def test_publish_failure_sets_needs_approval(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FailingGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "needs_approval"
    assert _latest_run(session, task.id).status == "done"


def test_exception_finalizes_run_failed(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class ExplodingAdapter:
        def start(self, *args, **kwargs):
            raise RuntimeError("boom")

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: ExplodingAdapter())
    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "failed"
    run = _latest_run(session, task.id)
    assert run.status == "failed"
    assert run.finished_at is not None
