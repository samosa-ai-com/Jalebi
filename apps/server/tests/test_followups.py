"""Follow-up (resume) tests: route validation, masking, and queue resume (PRD F11)."""

import json
import subprocess

import pytest

from jalebi import repos, secrets, settings, tasks
from jalebi.adapters.types import AgentEvent
from jalebi.db import Run, now
from jalebi.git_workspace import GitWorkspace

FULL_NAME = "owner/repo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class FakeProc:
    def poll(self):
        return None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout=None) -> int:
        return 0


class FakeHandle:
    def __init__(self, events, session_id: str = "ses_orig"):
        self._events = list(events)
        self.session_id = session_id
        self.proc = FakeProc()

    def events(self):
        yield from self._events


class ResumeAdapter:
    def __init__(self, handle):
        self.handle = handle
        self.resume_calls: list[dict[str, object]] = []

    def start(self, cwd, prompt, model=None, env=None):
        return self.handle

    def resume(self, cwd, session_id, prompt, model=None, env=None):
        self.resume_calls.append(
            {"cwd": cwd, "session_id": session_id, "prompt": prompt, "model": model}
        )
        return self.handle

    def list_models(self):
        return []


class FakeGitHubClient:
    def __init__(self, token: str):
        self.token = token

    def find_pr_by_head(self, full_name, head) -> int | None:
        return None

    def create_pr(self, full_name, *, title, body, head, base) -> int:
        return 77

    def get_pr(self, full_name, number) -> dict:
        return {"number": number, "state": "open"}

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _fake_token(config, monkeypatch):
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
        session,
        full_name=FULL_NAME,
        default_branch="main",
        clone_url=git_remote,
        pat_name="test",
    )
    return row


@pytest.fixture
def q(app):
    return app.config["JALEBI_QUEUE"]


def _done_task_with_session(
    session, repo_id: int, session_id: str = "ses_orig", model: str | None = None
):
    task = tasks.create_task(
        session, type_="freeform", repo_id=repo_id, prompt="do it", model=model
    )
    run = Run(
        task_id=task.id,
        seq=1,
        session_id=session_id,
        status="done",
        started_at=now(),
        finished_at=now(),
    )
    session.add(run)
    task.status = "done"
    session.commit()
    return task


