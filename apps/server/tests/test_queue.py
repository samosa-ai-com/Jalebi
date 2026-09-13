import json
import subprocess
import threading
import time

import pytest

from jalebi import repos, secrets, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.git_workspace import GitWorkspace

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
        self.find_pr_calls = 0

    def find_pr_by_head(self, full_name, head) -> int | None:
        self.find_pr_calls += 1
        return None

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        return 42

    def get_pr(self, full_name, number) -> dict:
        return {"number": number, "state": "open"}

    def close(self) -> None:
        pass


class FailingGitHubClient(FakeGitHubClient):
    def create_pr(self, full_name, *, title, body, head, base) -> int:
        raise RuntimeError("PR create failed")


class ReusingGitHubClient(FakeGitHubClient):
    def __init__(self, token: str):
        super().__init__(token)
        self.create_pr_calls = 0

    def find_pr_by_head(self, full_name, head) -> int | None:
        self.find_pr_calls += 1
        return 99

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        self.create_pr_calls += 1
        raise AssertionError("create_pr must not be called when a PR exists")


class ClosedPrGitHubClient(FakeGitHubClient):
    """find_pr_by_head returns a PR number, but that PR is closed — the publish
    must NOT reuse it and must fall through to create_pr."""

    def __init__(self, token: str):
        super().__init__(token)
        self.create_pr_calls = 0

    def find_pr_by_head(self, full_name, head) -> int | None:
        self.find_pr_calls += 1
        return 99

    def get_pr(self, full_name, number) -> dict:
        return {"number": number, "state": "closed"}

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        self.create_pr_calls += 1
        return 42


class StaleRecordedPrGitHubClient(FakeGitHubClient):
    """The task already has a recorded pr_number, but that PR is closed and no
    other open PR exists for the head — publish must create a fresh PR."""

    def __init__(self, token: str):
        super().__init__(token)
        self.create_pr_calls = 0
        self.get_pr_calls = []

    def get_pr(self, full_name, number) -> dict:
        self.get_pr_calls.append(number)
        return {"number": number, "state": "closed"}

    def find_pr_by_head(self, full_name, head) -> int | None:
        self.find_pr_calls += 1
        return None

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        self.create_pr_calls += 1
        return 42


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
    # Every account is a named account (no primary). Tests run under account
    # "test" whose token is ghp_test.
    secrets.add_github_token(config, "test", "ghp_test")


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
        session, full_name=FULL_NAME, default_branch="main", clone_url=git_remote, pat_name="test"
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
    # Auto-recovery ships ON; failure-path tests must opt out or a failing run
    # would silently re-enqueue. Recovery tests re-enable it after this.
    settings.set_setting(session, "retry_policy", {"auto_retry": False})


def _seed_commit(q, task_id: int, repo_row) -> None:
    """Create the worktree and add a commit so the branch is ahead of main."""
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task_id, FULL_NAME, "main")
    (wt / "f.txt").write_text("changed\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "f.txt"])
    _git(["-C", str(wt), "commit", "-m", "change"])


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
    # Robust: the message is present and 'done' is terminal — the exact prefix
    # list is an implementation detail (T-15).
    assert "message" in step_types
    assert step_types[-1] == "done"


def test_terminal_notification_sent_on_done(q, session, repo_row, monkeypatch) -> None:
    """A done run sends an ntfy notification with the final agent message when
    notify_on_done is on and an ntfy topic is configured."""
    settings.set_setting(session, "ntfy_topic", "room")
    settings.set_setting(session, "notify_on_done", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle([AgentEvent(type="message", text="finished the work"), AgentEvent(type="done")]),
    )

    sent: list[dict] = []

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "json": json})
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert sent, "expected a notification to be sent"
    body = sent[0]["json"]
    assert "Task #" in body["title"]
    assert "finished the work" in body["message"]
    assert sent[0]["url"] == "https://ntfy.sh"  # JSON publishing → server root
    assert body["topic"] == "room"
    assert body["markdown"] is True
    assert body["click"] == f"http://127.0.0.1:{q.config.port}/tasks/{task.id}"


def test_terminal_notification_skipped_when_topic_unset(q, session, repo_row, monkeypatch) -> None:
    """No notification is attempted when no ntfy topic is configured."""
    settings.set_setting(session, "ntfy_topic", "")
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    called = False

    def fake_post(*a, **k):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert called is False


def test_terminal_notification_masks_env_var_values(
    q, session, repo_row, monkeypatch
) -> None:
    """An env-var value echoed by the agent in its final message must be masked
    in the ntfy push (regression: notification masker must include env vars)."""
    from jalebi import envvars

    settings.set_setting(session, "ntfy_topic", "room")
    settings.set_setting(session, "notify_on_done", True)
    envvars.upsert_env_var(session, name="API_KEY", value="ghp_echoed_secret", repo_id=None)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        env_vars=["API_KEY"],
    )
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="done, key is ghp_echoed_secret"),
                AgentEvent(type="done"),
            ]
        ),
    )

    sent: list[dict] = []

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "json": json})
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert sent
    body = sent[0]["json"]
    assert "ghp_echoed_secret" not in str(body)
    assert "***" in body["message"]


def test_failure_notification_gated_by_toggle(q, session, repo_row, monkeypatch) -> None:
    """A failed run does NOT notify when notify_on_failed is off."""
    _no_publish(session)
    settings.set_setting(session, "ntfy_topic", "room")
    settings.set_setting(session, "notify_on_failed", False)
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

    called = False

    def fake_post(*a, **k):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "failed"
    assert called is False


def test_progress_notification_fires_on_interval(q, session, repo_row, monkeypatch) -> None:
    """A still-running task gets a progress ping at the configured interval with
    the latest agent message."""
    from jalebi.queue import _RunState

    settings.set_setting(session, "ntfy_topic", "room")
    settings.set_setting(session, "notify_on_progress", True)
    settings.set_setting(session, "notify_progress_interval_minutes", 1)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class LiveProc(FakeProc):
        def __init__(self):
            super().__init__()
            self.dead = False

        def poll(self):  # type: ignore[override]
            return 1 if self.dead else None

    proc = LiveProc()
    state = _RunState(FakeHandle([], session_id="ses_fake"))
    state.handle.proc = proc
    state.last_step_text = "compiling"

    # A fake clock that jumps a minute per call + no real sleeping, so the loop's
    # interval check fires almost immediately without waiting real minutes. We
    # patch `jalebi.queue.time` (the module-global name), NOT the real `time`
    # module, so the test's own _wait_until keeps working.
    class FakeTime:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            self.now += 60.0
            return self.now

        def sleep(self, _s):
            return None

    fake_time = FakeTime()
    monkeypatch.setattr("jalebi.queue.time", fake_time)

    sent: list[dict] = []

    class FakeResp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "json": json})
        return FakeResp()

    monkeypatch.setattr("jalebi.notify.httpx.post", fake_post)

    thread = threading.Thread(target=q._progress_notify_loop, args=(task, state))
    thread.start()
    assert _wait_until(lambda: any("still running" in (s["json"].get("title") or "") for s in sent))
    proc.dead = True
    thread.join(timeout=5)
    assert not thread.is_alive()
    progress = [s for s in sent if "still running" in s["json"]["title"]]
    assert progress
    assert "compiling" in progress[0]["json"]["message"]
    assert sent[0]["url"] == "https://ntfy.sh"
    assert progress[0]["json"]["topic"] == "room"
    assert progress[0]["json"]["markdown"] is True


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


