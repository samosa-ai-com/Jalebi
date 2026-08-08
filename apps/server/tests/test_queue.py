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

    monkeypatch.setattr("jalebi.queue.STALL_TIMEOUT_SECONDS", 0)
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


def test_slow_but_live_stream_is_not_stalled(q, session, repo_row, monkeypatch) -> None:
    """An agent that keeps emitting (never 300s silent) must NOT be stalled (T-9)."""
    monkeypatch.setattr("jalebi.queue.STALL_TIMEOUT_SECONDS", 0.5)
    _no_publish(session)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    class ChattyHandle(FakeHandle):
        def __init__(self):
            super().__init__([])

        def events(self):
            # 10 ms gaps vs a 0.5 s stall timeout = a wide margin; the agent is
            # slow but never silent.
            for i in range(5):
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
    texts = [s.get("text") for s in json.loads(run.steps_json or "[]")]
    assert "tick 0" in texts and "tick 4" in texts
