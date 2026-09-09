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
    enqueued: list[tuple[int, str, str | None, str | None, str | None]] = []
    q = app.config["JALEBI_QUEUE"]
    monkeypatch.setattr(
        q,
        "enqueue_followup",
        lambda tid, body, pat_name=None, model=None, cli=None: enqueued.append(
            (tid, body, pat_name, model, cli)
        ),
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
    # Masked body is what gets enqueued (no PAT/model/cli override → None).
    assert enqueued == [(task.id, "use *** here", None, None, None)]


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


def test_failed_followup_still_records_row(q, session, repo_row, monkeypatch) -> None:
    """A follow-up that dies by exception (after its run row exists) records
    its row anyway — otherwise the attempt leaves no trace."""
    settings.set_setting(session, "auto_publish", False)
    settings.set_setting(session, "retry_policy", {"auto_retry": False})
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")

    class ExplodingAdapter(ResumeAdapter):
        def start(self, *args, **kwargs):
            raise RuntimeError("spawn blew up")

    monkeypatch.setattr(
        "jalebi.queue.get_adapter", lambda cli: ExplodingAdapter(FakeHandle([]))
    )

    q._run_followup(task.id, "doomed attempt", cli="kilo")

    session.expire_all()
    runs = tasks.runs_for_task(session, task.id)
    assert runs[-1].status == "failed"
    fups = tasks.list_followups(session, task.id)
    assert len(fups) == 1
    assert fups[0].body == "doomed attempt"
    assert fups[0].run_id == runs[-1].id
    assert fups[0].cli == "kilo"


def test_followup_row_records_cli_override(q, session, repo_row, monkeypatch) -> None:
    """A successful backend-switch follow-up records the requested backend."""
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    run = tasks.latest_run(session, task.id)
    assert run is not None
    run.cli = "opencode"
    session.commit()
    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_new")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "switch backend", cli="codex")

    session.expire_all()
    fups = tasks.list_followups(session, task.id)
    assert len(fups) == 1
    assert fups[0].cli == "codex"


def test_followup_row_body_excludes_agent_instructions(
    q, session, repo_row, monkeypatch
) -> None:
    """Catalog custom_instructions join the prompt but must not pollute the
    stored follow-up body (which should read as what the user typed)."""
    from jalebi import catalog

    catalog.create_agent(
        session,
        id="instructor",
        name="Instructor",
        kind="general",
        cli="opencode",
        personality_md="Teach.",
        custom_instructions="Always explain like I'm five.",
        enabled=True,
    )
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    task.agent_id = "instructor"
    session.commit()
    handle = FakeHandle(
        [AgentEvent(type="message", text="ok"), AgentEvent(type="done")],
        session_id="ses_orig",
    )
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "do more")

    assert "like I'm five" in str(adapter.resume_calls[0]["prompt"])
    session.expire_all()
    fups = tasks.list_followups(session, task.id)
    assert len(fups) == 1
    assert fups[0].body == "do more"


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


PLAN_ONLY_STEPS = json.dumps(
    [
        {"type": "step", "text": None, "phase": None, "ts": "t"},
        {"type": "message", "text": "Plan: read the diff, run checks.", "phase": None, "ts": "t"},
        {
            "type": "message",
            "text": "Please approve the review plan above, and I'll begin.",
            "phase": None,
            "ts": "t",
        },
    ]
)


def test_pr_review_plan_only_run_posts_nothing(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """Task 68 regression: a done run ending in an approval ask with no
    review.md posts NOTHING to the PR (previously the plan went out via the
    _last_message fallback). Run/task stay done; assignment stays running."""
    from jalebi import reviews as reviews_service

    task = _review_task_with_resumable_session(session, repo_row.id, prs=[3])
    _with_assignment(session, task)
    reviews_service.set_assignment_status(session, task.id, "running")
    run = tasks.latest_run(session, task.id)
    assert run is not None
    run.steps_json = PLAN_ONLY_STEPS
    session.commit()

    wt = tmp_path / "review-wt"
    wt.mkdir(parents=True)  # no .jalebi/review.md
    recording = RecordingGitHub("ghp_test")
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: recording)

    q._post_review(session, task, repo_row, 3, wt, run, "tok", lambda s: s)

    assert recording.posted == []
    session.expire_all()
    fresh_run = tasks.latest_run(session, task.id)
    assert fresh_run is not None
    assert fresh_run.status == "done"
    fresh_task = tasks.get_task(session, task.id)
    assert fresh_task is not None
    assert fresh_task.status == "done"
    assignment = reviews_service.assignment_by_task(session, task.id)
    assert assignment is not None
    assert assignment.status == "running"


def test_pr_review_fallback_still_posts_non_approval_message(
    q, session, repo_row, monkeypatch, tmp_path
) -> None:
    """Guard the gate: a real final message (no approval ask) with no
    review.md still posts via the _last_message fallback."""
    task = _review_task_with_resumable_session(session, repo_row.id, prs=[3])
    _with_assignment(session, task)
    run = tasks.latest_run(session, task.id)
    assert run is not None
    run.steps_json = json.dumps(
        [{"type": "message", "text": "Verdict: looks good, merge it.", "phase": None, "ts": "t"}]
    )
    session.commit()

    wt = tmp_path / "review-wt"
    wt.mkdir(parents=True)  # no .jalebi/review.md
    recording = RecordingGitHub("ghp_test")
    monkeypatch.setattr("jalebi.queue.GitHubClient", lambda token: recording)

    q._post_review(session, task, repo_row, 3, wt, run, "tok", lambda s: s)

    assert len(recording.posted) == 1
    assert "Verdict: looks good" in recording.posted[0][2]