def test_stall_marks_failed_with_diagnostic(q, session, repo_row, monkeypatch) -> None:
    """A process that emits nothing for STALL_TIMEOUT_SECONDS is killed and failed."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class HungProc(FakeProc):
        pass

    class HungHandle(FakeHandle):
        def __init__(self) -> None:
            super().__init__([], "ses_fake")
            self.proc = HungProc()

        def events(self):
            while not self.proc.killed:
                time.sleep(0.02)
            return
            yield  # pragma: no cover — makes this a generator

    settings.set_setting(session, "stall_timeout_seconds", 1)
    _install_adapter(monkeypatch, HungHandle())
    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "failed"
    run = _latest_run(session, task.id)
    assert run.status == "failed"
    steps = json.loads(run.steps_json or "[]")
    assert any("no output" in s.get("text", "") for s in steps)


def _wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_cancel_running_task(q, session, repo_row, monkeypatch) -> None:
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, BlockingHandle())

    thread = threading.Thread(target=q._run_task, args=(task.id,))
    thread.start()
    assert _wait_until(lambda: _fresh_task(session, task.id).status == "running")
    assert q.cancel(task.id) is True
    thread.join(timeout=10)
    assert not thread.is_alive()  # the worker wound down, not just timed out

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
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 42
    refs = _git(["-C", repo_row.clone_url, "show-ref", "--heads"]).splitlines()
    assert any(f"refs/heads/jalebi/{task.id}" in line for line in refs)


def test_publish_reuses_existing_pr_for_head(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", ReusingGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 99


def test_publish_creates_new_pr_when_existing_head_pr_is_closed(
    q, session, repo_row, monkeypatch
) -> None:
    """A closed/merged PR reusing the jalebi/<taskId> head must NOT be reused —
    publish must open a fresh PR instead (regression: task 3 re-published onto a
    stale closed PR)."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    client = ClosedPrGitHubClient("t")
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: client)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 42
    assert client.create_pr_calls == 1


def test_publish_creates_new_pr_when_recorded_pr_is_closed(
    q, session, repo_row, monkeypatch
) -> None:
    """A task whose stored pr_number points at a now-closed PR must not return
    it — publish must drop the stale number and open a fresh PR."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    task.pr_number = 99
    session.commit()
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    client = StaleRecordedPrGitHubClient("t")
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: client)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 42
    assert client.create_pr_calls == 1
    assert client.get_pr_calls == [99]


def test_publish_uses_agent_written_pr_md(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    pr_md = GitWorkspace.worktree_path(q.config.data_dir, task.id) / ".jalebi" / "pr.md"
    pr_md.parent.mkdir(parents=True, exist_ok=True)
    pr_md.write_text("# Implement the thing\n\nAdded the thing and a changelog entry.\n")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 42


def test_cancel_suppresses_error_event(q, session, repo_row, monkeypatch) -> None:
    """Cancelling a run must not surface 'exited with code -15' as an error."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, BlockingHandle())
    thread = threading.Thread(target=q._run_task, args=(task.id,))
    thread.start()
    assert _wait_until(lambda: _fresh_task(session, task.id).status == "running")
    assert q.cancel(task.id) is True
    thread.join(timeout=5)

    run = _latest_run(session, task.id)
    assert run.status == "cancelled"
    steps = json.loads(run.steps_json or "[]")
    assert not any("code -15" in (s.get("text") or "") for s in steps)
    assert any("cancelled" in (s.get("text") or "").lower() for s in steps)


def test_review_task_posts_review_and_does_not_publish(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_row.id,
        prompt="review it",
        prs=[3],
        context={
            "prs": [
                {
                    "number": 3,
                    "title": "t",
                    "body": "b",
                    "html_url": "u",
                    "base": "main",
                    "head": "h",
                    "state": "open",
                    "author": "a",
                }
            ]
        },
    )
    wt = tmp_path / "review"
    wt.mkdir(parents=True)
    (wt / ".jalebi").mkdir(parents=True)
    (wt / ".jalebi" / "review.md").write_text("LGTM with nits:\n- fix x\n")

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ReviewGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)

    class RecordingClient:
        def __init__(self, token: str):
            self.token = token
            self.reviews = []

        def post_pr_review(self, full_name, pr_number, body):
            self.reviews.append((pr_number, body))

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", RecordingClient)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    run = _latest_run(session, task.id)
    assert run.status == "done"
    assert run.steps_json and "Review posted" in run.steps_json


def test_manual_publish_uses_tasks_account(q, session, repo_row, monkeypatch) -> None:
    """Manual publish must resolve the task's own account, not the primary."""
    secrets.add_github_token(q.config, "acct-b", "ghp_b")
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        pat_name="acct-b",
    )
    _seed_commit(q, task.id, repo_row)

    seen: dict[str, str] = {}

    class RecordingClient:
        def __init__(self, token: str):
            seen["token"] = token

        def find_pr_by_head(self, *a, **k):
            return None

        def create_pr(self, *a, **k):
            return 7

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", RecordingClient)
    assert q.publish_task(task.id) == 7
    assert seen["token"] == "ghp_b"


def test_agent_env_carries_resolved_token_and_strips_gh(q, session, repo_row, monkeypatch) -> None:
    """Agents get the selected account's token in env and never auth gh."""
    secrets.add_github_token(q.config, "acct-b", "ghp_b")
    task = tasks.create_task(
        session,
        type_="issue_fix",
        repo_id=repo_row.id,
        prompt="do it",
        pat_name="acct-b",
    )
    captured: dict[str, dict[str, str | None] | None] = {}

    class CapturingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["env"] = env
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *a, **k):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: CapturingAdapter())
    _no_publish(session)
    q._run_task(task.id)

    env = captured["env"]
    assert env is not None
    assert env["JALEBI_GITHUB_TOKEN"] == "ghp_b"
    assert env["GIT_AUTHOR_NAME"] == "Jalebi"
    assert env["GH_CONFIG_DIR"]
    assert env.get("GH_TOKEN") is None
    assert env.get("GITHUB_TOKEN") is None
    # No git push credentials for the agent (Jalebi is the only pusher).
    assert env.get("GIT_CONFIG_VALUE_0") is None


