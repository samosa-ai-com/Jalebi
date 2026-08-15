"""Commit-status lifecycle tests (PRD F15): registry, state mapping, queue hooks."""

import subprocess

import pytest

from jalebi import checkruns, repos, secrets, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.checkruns import (
    STATE_ERROR,
    STATE_FAILURE,
    STATE_PENDING,
    STATE_SUCCESS,
    state_for_status,
    status_context,
)

FULL_NAME = "owner/checkrepo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def poll(self):
        return None

    def wait(self, timeout=None) -> int:
        return 0


class FakeHandle:
    def __init__(self, events):
        self._events = list(events)
        self.session_id = "ses_check"
        self.proc = FakeProc()

    def events(self):
        yield from self._events


class FakeAdapter:
    def __init__(self, handle):
        self.handle = handle

    def start(self, cwd, prompt, model=None, env=None):
        return self.handle

    def list_models(self):
        return []


class RecordingGitHubClient:
    """A GitHubClient fake that records commit-status calls."""

    def __init__(self, token: str):
        self.token = token
        self.status_calls: list[dict] = []
        self.pr_head_sha = "abc123"
        self._id = 100

    def get_pr(self, full_name, number):
        return {"number": number, "head": "jalebi/7", "head_sha": self.pr_head_sha}

    def find_pr_by_head(self, full_name, head):
        return None

    def create_pr(self, full_name, *, title, body, head, base):
        return 42

    def set_commit_status(self, full_name, sha, state, context, description=None):
        self.status_calls.append(
            {"sha": sha, "state": state, "context": context}
        )
        self._id += 1
        return self._id

    def close(self):
        pass


@pytest.fixture
def git_remote(tmp_path) -> str:
    remote = tmp_path / "remote.git"
    src = tmp_path / "src"
    _git(["init", "--bare", str(remote)])
    _git(["init", str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "f.txt").write_text("hello\n")
    _git(["-C", str(src), "add", "f.txt"])
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
    row.check_runs_enabled = True
    session.commit()
    return row


@pytest.fixture(autouse=True)
def _token(app):
    secrets.add_github_token(app.config["JALEBI_CONFIG"], "test", "ghp_test")
    yield
    secrets.remove_github_token(app.config["JALEBI_CONFIG"], "test")


def _install(monkeypatch, q, handle, client):
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: FakeAdapter(handle))
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: client)


def _done_events() -> list[AgentEvent]:
    return [AgentEvent(type="message", text="ok"), AgentEvent(type="done")]


class RaisingHandle:
    """A handle whose stream raises mid-run, exercising the queue's exception path."""

    def __init__(self):
        self.proc = FakeProc()
        self.session_id = "ses_raise"

    def events(self):
        raise RuntimeError("boom")
        yield  # pragma: no cover - generator marker


def _add_pr_head_ref(git_remote: str, pr_number: int = 7) -> str:
    """Create ``refs/pull/<n>/head`` in the bare remote at ``main``'s HEAD."""
    head = _git(["-C", git_remote, "rev-parse", "HEAD"])
    _git(["-C", git_remote, "update-ref", f"refs/pull/{pr_number}/head", head])
    return head


def _create_issue_fix(session, repo_row):
    return tasks.create_task(
        session,
        type_="issue_fix",
        repo_id=repo_row.id,
        prompt="Fix the bug.",
        source_branch="main",
        target_branch="main",
        pat_name="test",
        issues=[1],
    )


def _create_pr_review(session, repo_row):
    return tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_row.id,
        prompt="Review the PR.",
        target_branch="main",
        pat_name="test",
        prs=[7],
    )


def test_state_mapping():
    assert state_for_status("done") == STATE_SUCCESS
    assert state_for_status("failed") == STATE_FAILURE
    assert state_for_status("timed_out") == STATE_FAILURE
    assert state_for_status("cancelled") == STATE_ERROR
    assert state_for_status("interrupted") == STATE_ERROR
    assert state_for_status("needs_approval") == STATE_PENDING
    assert state_for_status("weird") == STATE_PENDING


def test_status_context():
    from jalebi.db import Task

    fix = Task(id=1, type="issue_fix", repo_id=1, prompt="x")
    review = Task(id=2, type="pr_review", repo_id=1, prompt="x")
    assert status_context(fix) == "Jalebi / fix"
    assert status_context(review) == "Jalebi / review"