def _seed_commit(q, task_id: int, clone_url: str) -> None:
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, clone_url)
    wt = git.create_worktree(task_id, FULL_NAME, "main")
    (wt / "f.txt").write_text("changed\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "f.txt"])
    _git(["-C", str(wt), "commit", "-m", "change"])


def make_review_git(wt):
    """Fake GitWorkspace: pr_review follow-ups must land in the review worktree."""

    class ReviewGit:
        def __init__(self, config):
            self.config = config

        @staticmethod
        def worktree_path(data_dir, task_id):
            return wt

        def ensure_mirror(self, *a, **k):
            return None

        def create_review_worktree(self, *a, **k):
            return wt

        def create_worktree(self, *a, **k):
            raise AssertionError("pr_review follow-up must resume in the review worktree")

    return ReviewGit


class RecordingGitHub(FakeGitHubClient):
    """FakeGitHubClient that records posted PR reviews."""

    def __init__(self, token: str):
        super().__init__(token)
        self.posted: list[tuple[str, int, str]] = []

    def post_pr_review(self, full_name, pr_number, body):
        self.posted.append((full_name, pr_number, body))


def _review_task_with_resumable_session(
    session, repo_id: int, prs: list[int] | None = None
):
    prs = prs or [3]
    task = tasks.create_task(
        session,
        type_="pr_review",
        repo_id=repo_id,
        prompt="review it",
        prs=prs,
        context={"prs": [{"number": prs[0]}]},
    )
    run = Run(
        task_id=task.id,
        seq=1,
        session_id="ses_orig",
        status="done",
        started_at=now(),
        finished_at=now(),
    )
    session.add(run)
    task.status = "done"
    session.commit()
    return task


def _with_assignment(session, task, agent_id: str = "reviewer-1", pr_number: int = 3):
    from jalebi.db import ReviewAssignment

    session.add(
        ReviewAssignment(
            task_id=task.id,
            agent_id=agent_id,
            pr_number=pr_number,
            repo_id=task.repo_id,
            status="posted",
            created_at=now(),
        )
    )
    session.commit()


# -- route tests ---------------------------------------------------------


def test_followup_route_enqueues_masked_body(app, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id)
    enqueued: list[tuple[int, str, str | None, str | None]] = []
    q = app.config["JALEBI_QUEUE"]
    monkeypatch.setattr(
        q,
        "enqueue_followup",
        lambda tid, body, pat_name=None, model=None: enqueued.append((tid, body, pat_name, model)),
    )

    client = app.test_client()
    resp = client.post(
        f"/api/tasks/{task.id}/followup", json={"prompt": "use ghp_test here"}
    )
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["id"] == task.id
    assert body["followups"] == []

    # No row at route time — the worker records it when the resume runs.
    session.expire_all()
    assert tasks.list_followups(session, task.id) == []
    # Masked body is what gets enqueued (no PAT/model override → None).
    assert enqueued == [(task.id, "use *** here", None, None)]


def test_followup_route_validations(app, session, repo_row) -> None:
    client = app.test_client()

    assert client.post("/api/tasks/999/followup", json={"prompt": "x"}).status_code == 404

    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    task.status = "done"
    session.commit()
    resp = client.post(f"/api/tasks/{task.id}/followup", json={"prompt": "x"})
    assert resp.status_code == 409
    assert "no resumable session" in resp.get_json()["error"]

    task2 = _done_task_with_session(session, repo_row.id)
    task2.status = "running"
    session.commit()
    resp = client.post(f"/api/tasks/{task2.id}/followup", json={"prompt": "x"})
    assert resp.status_code == 409

    resp = client.post(f"/api/tasks/{task2.id}/followup", json={"prompt": "   "})
    assert resp.status_code == 400


# -- queue tests ---------------------------------------------------------


def test_followup_resumes_session_in_same_worktree(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    handle = FakeHandle(
        [AgentEvent(type="message", text="more work"), AgentEvent(type="done")],
        session_id="ses_orig",
    )
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "do more")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"

    runs = tasks.runs_for_task(session, task.id)
    assert len(runs) == 2
    assert runs[1].seq == 2
    assert runs[1].session_id == "ses_orig"
    assert runs[1].status == "done"
    step_types = [s["type"] for s in json.loads(runs[1].steps_json or "[]")]
    assert step_types == ["message", "done"]

    assert len(adapter.resume_calls) == 1
    call = adapter.resume_calls[0]
    assert call["cwd"] == str(GitWorkspace.worktree_path(q.config.data_dir, task.id))
    assert call["session_id"] == "ses_orig"
    assert str(call["prompt"]).startswith("do more\n")
    assert "AGENTS.md" in str(call["prompt"])

    fups = tasks.list_followups(session, task.id)
    assert len(fups) == 1
    assert fups[0].body == "do more"
    assert fups[0].run_id == runs[0].id


def test_followup_auto_publishes_when_ahead(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    _seed_commit(q, task.id, repo_row.clone_url)

    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)
    monkeypatch.setattr("jalebi.queue.GitHubClient", FakeGitHubClient)

    q._run_followup(task.id, "do more")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"
    assert fresh.pr_number == 77


def test_followup_reuses_existing_pr(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", True)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    task.pr_number = 5
    session.commit()
    _seed_commit(q, task.id, repo_row.clone_url)

    class NoCreatePR(FakeGitHubClient):
        def create_pr(self, full_name, *, title, body, head, base) -> int:
            raise AssertionError("create_pr must not be called when a PR already exists")

    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: ResumeAdapter(handle))
    monkeypatch.setattr("jalebi.queue.GitHubClient", NoCreatePR)

    q._run_followup(task.id, "do more")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"
    assert fresh.pr_number == 5
    fups = tasks.list_followups(session, task.id)
    assert [f.body for f in fups] == ["do more"]


def test_followup_without_session_marks_failed(q, session, repo_row) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")

    q._run_followup(task.id, "do more")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "failed"


def test_pr_review_followup_resumes_in_review_worktree(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """pr_review sessions live in the review worktree, so follow-ups must resume there."""
    settings.set_setting(session, "auto_publish", False)
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
    run = Run(
        task_id=task.id,
        seq=1,
        session_id="ses_orig",
        status="done",
        started_at=now(),
        finished_at=now(),
    )
    session.add(run)
    task.status = "done"
    session.commit()

    review_wt = tmp_path / "review-wt"
    (review_wt / ".jalebi").mkdir(parents=True)
    (review_wt / ".jalebi" / "review.md").write_text("Verdict: fine.\n")

    monkeypatch.setattr("jalebi.queue.GitWorkspace", make_review_git(review_wt))
    monkeypatch.setattr(
        "jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None
    )

    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)
    monkeypatch.setattr("jalebi.queue.GitHubClient", RecordingGitHub)

    q._run_followup(task.id, "more review")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"
    assert len(adapter.resume_calls) == 1
    call = adapter.resume_calls[0]
    assert call["cwd"] == str(review_wt)
    assert call["session_id"] == "ses_orig"
    fups = tasks.list_followups(session, task.id)
    assert len(fups) == 1
    assert fups[0].body == "more review"


def test_pr_review_followup_posts_review_to_github(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """A pr_review follow-up that writes a review posts it to the PR (F7.4)."""
    from jalebi import reviews as reviews_service

    settings.set_setting(session, "auto_publish", False)
    task = _review_task_with_resumable_session(session, repo_row.id, prs=[3])
    _with_assignment(session, task)

    review_wt = tmp_path / "review-wt"
    (review_wt / ".jalebi").mkdir(parents=True)
    # Include the test token to prove the follow-up path masks before posting.
    (review_wt / ".jalebi" / "review.md").write_text(
        "**Verdict:** needs work\n\n"
        "- **High** `a.py:10` — leaked ghp_test\n"
        "- **Med** `b.py:20` — reuse the wrap helper\n"
    )

    monkeypatch.setattr("jalebi.queue.GitWorkspace", make_review_git(review_wt))
    monkeypatch.setattr(
        "jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None
    )
    recording = RecordingGitHub("ghp_test")
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: recording)

    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "more review")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "done"

    assert len(recording.posted) == 1
    full_name, pr_number, body = recording.posted[0]
    assert full_name == FULL_NAME
    assert pr_number == 3
    assert "**Verdict:** needs work" in body
    assert "leaked ***" in body  # token masked before posting
    assert "Review posted by [Jalebi]" in body  # wrap header

    assignment = reviews_service.assignment_by_task(session, task.id)
    assert assignment is not None
    assert assignment.status == "posted"
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert run.status == "done"
    assert run.steps_json and "Review posted to PR #3." in run.steps_json

    fups = tasks.list_followups(session, task.id)
    assert [f.body for f in fups] == ["more review"]