def test_freeform_agent_env_carries_selected_token(q, session, repo_row, monkeypatch) -> None:
    """Freeform agents act as the SELECTED account: they get that account's PAT
    in the env (for GitHub API use) but no git push credentials."""
    secrets.add_github_token(q.config, "acct-b", "ghp_b")
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        pat_name="acct-b",
    )
    captured: dict[str, dict[str, str | None] | None] = {}

    class CapturingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["env"] = env
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *a, **k):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: CapturingAdapter())
    _no_publish(session)
    q._run_task(task.id)

    env = captured["env"]
    assert env is not None
    assert env["JALEBI_GITHUB_TOKEN"] == "ghp_b"
    assert env["GIT_AUTHOR_NAME"] == "Jalebi"
    assert env.get("GH_TOKEN") is None
    assert env.get("GITHUB_TOKEN") is None
    # No git push credentials — the token is for the GitHub API, not git.
    assert env.get("GIT_CONFIG_VALUE_0") is None


def test_agent_env_injects_selected_env_vars(q, session, repo_row, monkeypatch) -> None:
    """A task's selected env vars are injected into the agent subprocess env."""
    from jalebi import envvars

    envvars.upsert_env_var(session, name="DATABASE_URL", value="postgres://secret", repo_id=None)
    envvars.upsert_env_var(session, name="API_KEY", value="sk-secret-value", repo_id=None)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        env_vars=["DATABASE_URL", "API_KEY"],
    )
    captured: dict[str, dict[str, str | None] | None] = {}

    class CapturingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["env"] = env
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *a, **k):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: CapturingAdapter())
    _no_publish(session)
    q._run_task(task.id)

    env = captured["env"]
    assert env is not None
    assert env["DATABASE_URL"] == "postgres://secret"
    assert env["API_KEY"] == "sk-secret-value"
    # The Jalebi-pinned token/identity are not overridable by the env vars.
    assert env["JALEBI_GITHUB_TOKEN"] is not None


def test_agent_env_does_not_inject_unselected_env_vars(q, session, repo_row, monkeypatch) -> None:
    """Only the task's selected env vars reach the agent; others stay out."""
    from jalebi import envvars

    envvars.upsert_env_var(session, name="SECRET_A", value="aaa", repo_id=None)
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="do it", env_vars=[]
    )
    captured: dict[str, dict[str, str | None] | None] = {}

    class CapturingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["env"] = env
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *a, **k):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: CapturingAdapter())
    _no_publish(session)
    q._run_task(task.id)

    env = captured["env"]
    assert env is not None
    assert env.get("SECRET_A") is None


def test_env_var_value_is_masked_in_stored_steps(q, session, repo_row, monkeypatch) -> None:
    """An env-var value echoed by the agent must be redacted in stored steps."""
    from jalebi import envvars

    envvars.upsert_env_var(session, name="API_KEY", value="ghp_env_secret", repo_id=None)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        env_vars=["API_KEY"],
    )
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="the key is ghp_env_secret here"),
                AgentEvent(type="done"),
            ]
        ),
    )
    _no_publish(session)
    q._run_task(task.id)

    run = _latest_run(session, task.id)
    steps_json = json.dumps(json.loads(run.steps_json or "[]"))
    assert "ghp_env_secret" not in steps_json
    assert "***" in steps_json


def test_publish_masks_agent_written_pr_md(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    pr_md = GitWorkspace.worktree_path(q.config.data_dir, task.id) / ".jalebi" / "pr.md"
    pr_md.parent.mkdir(parents=True, exist_ok=True)
    pr_md.write_text("# Leaked token ghp_test here\n\ntoken: ghp_test must be masked\n")

    posted: dict[str, str] = {}

    class RecordingClient:
        def __init__(self, token: str):
            pass

        def find_pr_by_head(self, *a, **k):
            return None

        def create_pr(self, full_name, *, title, body, head, base):
            posted["title"] = title
            posted["body"] = body
            return 42

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", RecordingClient)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q._run_task(task.id)

    assert "ghp_test" not in posted["title"]
    assert "ghp_test" not in posted["body"]
    assert "***" in posted["body"]


def test_review_post_is_masked(q, session, repo_row, monkeypatch, tmp_path) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_row.id,
        prompt="review it",
        prs=[3],
        context={
            "prs": [
                {
                    "number": 3,
                    "title": "t",
                    "body": "b",
                    "html_url": "u",
                    "base": "main",
                    "head": "h",
                    "state": "open",
                    "author": "a",
                }
            ]
        },
    )
    wt = tmp_path / "review"
    wt.mkdir(parents=True)
    (wt / ".jalebi").mkdir(parents=True)
    (wt / ".jalebi" / "review.md").write_text("saw token ghp_test in the diff\n")

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ReviewGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)

    posted: list[str] = []

    class RecordingClient:
        def __init__(self, token: str):
            pass

        def post_pr_review(self, full_name, pr_number, body):
            posted.append(body)

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", RecordingClient)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q._run_task(task.id)

    assert posted
    assert "ghp_test" not in posted[0]
    assert "***" in posted[0]


def test_no_changes_skips_publish(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class MustNotPublish:
        def __init__(self, token: str):
            self.token = token

        def find_pr_by_head(self, *args, **kwargs) -> int | None:
            return None

        def create_pr(self, *args, **kwargs) -> int:
            raise AssertionError("publish must be skipped when nothing changed")

        def close(self) -> None:
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", MustNotPublish)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number is None


def test_publish_failure_sets_needs_approval(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FailingGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "needs_approval"
    assert _latest_run(session, task.id).status == "done"


@pytest.mark.parametrize("mode", ["new_pr", "update_pr", "push_branch"])
def test_failed_publish_blocks_dependents_until_manual_publish_succeeds(
    q, session, repo_row, monkeypatch, mode
) -> None:
    settings.set_setting(session, "auto_publish", True)
    parent = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="parent")
    child = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="child")
    child.status = "blocked"
    tasks.add_dependency(session, child.id, parent.id)
    session.commit()
    _seed_commit(q, parent.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FailingGitHubClient)
    enqueued = []
    monkeypatch.setattr(q, "enqueue", enqueued.append)

    q._run_task(parent.id)

    assert _fresh_task(session, parent.id).status == "needs_approval"
    assert _fresh_task(session, child.id).status == "blocked"
    assert tasks.has_unmet_deps(session, child.id)
    assert enqueued == []

    # Retrying the publish must keep dependents blocked if it fails again.
    with pytest.raises(RuntimeError, match="PR create failed"):
        q.publish_task(parent.id)
    assert _fresh_task(session, child.id).status == "blocked"
    assert enqueued == []

    # Exercise the shared completion path for all three manual publish modes.
    result = 0 if mode == "push_branch" else 42
    monkeypatch.setattr(q, "_publish", lambda *a, **kw: result)
    assert q.publish_task(parent.id, mode=mode, target_branch="main", pr_number=42) == result
    assert _fresh_task(session, parent.id).status == "done"
    assert _fresh_task(session, child.id).status == "queued"
    assert not tasks.has_unmet_deps(session, child.id)
    assert enqueued == [child.id]