class RecordingIssueClient:
    def __init__(self):
        self.comments: list[tuple[str, int, str]] = []

    def comment_on_issue(self, full_name, number, body):
        self.comments.append((full_name, number, body))


def test_publish_issue_comments_skipped_on_approval_wait(q, session, repo_row) -> None:
    """Publish-time issue link comments stay silent when the run ended asking
    for approval (the push/PR itself is unaffected — only the comment)."""
    task = tasks.create_task(
        session, type_="issue_fix", repo_id=repo_row.id, prompt="fix it", issues=[12]
    )
    run = Run(
        task_id=task.id,
        seq=1,
        session_id="ses_1",
        status="done",
        started_at=now(),
        finished_at=now(),
    )
    run.steps_json = PLAN_ONLY_STEPS
    session.add(run)
    session.commit()

    recording = RecordingIssueClient()
    q._comment_on_issues(recording, FULL_NAME, task, 77, session=session)
    assert recording.comments == []

    # Control: a normal final message still gets its issue comment.
    run.steps_json = json.dumps(
        [{"type": "message", "text": "Fixed and pushed.", "phase": None, "ts": "t"}]
    )
    session.commit()
    q._comment_on_issues(recording, FULL_NAME, task, 77, session=session)
    assert len(recording.comments) == 1
    assert recording.comments[0][:2] == (FULL_NAME, 12)


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


def test_followup_same_backend_resumes(q, session, repo_row, monkeypatch) -> None:
    """A follow-up backend override matching the session's own still resumes."""
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    run = tasks.latest_run(session, task.id)
    assert run is not None
    run.cli = "opencode"
    session.commit()
    handle = FakeHandle([AgentEvent(type="done")], session_id="ses_orig")
    adapter = ResumeAdapter(handle)
    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: adapter)

    q._run_followup(task.id, "keep backend", cli="opencode")

    assert len(adapter.resume_calls) == 1
    assert adapter.resume_calls[0]["session_id"] == "ses_orig"
    prompt = adapter.resume_calls[0]["prompt"]
    assert isinstance(prompt, str)
    assert "## Prior conversation" not in prompt


def test_followup_backend_change_forks_fresh_session_with_history(
    q, session, repo_row, monkeypatch
) -> None:
    """Changing the backend on a follow-up can't resume the old session (each CLI
    owns its session format): it starts a fresh run seeded with the prior
    conversation instead (the UI notes this)."""
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_orig")
    run = tasks.latest_run(session, task.id)
    assert run is not None
    run.cli = "opencode"
    run.steps_json = json.dumps(
        [
            {"type": "message", "text": "I implemented the fix.", "phase": None, "ts": "t"},
            {
                "type": "tool_call",
                "text": '{"tool": "bash", "command": "pwd"}',
                "phase": None,
                "ts": "t",
            },
        ]
    )
    session.commit()

    started: list[dict[str, object]] = []
    resumed: list[dict[str, object]] = []

    class StartAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            started.append({"cwd": cwd, "prompt": prompt, "model": model})
            return FakeHandle([AgentEvent(type="done")], session_id="ses_new")

        def resume(self, cwd, session_id, prompt, model=None, env=None):
            resumed.append({"session_id": session_id, "prompt": prompt})
            return FakeHandle([AgentEvent(type="done")], session_id="ses_orig")

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: StartAdapter())

    q._run_followup(task.id, "switch to codex", cli="codex")

    assert len(started) == 1
    assert len(resumed) == 0
    prompt = started[0]["prompt"]
    assert isinstance(prompt, str)
    assert "## Prior conversation" in prompt
    assert "I implemented the fix." in prompt
    assert "(tool)" in prompt
    session.expire_all()
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert run.session_id == "ses_new"
    assert run.cli == "codex"


def test_followup_legacy_backend_override_forks_fresh_session(
    q, session, repo_row, monkeypatch
) -> None:
    """A legacy run (prev.cli is NULL) + an explicit follow-up backend override
    must fork a fresh session, not attempt a resume on the wrong backend.

    Previously, ``fork = bool(prev.cli) and cli != prev.cli`` meant a legacy
    run with a user-supplied follow-up backend would call
    ``adapter.resume(prev_session_id)`` on a backend whose session id format
    didn't match — the run failed mid-stream. Treat the explicit override as
    authoritative: fork and seed the prior conversation.
    """
    settings.set_setting(session, "auto_publish", False)
    task = _done_task_with_session(session, repo_row.id, session_id="ses_legacy")
    # No `run.cli` — a legacy run predating the per-run cli column.
    run = tasks.latest_run(session, task.id)
    assert run is not None
    run.cli = None
    run.steps_json = json.dumps(
        [{"type": "message", "text": "legacy action", "phase": None, "ts": "t"}]
    )
    session.commit()

    started: list[dict[str, object]] = []
    resumed: list[dict[str, object]] = []

    class StartAdapter:
        def start(self, cwd, prompt, model=None, env=None):
            started.append({"cwd": cwd, "prompt": prompt, "model": model})
            return FakeHandle([AgentEvent(type="done")], session_id="ses_new")

        def resume(self, cwd, session_id, prompt, model=None, env=None):
            resumed.append({"session_id": session_id, "prompt": prompt})
            return FakeHandle([AgentEvent(type="done")], session_id="ses_legacy")

        def list_models(self):
            return []

    monkeypatch.setattr("jalebi.queue.get_adapter", lambda cli: StartAdapter())

    q._run_followup(task.id, "switch backend on legacy run", cli="codex")

    assert len(started) == 1
    assert len(resumed) == 0
    prompt = started[0]["prompt"]
    assert isinstance(prompt, str)
    assert "## Prior conversation" in prompt
    assert "legacy action" in prompt
    session.expire_all()
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert run.session_id == "ses_new"
    assert run.cli == "codex"


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