def test_pr_review_followup_without_review_marks_failed(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """A done pr_review follow-up with no review content is a failure, not done."""
    from jalebi import reviews as reviews_service

    settings.set_setting(session, "auto_publish", False)
    # Isolate the deliverable-validation behavior from auto-recovery.
    settings.set_setting(session, "retry_policy", {"auto_retry": False})
    task = _review_task_with_resumable_session(session, repo_row.id, prs=[3])
    _with_assignment(session, task)

    review_wt = tmp_path / "review-wt"
    review_wt.mkdir(parents=True)

    monkeypatch.setattr("jalebi.queue.GitWorkspace", make_review_git(review_wt))
    monkeypatch.setattr(
        "jalebi.queue.worktree_bootstrap.bootstrap_worktree", lambda *a, **k: None
    )

    # Agent emits `done` but neither wrote .jalebi/review.md nor a final message.
    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "more review")

    session.expire_all()
    fresh = tasks.get_task(session, task.id)
    assert fresh is not None
    assert fresh.status == "failed"

    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert run.status == "failed"
    assert run.steps_json and "without writing a review" in run.steps_json

    assignment = reviews_service.assignment_by_task(session, task.id)
    assert assignment is not None
    assert assignment.status == "failed"
    assert assignment.run_id == run.id


def test_followup_forwards_model_override(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "switch model", model="opencode-go/m9")

    assert len(adapter.resume_calls) == 1
    assert adapter.resume_calls[0]["model"] == "opencode-go/m9"
    session.expire_all()
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert run.model == "opencode-go/m9"


def test_followup_no_model_uses_task_model(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(
        session, repo_row.id, session_id="ses_orig", model="opencode-go/m1"
    )
    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "keep model")

    assert adapter.resume_calls[0]["model"] == "opencode-go/m1"


def test_chained_sequential_followups(q, session, repo_row, monkeypatch) -> None:
    """Two follow-ups in a row each resume the latest resumable run (T-10)."""
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")

    for body in ("first follow-up", "second follow-up"):
        handle = FakeHandle(
            [AgentEvent(type="message", text=body), AgentEvent(type="done")],
            session_id="ses_orig",
        )
        adapter = ResumeAdapter(handle)
        monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli, a=adapter: a)
        q._run_followup(task.id, body)

    session.expire_all()
    runs = tasks.runs_for_task(session, task.id)
    assert len(runs) == 3  # original + two follow-ups
    assert [r.status for r in runs] == ["done", "done", "done"]
    fups = tasks.list_followups(session, task.id)
    assert [f.body for f in fups] == ["first follow-up", "second follow-up"]
    # each follow-up resumed the immediately-previous run
    assert [f.run_id for f in fups] == [runs[0].id, runs[1].id]