def test_publish_conflict_sets_needs_approval_and_skips_pr(
    q, session, repo_row, monkeypatch
) -> None:
    """A task branch that conflicts with the PR target must NOT be pushed or
    published: the task goes to needs_approval with the conflicting files
    surfaced, and no PR is opened (main→dev divergence handling)."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="issue_fix", repo_id=repo_row.id, prompt="fix it")

    # Branch the worktree off main and commit a conflicting change.
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task.id, FULL_NAME, "main")
    (wt / "file.txt").write_text("hello\nagent change\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "file.txt"])
    _git(["-C", str(wt), "commit", "-m", "agent change"])

    class ConflictGit(GitWorkspace):
        def merge_origin_into(self, worktree, full_name, base_branch, token=None):
            return ["file.txt"]

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ConflictGit)

    class MustNotPublish:
        def __init__(self, token: str):
            self.token = token

        def create_pr(self, *args, **kwargs) -> int:
            raise AssertionError("publish must not open a PR on a conflict")

        def find_pr_by_head(self, *args, **kwargs):
            raise AssertionError("publish must not query PRs on a conflict")

        def get_pr(self, *args, **kwargs):
            raise AssertionError("publish must not query PRs on a conflict")

        def close(self) -> None:
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", MustNotPublish)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "needs_approval"
    assert fresh.pr_number is None
    steps = json.loads(_latest_run(session, task.id).steps_json or "[]")
    conflicted = [s for s in steps if "conflict" in (s.get("text") or "")]
    assert conflicted and "file.txt" in conflicted[0]["text"]


def test_publish_comments_only_on_new_pr(q, session, repo_row, monkeypatch) -> None:
    """Re-publishing to an existing OPEN PR (follow-up pushes) must NOT re-comment
    on the linked issues — only a freshly created PR gets the issue link comments."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(session, type_="issue_fix", repo_id=repo_row.id, prompt="fix it")

    comments: list[tuple[int, str]] = []

    class ReuseClient(ReusingGitHubClient):
        def __init__(self, token: str):
            super().__init__(token)

        def comment_on_issue(self, full_name, number, body):
            comments.append((number, body))

    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", ReuseClient)

    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert _fresh_task(session, task.id).pr_number == 99
    assert comments == []  # reuse path stays silent


def test_manual_publish_refuses_no_commits(q, session, repo_row, monkeypatch) -> None:
    """Manual publish on a branch with zero commits ahead of the target must be
    refused (no-op gate) instead of pushing an empty PR."""
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class MustNotPublish:
        def __init__(self, token: str):
            self.token = token

        def create_pr(self, *args, **kwargs) -> int:
            raise AssertionError("publish must be refused when nothing to publish")

        def find_pr_by_head(self, *args, **kwargs):
            raise AssertionError("publish must be refused when nothing to publish")

        def close(self) -> None:
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", MustNotPublish)
    from jalebi.queue import PublishError

    with pytest.raises(PublishError):
        q.publish_task(task.id)


def test_word_stream_messages_coalesce_into_one_step(
    q, session, repo_row, monkeypatch
) -> None:
    """Word-streaming backends (grok) emit one message event per token — they
    must persist as a single merged timeline step, not hundreds of steps."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    words = ["I'll", " work", " through", " it", "."]
    _install_adapter(
        monkeypatch,
        FakeHandle([AgentEvent(type="message", text=w) for w in words] + [AgentEvent(type="done")]),
    )
    q._run_task(task.id)
    run = _latest_run(session, task.id)
    assert run.status == "done"
    steps = json.loads(run.steps_json or "[]")
    messages = [s for s in steps if s["type"] == "message"]
    assert len(messages) == 1
    assert messages[0]["text"] == "I'll work through it."


def test_coalescing_boundary_preserves_every_bounded_message_chunk(
    q, session, repo_row, monkeypatch
) -> None:
    """A large next chunk flushes the buffer instead of truncating its tail."""
    from jalebi.queue import MAX_STEP_TEXT

    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    first = "a" * 400
    second = "b" * (MAX_STEP_TEXT - 100)
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text=first),
                AgentEvent(type="message", text=second),
                AgentEvent(type="done"),
            ]
        ),
    )
    q._run_task(task.id)
    run = _latest_run(session, task.id)
    messages = [
        step["text"]
        for step in json.loads(run.steps_json or "[]")
        if step["type"] == "message"
    ]
    assert "".join(messages) == first + second
    assert all(len(text) <= MAX_STEP_TEXT for text in messages)


def test_non_message_event_splits_coalesced_messages(
    q, session, repo_row, monkeypatch
) -> None:
    """A tool_call between two message bursts keeps them as separate steps."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="before "),
                AgentEvent(type="tool_call", data={"tool": "bash"}),
                AgentEvent(type="message", text="after"),
                AgentEvent(type="done"),
            ]
        ),
    )
    q._run_task(task.id)
    run = _latest_run(session, task.id)
    steps = json.loads(run.steps_json or "[]")
    messages = [s for s in steps if s["type"] == "message"]
    assert [m["text"] for m in messages] == ["before ", "after"]
    assert any(s["type"] == "tool_call" for s in steps)