def test_status_enabled_gates_by_type_and_flag(session, repo_row):
    fix = tasks.create_task(
        session, type_="issue_fix", repo_id=repo_row.id, prompt="x", pat_name="test", issues=[1]
    )
    review = tasks.create_task(
        session, type_="pr_review", repo_id=repo_row.id, prompt="x", pat_name="test", prs=[1]
    )
    freeform = tasks.create_task(
        session, type_="freeform", repo_id=repo_row.id, prompt="x", pat_name="test"
    )
    assert checkruns.status_enabled(session, fix, repo_row)
    assert checkruns.status_enabled(session, review, repo_row)
    assert not checkruns.status_enabled(session, freeform, repo_row)
    repo_row.check_runs_enabled = False
    session.commit()
    assert not checkruns.status_enabled(session, fix, repo_row)


def test_pr_review_sets_pending_then_terminal(app, session, repo_row, monkeypatch):
    q = app.config["JALEBI_QUEUE"]
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})
    client = RecordingGitHubClient("t")
    _add_pr_head_ref(repo_row.clone_url, 7)
    task = _create_pr_review(session, repo_row)
    _install(monkeypatch, q, FakeHandle(_done_events()), client)
    q._run_task(task.id)
    session.expire_all()
    t = tasks.get_task(session, task.id)
    assert t is not None
    assert t.status in ("done", "failed")
    # The recording client captured pending (start) + a terminal state (end).
    assert len(client.status_calls) >= 2
    assert client.status_calls[0]["state"] == STATE_PENDING
    assert client.status_calls[0]["sha"] == "abc123"
    assert client.status_calls[0]["context"] == "Jalebi / review"
    last = client.status_calls[-1]["state"]
    if t.status == "done":
        assert last == STATE_SUCCESS
    else:
        assert last == STATE_FAILURE
    # The registry row + task pointer were written (the DB record step ran).
    row = checkruns.latest_for_task(session, task.id)
    assert row is not None
    assert row.status == "completed"
    assert row.head_sha == "abc123"
    assert row.name == "Jalebi / review"
    assert row.github_check_id is not None  # the field the keyword-mismatch bug left NULL
    assert t.check_run_id == row.id


def test_issue_fix_status_created_at_publish(app, session, repo_row, monkeypatch):
    q = app.config["JALEBI_QUEUE"]
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})
    client = RecordingGitHubClient("t")
    task = _create_issue_fix(session, repo_row)
    _install(monkeypatch, q, FakeHandle(_done_events()), client)
    q._run_task(task.id)
    session.expire_all()
    t = tasks.get_task(session, task.id)
    assert t is not None
    assert t.status == "done"
    # Not published yet (manual) — no status row should exist yet (branch unpushed).
    row = checkruns.latest_for_task(session, task.id)
    assert row is None or row.status != "completed"
    # Now publish → a completed status is set on the pushed head.
    from jalebi.git_workspace import GitWorkspace

    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task.id, FULL_NAME, "main")
    (wt / "fix.txt").write_text("fixed\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "fix.txt"])
    _git(["-C", str(wt), "commit", "-m", "fix"])
    client._id = 1000
    pr = q.publish_task(task.id, mode="new_pr")
    assert pr > 0
    row = checkruns.latest_for_task(session, task.id)
    assert row is not None
    assert row.status == "completed"
    assert row.conclusion == STATE_SUCCESS
    assert row.head_sha is not None
    assert any(c["sha"] == row.head_sha for c in client.status_calls)


def test_status_failure_is_non_fatal(app, session, repo_row, monkeypatch):
    """A status API failure must never fail the task."""
    q = app.config["JALEBI_QUEUE"]
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})

    class FailingStatusClient(RecordingGitHubClient):
        def set_commit_status(self, full_name, sha, state, context, description=None):
            raise RuntimeError("GitHub exploded")

    client = FailingStatusClient("t")
    _add_pr_head_ref(repo_row.clone_url, 7)
    task = _create_pr_review(session, repo_row)
    _install(monkeypatch, q, FakeHandle(_done_events()), client)
    q._run_task(task.id)
    session.expire_all()
    t = tasks.get_task(session, task.id)
    assert t is not None
    # The task still reached its terminal state despite status failures.
    assert t.status in ("done", "failed")


def test_exception_path_closes_out_pending_status(app, session, repo_row, monkeypatch):
    """An unexpected run failure must flip the pending status to failure, not
    leave a forever-blocking pending on the PR head."""
    q = app.config["JALEBI_QUEUE"]
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})
    client = RecordingGitHubClient("t")
    _add_pr_head_ref(repo_row.clone_url, 7)
    task = _create_pr_review(session, repo_row)
    _install(monkeypatch, q, RaisingHandle(), client)
    q._run_task(task.id)
    session.expire_all()
    t = tasks.get_task(session, task.id)
    assert t is not None
    assert t.status == "failed"
    # pending was posted at start, then closed out as failure.
    assert any(c["state"] == STATE_PENDING for c in client.status_calls)
    assert client.status_calls[-1]["state"] == STATE_FAILURE