def test_done_with_only_steps_and_no_content_is_failed(
    q, session, repo_row, monkeypatch
) -> None:
    """Exit-0 with zero agent output (e.g. a banner-only run) is a
    failure, not a success — with a stable marker auto-recovery won't retry."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="step", phase="step", text="banner line"),
                AgentEvent(type="done"),
            ]
        ),
    )
    q._run_task(task.id)
    run = _latest_run(session, task.id)
    assert run.status == "failed"
    assert _fresh_task(session, task.id).status == "failed"
    steps = json.loads(run.steps_json or "[]")
    assert any(
        "without producing any agent output" in (s.get("text") or "") for s in steps
    )


def test_bare_done_with_no_events_stays_done(q, session, repo_row, monkeypatch) -> None:
    """A synthetic done with no stream at all keeps the old verdict (this is
    the shape the wider suite's fake handles use)."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q._run_task(task.id)
    assert _latest_run(session, task.id).status == "done"


def test_mid_stream_error_drains_rest_of_stream(
    q, session, repo_row, monkeypatch
) -> None:
    """An error event no longer stops the drain: later events are recorded,
    the child is observed to exit, and the run still fails."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="error", text="boom"),
                AgentEvent(type="message", text="aftermath"),
                AgentEvent(type="done"),
            ]
        ),
    )
    q._run_task(task.id)
    run = _latest_run(session, task.id)
    assert run.status == "failed"
    steps = json.loads(run.steps_json or "[]")
    assert any((s.get("text") or "") == "boom" for s in steps if s["type"] == "error")
    assert any((s.get("text") or "") == "aftermath" for s in steps)


def test_manual_publish_refuses_source_only_commits(
    q, session, repo_row, git_remote, monkeypatch, tmp_path
) -> None:
    """A task based on source_branch=dev targeting main must NOT publish when
    its only "ahead" commits are the source branch's own (the PR #8 shape: a
    no-op agent run once shipped a whole development branch as a task PR)."""
    src = tmp_path / "src2"
    _git(["clone", git_remote, str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    _git(["-C", str(src), "checkout", "-b", "dev"])
    (src / "dev.txt").write_text("dev work\n")
    _git(["-C", str(src), "add", "dev.txt"])
    _git(["-C", str(src), "commit", "-m", "dev commit"])
    _git(["-C", str(src), "push", "-u", "origin", "dev"])

    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        source_branch="dev",
        target_branch="main",
    )
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    git.create_worktree(task.id, FULL_NAME, "dev")

    class MustNotPublish:
        def __init__(self, token: str):
            self.token = token

        def create_pr(self, *args, **kwargs) -> int:
            raise AssertionError("publish must be refused when only source commits are ahead")

        def find_pr_by_head(self, *args, **kwargs):
            raise AssertionError("publish must be refused when only source commits are ahead")

        def close(self) -> None:
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", MustNotPublish)
    from jalebi.queue import PublishError

    with pytest.raises(PublishError):
        q.publish_task(task.id)


def test_task_own_base_matches_worktree_base(q, session, repo_row) -> None:
    """The publish no-op gate compares against the worktree base per type."""
    from jalebi.queue import TaskQueue

    issue = tasks.create_task(session, type_="issue_fix", repo_id=repo_row.id, prompt="x")
    assert TaskQueue._task_own_base(issue) == "main"
    free = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="x",
        source_branch="dev",
        target_branch="main",
    )
    assert TaskQueue._task_own_base(free) == "dev"
    pr_head = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="x",
        source_branch="pr/7/head",
        target_branch="main",
    )
    assert TaskQueue._task_own_base(pr_head) == "main"


def test_issue_fix_worktree_based_on_target_branch(q, session, repo_row, monkeypatch) -> None:
    """issue_fix uses the SINGLE-target model: the worktree must be created from
    the target branch (the PR base), not the source branch."""
    from jalebi.db import Run as RunRow
    from jalebi.db import now

    task = tasks.create_task(
        session,
        type_="issue_fix",
        repo_id=repo_row.id,
        prompt="fix it",
        source_branch="main",
        target_branch="development",
    )
    run = RunRow(
        task_id=task.id,
        seq=1,
        cli="opencode",
        model=None,
        started_at=now(),
        status="running",
    )
    session.add(run)
    session.commit()

    seen: dict[str, str] = {}

    class CapturingGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):  # type: ignore[override]
            return None

        def branch_exists(self, *a, **k):
            return False

        def create_worktree(self, task_id, full_name, base_branch, token=None):  # type: ignore[override]
            seen["base_branch"] = base_branch
            wt = GitWorkspace.worktree_path(self.config.data_dir, task_id)
            wt.mkdir(parents=True, exist_ok=True)
            (wt / ".git").write_text("gitdir: x\n")
            return wt

        def reset_branch_to_base(self, *a, **k):
            raise AssertionError("no stale branch on first run")

    monkeypatch.setattr("jalebi.queue.GitWorkspace", CapturingGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    _no_publish(session)

    q._run_task(task.id)

    assert seen["base_branch"] == "development"


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


def test_step_from_event_truncates_before_masking(q) -> None:
    from jalebi.adapters.types import AgentEvent
    from jalebi.queue import MAX_STEP_TEXT

    seen: list[str] = []

    def masker(text: str) -> str:
        seen.append(text)
        return text.replace("TOKEN", "***")

    long = "x" * (MAX_STEP_TEXT * 2) + " TOKEN"
    entry = q._step_from_event(AgentEvent(type="message", text=long), masker)
    # The masker must never receive more than MAX_STEP_TEXT characters, so a
    # pathological user regex cannot backtrack over unbounded input (M2).
    assert len(seen[0]) == MAX_STEP_TEXT
    assert len(entry["text"]) <= MAX_STEP_TEXT


def test_build_agent_env_strips_inherited_git_config(monkeypatch) -> None:
    from jalebi.queue import _build_agent_env

    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "store")
    monkeypatch.setenv("GIT_DIR", "/somewhere/else")
    monkeypatch.setenv("GIT_WORK_TREE", "/elsewhere")

    env = _build_agent_env("ghp_x")
    # inherited git state stripped
    assert env.get("GIT_CONFIG_KEY_1") is None
    assert env.get("GIT_CONFIG_VALUE_1") is None
    assert env.get("GIT_DIR") is None
    assert env.get("GIT_WORK_TREE") is None
    # Jalebi's own config stays
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"]
    assert env["JALEBI_GITHUB_TOKEN"] == "ghp_x"


def test_build_agent_env_has_no_git_push_credentials(monkeypatch) -> None:
    """The agent gets the selected PAT for the GitHub API but NO git push
    credentials — Jalebi is the only pusher (auth_env is never applied)."""
    from jalebi.queue import _build_agent_env

    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "credential.helper")
    monkeypatch.setenv("GIT_DIR", "/somewhere/else")
    env = _build_agent_env("ghp_x")
    # inherited git state dropped…
    assert env.get("GIT_DIR") is None
    assert env.get("GIT_CONFIG_KEY_1") is None
    # …and Jalebi does NOT give the agent git push credentials.
    assert env.get("GIT_CONFIG_COUNT") is None
    assert env.get("GIT_CONFIG_KEY_0") is None
    assert env.get("GIT_CONFIG_VALUE_0") is None
    # The selected PAT is still exposed for GitHub API use.
    assert env["JALEBI_GITHUB_TOKEN"] == "ghp_x"


def test_pr_title_and_body_closes_from_issues_json(q, session, repo_row) -> None:
    # Closes must come from issues_json (the real linked issue), NOT from a
    # stray #N in the prompt text (D-5).
    task = tasks.create_task(
        session,
        type_="issue_fix",
        repo_id=repo_row.id,
        prompt="fix the bug mentioned in #3 (see linked issue)",
        issues=[12],
    )
    title, body = q._pr_title_and_body(task)
    assert "Closes #12" in body
    assert "Closes #3" not in body


def test_pr_title_and_body_no_issues_adds_no_closes(q, session, repo_row) -> None:
    task = tasks.create_task(
        session, type_="issue_fix", repo_id=repo_row.id, prompt="fix #7 please"
    )
    _title, body = q._pr_title_and_body(task)
    assert "Closes" not in body


def test_run_captures_masked_diff(q, session, repo_row, monkeypatch) -> None:
    """A code task's run stores a masked run-end diff (PRD §12 diff viewer)."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task.id, FULL_NAME, "main")
    # The committed change includes the (fake) token so we can prove masking.
    (wt / "f.txt").write_text("changed token ghp_test here\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "f.txt"])
    _git(["-C", str(wt), "commit", "-m", "change"])
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    q._run_task(task.id)

    session.expire_all()
    run = _latest_run(session, task.id)
    assert run is not None
    assert run.status == "done"
    assert run.diff_text is not None
    assert "f.txt" in run.diff_text
    assert "+changed" in run.diff_text
    # The token value never survives; the diff is masked at ingest.
    assert "ghp_test" not in run.diff_text
    assert "***" in run.diff_text


def test_done_run_with_uncommitted_changes_is_surfaced(
    q, session, repo_row, monkeypatch
) -> None:
    """A done run whose working tree is dirty but which made NO commits must not
    look clean: the uncommitted files are surfaced as a step and the working-tree
    diff is captured (there is no committed diff to take precedence)."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task.id, FULL_NAME, "main")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    # Dirty working tree, no commits: a modified tracked file AND an untracked
    # file. The branch is not ahead, so nothing would be published.
    (wt / "file.txt").write_text("hello\nchanged\n")
    (wt / "scratch.txt").write_text("untracked\n")
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    q._run_task(task.id)

    session.expire_all()
    run = _latest_run(session, task.id)
    assert run.status == "done"
    steps = json.loads(run.steps_json or "[]")
    texts = " ".join(str(s.get("text") or "") for s in steps)
    assert "uncommitted" in texts
    assert "file.txt" in texts
    assert "scratch.txt" in texts
    assert run.diff_text is not None
    assert "changed" in run.diff_text


def test_empty_done_run_with_uncommitted_changes_is_surfaced(
    q, session, repo_row, monkeypatch
) -> None:
    """An `empty_done` failure (exit 0, stream of steps but no agent output —
    the agy 1.2.x task-83 shape) must still surface a dirty working tree: the
    agent may have done real file work whose output never streamed."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task.id, FULL_NAME, "main")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    (wt / "file.txt").write_text("hello\nchanged\n")
    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="step", phase="step", text="banner line"),
                AgentEvent(type="done"),
            ]
        ),
    )

    q._run_task(task.id)

    session.expire_all()
    run = _latest_run(session, task.id)
    assert run.status == "failed"  # still a failure — just a visible one now
    steps = json.loads(run.steps_json or "[]")
    texts = " ".join(str(s.get("text") or "") for s in steps)
    assert "without producing any agent output" in texts
    assert "uncommitted" in texts
    assert "file.txt" in texts
    assert run.diff_text is not None
    assert "changed" in run.diff_text


def test_manual_publish_mode_skips_autopublish(q, session, repo_row, monkeypatch) -> None:
    """A task with publish_mode='manual' must NOT auto-publish even when the
    global auto_publish setting is true."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        publish_mode="manual",
    )
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number is None  # never auto-published


def test_auto_publish_mode_skips_when_setting_off(q, session, repo_row, monkeypatch) -> None:
    """A task with publish_mode='auto' publishes even when the global setting is off."""
    _no_publish(session)  # auto_publish = False
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        publish_mode="auto",
    )
    _seed_commit(q, task.id, repo_row)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assert fresh.pr_number == 42


def test_review_run_has_no_diff(q, session, repo_row, monkeypatch, tmp_path) -> None:
    """pr_review runs do not capture a diff (the review worktree is the PR itself)."""
    from pathlib import Path

    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_row.id,
        prompt="review",
        prs=[1],
        context={
            "prs": [
                {
                    "number": 1,
                    "title": "t",
                    "body": "b",
                    "html_url": "u",
                    "base": "main",
                    "head": "h",
                    "state": "open",
                    "author": "a",
                }
            ]
        },
    )
    wt = Path(tmp_path) / "review"
    wt.mkdir(parents=True)
    (wt / ".jalebi").mkdir(parents=True)
    (wt / ".jalebi" / "review.md").write_text("ok\n")

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

        def diff_against_target(self, *a, **k):
            # If the pr_review exclusion guard were removed, this sentinel would
            # be stored — the assertion below only passes because of the guard.
            return "diff --git a/x b/x\nSHOULD NOT BE SEEN"

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ReviewGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)

    class NoopClient:
        def __init__(self, token):
            pass

        def post_pr_review(self, *a, **k):
            return None

        def close(self):
            pass

    class ReviewAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            return FakeHandle([AgentEvent(type="done")], session_id="ses_r")

        def resume(self, *a, **k):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: ReviewAdapter())
    monkeypatch.setattr("jalebi.queue.GitHubClient", NoopClient)

    q._run_task(task.id)

    session.expire_all()
    run = _latest_run(session, task.id)
    assert run is not None
    assert run.status == "done"
    assert run.diff_text is None


def test_publish_comments_on_linked_issues(q, session, repo_row, monkeypatch) -> None:
    """issue_fix publish comments on each linked issue with the PR link (T-3)."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(
        session, type_="issue_fix", repo_id=repo_row.id, prompt="fix it", issues=[12, 34]
    )
    _seed_commit(q, task.id, repo_row)

    comments: list[tuple[int, str]] = []

    class CommentingClient(FakeGitHubClient):
        def __init__(self, token):
            super().__init__(token)

        def comment_on_issue(self, full_name, number, body):
            comments.append((number, body))

    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    monkeypatch.setattr("jalebi.queue.GitHubClient", CommentingClient)

    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert sorted(n for n, _ in comments) == [12, 34]
    assert all("PR #42" in body for _, body in comments)  # FakeGitHubClient.create_pr → 42


def test_publish_comments_on_issue_after_an_ordinary_question(
    q, session, repo_row, monkeypatch
) -> None:
    """A normal question in a completion summary is not an approval gate."""
    settings.set_setting(session, "auto_publish", True)
    task = tasks.create_task(
        session, type_="issue_fix", repo_id=repo_row.id, prompt="fix it", issues=[12]
    )
    _seed_commit(q, task.id, repo_row)
    comments: list[int] = []

    class CommentingClient(FakeGitHubClient):
        def comment_on_issue(self, full_name, number, body):
            comments.append(number)

    _install_adapter(
        monkeypatch,
        FakeHandle(
            [
                AgentEvent(type="message", text="Fixed it. Does that cover the issue?"),
                AgentEvent(type="done"),
            ]
        ),
    )
    monkeypatch.setattr("jalebi.queue.GitHubClient", CommentingClient)
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert comments == [12]


def test_slow_but_live_stream_is_not_stalled(q, session, repo_row, monkeypatch) -> None:
    """An agent that keeps emitting (never silent for the stall timeout) must
    NOT be stalled (T-9)."""
    # Generous stall timeout so a CI hiccup between the 10 ms events can't flake.
    settings.set_setting(session, "stall_timeout_seconds", 5)
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class ChattyHandle(FakeHandle):
        def __init__(self):
            super().__init__([])

        def events(self):
            # 10 ms gaps vs a 5 s stall timeout = a wide margin; the agent is
            # slow but never silent (150 ticks ~ 1.5 s total).
            for i in range(150):
                yield AgentEvent(type="message", text=f"tick {i}")
                time.sleep(0.01)
            yield AgentEvent(type="done")

    _install_adapter(monkeypatch, ChattyHandle())
    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    run = _latest_run(session, task.id)
    assert run.status == "done"
    step_types = [s["type"] for s in json.loads(run.steps_json or "[]")]
    # A stall misfire would append a diagnostic error step AFTER done; assert the
    # stream really ends on 'done' (and no stall text crept in).
    assert step_types[-1] == "done"
    assert not any("no output" in (s.get("text") or "") for s in json.loads(run.steps_json or "[]"))
    texts = [s.get("text") or "" for s in json.loads(run.steps_json or "[]")]
    merged = "\n".join(texts)
    message_steps = [s for s in json.loads(run.steps_json or "[]") if s["type"] == "message"]
    # Word-stream coalescing: 150 tick events persist as a handful of merged
    # steps, with the first and last tick intact.
    assert len(message_steps) < 150
    assert "tick 0" in merged and "tick 149" in merged


def test_stalled_run_is_failed_even_when_handle_emits_done(
    q, session, repo_row, monkeypatch
) -> None:
    """A watchdog kill is not a successful completion just because done follows it."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _install_adapter(
        monkeypatch,
        FakeHandle([AgentEvent(type="message", text="starting"), AgentEvent(type="done")]),
    )

    def force_stalled(_task, state, _timeout, stall_timeout=None):
        state.reason = "stalled"

    monkeypatch.setattr(q, "_start_watchdog", force_stalled)
    q._run_task(task.id)
    run = _latest_run(session, task.id)
    assert run.status == "failed"
    assert _fresh_task(session, task.id).status == "failed"
    assert any(step.get("stall") for step in json.loads(run.steps_json or "[]"))


def test_catalog_agent_applies_cli_model_custom_instructions(
    q, session, repo_row, monkeypatch,
) -> None:
    """A task backed by a catalog agent gets its cli/model/custom_instructions
    and its skills materialized into the worktree."""
    from jalebi import catalog

    catalog.create_agent(
        session,
        id="auditor",
        name="Auditor",
        kind="general",
        cli="opencode",
        model="openai/gpt-5.1",
        personality_md="Be adversarial.",
        skills=[{"name": "secure-coding", "content": "# Secure coding\n"}],
        custom_instructions="Check auth, secrets, injection.",
        enabled=True,
    )
    _no_publish(session)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        agent_id="auditor",
    )
    captured: dict = {}

    class RecordingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["cwd"] = cwd
            captured["prompt"] = prompt
            captured["model"] = model
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: RecordingAdapter())
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    assert captured["prompt"] == "do it\n\nCheck auth, secrets, injection."
    assert captured["model"] == "openai/gpt-5.1"
    # Worktree has the personality + skills + @path references.
    wt = GitWorkspace.worktree_path(q.config.data_dir, task.id)
    skill = wt / ".claude" / "skills" / "secure-coding" / "SKILL.md"
    assert skill.read_text() == "# Secure coding\n"
    assert "Be adversarial." in (wt / "AGENTS.md").read_text()
    assert "@.claude/skills/secure-coding/SKILL.md" in (wt / "AGENTS.md").read_text()


def test_linked_pr_prompt_pointer_appended(q, session, repo_row, monkeypatch) -> None:
    """A non-review task linked to a PR gets a small pointer appended to the
    effective prompt naming the PR and how to fetch its reviews — so a run still
    knows which PR even if the AGENTS.md context block is restored away."""
    _no_publish(session)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="fix the issues",
        prs=[7],
    )
    captured: dict = {}

    class RecordingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["prompt"] = prompt
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: RecordingAdapter())
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    prompt = captured["prompt"]
    assert prompt.startswith("fix the issues")
    assert "Linked PR: #7" in prompt
    assert repo_row.full_name in prompt
    assert "pulls/7/reviews" in prompt
    assert "$JALEBI_GITHUB_TOKEN" in prompt


def test_default_model_applied_only_for_default_backend(
    q, session, repo_row, monkeypatch
) -> None:
    """The global default model is a fallback only when the resolved backend IS
    the default backend; a task pinned to another backend uses the CLI's own
    default (None)."""
    settings.set_setting(session, "default_backend", "codex")
    settings.set_setting(session, "default_model", "gpt-default")
    captured: dict = {}

    class RecordingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["model"] = model
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: RecordingAdapter())

    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    q._run_task(task.id)
    assert captured["model"] == "gpt-default"

    # A task pinned to a different backend must NOT inherit the default model.
    task2 = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="do it", cli="claude"
    )
    q._run_task(task2.id)
    assert captured["model"] is None


def test_catalog_agent_disabled_falls_back_to_defaults(q, session, repo_row, monkeypatch) -> None:
    """A task whose catalog agent was disabled/deleted still runs, falling back
    to the default build agent (stored model/cli already on the task)."""
    from jalebi import catalog

    catalog.create_agent(
        session,
        id="auditor",
        name="Auditor",
        kind="general",
        cli="opencode",
        model="openai/gpt-5.1",
        personality_md="Be adversarial.",
        custom_instructions="Check auth.",
        enabled=True,
    )
    _no_publish(session)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        agent_id="auditor",
    )
    # The agent is disabled between task creation and the run — the queue falls
    # back to the default build agent instead of failing the run.
    catalog.update_agent(session, "auditor", enabled=False)
    captured: dict = {}

    class RecordingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            captured["prompt"] = prompt
            captured["model"] = model
            return FakeHandle([AgentEvent(type="done")])

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: RecordingAdapter())
    q._run_task(task.id)

    assert _fresh_task(session, task.id).status == "done"
    # custom_instructions NOT appended, no model override forced at run time
    assert captured["prompt"] == "do it"
    wt = GitWorkspace.worktree_path(q.config.data_dir, task.id)
    assert "Be adversarial." not in (wt / "AGENTS.md").read_text()


def test_review_assignment_status_tracks_lifecycle(
    q, session, repo_row, monkeypatch, tmp_path,
) -> None:
    """A reviewer task's assignment goes queued → running → posted when the
    review is posted (PRD F7.4 status tracking)."""
    from jalebi import catalog, reviews

    catalog.create_agent(
        session,
        id="auditor",
        name="Auditor",
        kind="reviewer",
        personality_md="Review.",
        enabled=True,
    )
    (task,) = reviews.assign_reviewers(session, repo_row, 3, ["auditor"])
    wt = tmp_path / "review"
    wt.mkdir(parents=True)
    (wt / ".jalebi").mkdir(parents=True)
    (wt / ".jalebi" / "review.md").write_text("LGTM.\n")

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ReviewGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)

    class RecordingClient:
        def __init__(self, token: str):
            self.posted = []

        def post_pr_review(self, full_name, pr_number, body):
            self.posted.append(pr_number)

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", RecordingClient)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))

    q._run_task(task.id)

    fresh = _fresh_task(session, task.id)
    assert fresh.status == "done"
    assignment = reviews.assignment_by_task(session, task.id)
    assert assignment is not None
    assert assignment.status == "posted"
    assert assignment.run_id is not None


def test_review_assignment_failed_on_posting_error(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """A review that fails to POST leaves the assignment failed, not stuck running."""
    from jalebi import catalog, reviews

    catalog.create_agent(
        session, id="auditor", name="Auditor", kind="reviewer", enabled=True
    )
    (task,) = reviews.assign_reviewers(session, repo_row, 3, ["auditor"])
    wt = tmp_path / "review"
    wt.mkdir(parents=True)
    (wt / ".jalebi").mkdir(parents=True)
    (wt / ".jalebi" / "review.md").write_text("LGTM.\n")

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ReviewGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)

    class FailingClient:
        def __init__(self, token: str):
            pass

        def post_pr_review(self, full_name, pr_number, body):
            raise RuntimeError("post failed")

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", FailingClient)
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="done")]))
    q._run_task(task.id)

    assignment = reviews.assignment_by_task(session, task.id)
    assert assignment is not None
    assert assignment.status == "failed"


def test_review_assignment_failed_on_non_done_run(
    q, session, repo_row, monkeypatch, tmp_path,
) -> None:
    """A reviewer run that ends in an error (not done) must not leave the
    assignment stuck 'running'."""
    from jalebi import catalog, reviews

    catalog.create_agent(
        session, id="auditor", name="Auditor", kind="reviewer", enabled=True
    )
    (task,) = reviews.assign_reviewers(session, repo_row, 4, ["auditor"])
    wt = tmp_path / "review"
    wt.mkdir(parents=True)

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

    monkeypatch.setattr("jalebi.queue.GitWorkspace", ReviewGit)
    monkeypatch.setattr("jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None)
    monkeypatch.setattr(
        "jalebi.queue.GitHubClient",
        lambda token: type("C", (), {"close": lambda self: None})(),
    )
    # The agent ends with an error event → run.status != done.
    _install_adapter(monkeypatch, FakeHandle([AgentEvent(type="error", text="boom")]))
    q._run_task(task.id)

    assignment = reviews.assignment_by_task(session, task.id)
    assert assignment is not None
    assert assignment.status == "failed"


def test_history_resolved_session_persists_and_followup_resumes(
    q, session, repo_row, monkeypatch
) -> None:
    """Backends whose stdout carries no session id (e.g. cline) still get a
    resumable session: the adapter's post-run resolve_session result is
    persisted, and the follow-up then resumes with it instead of 409ing."""
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class NoSidHandle(FakeHandle):
        def __init__(self):
            super().__init__([AgentEvent(type="done")], session_id=None)

    class ResolvingAdapter:
        def __init__(self):
            self.resolve_calls: list[str] = []

        def start(self, cwd, prompt, model=None, env=None):
            return NoSidHandle()

        def resume(self, cwd, session_id, prompt, model=None, env=None):
            self.resumed = session_id
            return FakeHandle([AgentEvent(type="done")])

        def list_models(self):
            return []

        def resolve_session(self, cwd):
            self.resolve_calls.append(cwd)
            return "hist_ses_1"

    adapter = ResolvingAdapter()
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_task(task.id)
    session.expire_all()
    run = _latest_run(session, task.id)
    assert run.status == "done"
    assert run.session_id == "hist_ses_1"
    assert len(adapter.resolve_calls) == 1

    # Follow-up now finds a resumable session (would 409 before the fix).
    q._run_followup(task.id, "again")
    session.expire_all()
    runs = tasks.runs_for_task(session, task.id)
    assert len(runs) == 2
    assert runs[1].status == "done"
    # The resumed run records the fake handle's session (ses_fake) — any
    # non-null session proves the follow-up found a resumable session instead
    # of dying on "no resumable session".
    assert runs[1].session_id


def test_rerun_override_flows_into_run(q, session, repo_row, monkeypatch) -> None:
    """A rerun-persisted backend/model override is what the next run executes
    (the task-75 round-trip: switching qwen → kilo must actually run kilo)."""
    _no_publish(session)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo_row.id,
        prompt="do it",
        cli="opencode",
        model="m1",
    )
    # Post-rerun state as POST /rerun {cli, model} persists it (route-level
    # persistence is covered in test_api_tasks.py).
    task.status = "queued"
    task.cli = "codex"
    task.model = "m9"
    task.retry_count = 0
    session.commit()

    seen: dict = {}

    class RecordingAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            seen["model"] = model
            return FakeHandle(
                [AgentEvent(type="message", text="ok"), AgentEvent(type="done")]
            )

        def resume(self, *args, **kwargs):
            raise NotImplementedError

        def list_models(self):
            return []

    def fake_get_adapter(cli):
        seen["cli"] = cli
        return RecordingAdapter()

    monkeypatch.setattr("jalebi.queue.get_adapter", fake_get_adapter)
    q._run_task(task.id)

    assert seen["cli"] == "codex"
    assert seen["model"] == "m9"
    run = _latest_run(session, task.id)
    assert run.cli == "codex"
    assert run.model == "m9"


def test_enabled_cli_falls_back_for_unknown_backend(q, session) -> None:
    """A pin to a backend the registry no longer knows (e.g. removed goose)
    falls back to the first enabled backend instead of 500ing mid-dispatch."""
    from jalebi.queue import TaskQueue

    settings.set_setting(session, "enabled_backends", ["goose", "opencode"])
    assert TaskQueue._enabled_cli(session, "goose") == "opencode"
    assert TaskQueue._enabled_cli(session, "opencode") == "opencode"
    # No list configured at all: known backends pass, unknown fail safe.
    settings.set_setting(session, "enabled_backends", [])
    assert TaskQueue._enabled_cli(session, "opencode") == "opencode"
    assert TaskQueue._enabled_cli(session, "goose") == "opencode"


def test_is_backend_available(monkeypatch) -> None:
    """Availability is registry + PATH, without depending on the test host."""
    from jalebi.adapters import is_backend_available

    monkeypatch.setattr(
        "jalebi.adapters.shutil.which",
        lambda cli: "/mock/bin" if cli == "opencode" else None,
    )
    assert is_backend_available("opencode") is True
    assert is_backend_available("definitely-not-a-cli") is False


def test_missing_binary_fails_fast_without_recovery(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """An enabled-but-uninstalled backend fails with a clear, non-retryable
    message — one run, no auto-recovery loop, no spawn crash."""
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="do it", cli="opencode"
    )
    # Empty PATH: no CLI binary resolves (restored automatically after).
    monkeypatch.setenv("PATH", str(tmp_path))
    q._run_task(task.id)

    session.expire_all()
    assert _fresh_task(session, task.id).status == "failed"
    runs = tasks.runs_for_task(session, task.id)
    assert len(runs) <= 1
    steps = json.loads(runs[0].steps_json or "[]") if runs else []
    assert any("not installed" in (s.get("text") or "") for s in steps)
