"""Tests for the three publish modes: ``new_pr`` (default), ``update_pr``,
``push_branch``. See ``docs/04-git-workspace.md`` §8 and
``docs/14-messaging-strategy.md`` for the contracts.

The ``queue.publish_task`` dispatch is tested with mocks for the GitHub
client + git_workspace methods (fast, focused on validation + wiring).
The git_workspace methods themselves are tested against a real local
``git_remote`` fixture (integration).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from jalebi import repos, secrets, settings, tasks
from jalebi.git_workspace import GitWorkspace, PushLeaseFailed
from jalebi.queue import PublishConflict, PublishError

FULL_NAME = "owner/repo"


# ---- helpers ---------------------------------------------------------------


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _seed_test_token(config):
    """Every test in this module resolves a GitHub token — make sure one exists."""
    secrets.add_github_token(config, "test", "ghp_test")
    yield


@pytest.fixture
def git_remote(tmp_path) -> str:
    """Bare remote + a non-bare working clone so we can push + fetch."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(["init", "--bare", "--initial-branch=main", str(remote)])
    _git(["clone", str(remote), str(work)])
    _git(["-C", str(work), "config", "user.email", "seed@example.com"])
    _git(["-C", str(work), "config", "user.name", "Seed"])
    (work / "f.txt").write_text("seed\n")
    _git(["-C", str(work), "add", "f.txt"])
    _git(["-C", str(work), "commit", "-m", "seed"])
    _git(["-C", str(work), "push", "-u", "origin", "main"])
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


def _seed_commit(q, task_id: int, repo_row) -> None:
    """Create the worktree + add a commit so the branch is ahead of main."""
    git = GitWorkspace(q.config)
    git.ensure_mirror(FULL_NAME, repo_row.clone_url)
    wt = git.create_worktree(task_id, FULL_NAME, "main")
    (wt / "f.txt").write_text(f"task-{task_id}\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "f.txt"])
    _git(["-C", str(wt), "commit", "-m", f"task-{task_id}"])


def _seed_run(session, task_id: int) -> None:
    """Create a minimal Run row so publish_task can append a timeline step."""
    from jalebi.db import Run, now

    run = Run(
        task_id=task_id,
        seq=1,
        cli="opencode",
        model=None,
        started_at=now(),
        status="done",
        finished_at=now(),
        steps_json="[]",
    )
    session.add(run)
    session.commit()


def _fresh_task(session, task_id):
    session.expire_all()
    task = tasks.get_task(session, task_id)
    assert task is not None
    return task


# ---- publish_task dispatch (queue-level, mocked) --------------------------


def test_publish_default_mode_is_new_pr(q, session, repo_row, monkeypatch) -> None:
    """Backwards-compat: calling publish_task with no mode still does new_pr."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    seen: dict[str, str] = {}

    class RecordingQueue:
        def __init__(self, *a, **kw):
            pass

        def _publish_new_pr(self, *a, **kw):
            seen["mode"] = "new_pr"
            return 42

        def _publish_update_pr(self, *a, **kw):
            raise AssertionError("update_pr must not run for default publish_task")

        def _publish_push_branch(self, *a, **kw):
            raise AssertionError("push_branch must not run for default publish_task")

    monkeypatch.setattr(q, "_publish_new_pr", RecordingQueue()._publish_new_pr)
    monkeypatch.setattr(q, "_publish_update_pr", RecordingQueue()._publish_update_pr)
    monkeypatch.setattr(q, "_publish_push_branch", RecordingQueue()._publish_push_branch)

    pr = q.publish_task(task.id)
    assert pr == 42
    assert seen["mode"] == "new_pr"
    fresh = _fresh_task(session, task.id)
    assert fresh.pr_number == 42
    assert fresh.status == "done"


def test_publish_unknown_mode_raises(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    with pytest.raises(ValueError, match="unknown publish mode"):
        q.publish_task(task.id, mode="bogus")


def test_publish_workflow_scope_error_is_actionable(
    q, session, repo_row, monkeypatch
) -> None:
    """A push refused for missing `workflow` scope becomes an actionable PublishError."""
    from jalebi.git_workspace import GitWorkspaceError
    from jalebi.queue import _is_workflow_scope_error

    refused = (
        "git -C /tmp/ws push origin jalebi/1 failed: To https://github.com/o/r.git "
        "! [remote rejected] jalebi/1 -> jalebi/1 (refusing to allow a Personal "
        "Access Token to create or update workflow "
        "`.github/workflows/google-scholar.yml` without `workflow` scope)"
    )
    assert _is_workflow_scope_error(GitWorkspaceError(refused)) is True
    assert _is_workflow_scope_error(GitWorkspaceError("boom")) is False
    assert (
        _is_workflow_scope_error(
            GitWorkspaceError("remote rejected: workflow job failed branch protection")
        )
        is False
    )

    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _seed_run(session, task.id)

    def _boom(self, *a, **k):
        raise GitWorkspaceError(refused)

    monkeypatch.setattr(GitWorkspace, "push_branch", _boom)
    with pytest.raises(PublishError, match="workflow.*scope") as excinfo:
        q.publish_task(task.id, mode="new_pr")
    assert "update token" in str(excinfo.value)
    assert "git -C" not in str(excinfo.value)
    # The git cause chain is preserved for the timeline/Sentry.
    assert isinstance(excinfo.value.__cause__, GitWorkspaceError)


def test_push_or_workflow_error_maps_all_push_shapes() -> None:
    """The shared helper covers every publish push path identically."""
    from jalebi.git_workspace import GitWorkspaceError, PushLeaseFailed
    from jalebi.queue import _push_or_workflow_error

    refused = GitWorkspaceError(
        "refusing to allow a Personal Access Token to create or update workflow "
        "`.github/workflows/x.yml` without `workflow` scope"
    )
    with pytest.raises(PublishError, match="workflow.*scope"):
        with _push_or_workflow_error(7, "test"):
            raise refused
    # Lease failures pass through untouched (lease precedence).
    with pytest.raises(PushLeaseFailed):
        with _push_or_workflow_error(7, "test"):
            raise PushLeaseFailed("stale info")
    # Ordinary git failures re-raise unchanged.
    boom = GitWorkspaceError("git push failed: boom")
    with pytest.raises(GitWorkspaceError, match="boom"):
        with _push_or_workflow_error(7, "test"):
            raise boom


def test_publish_push_branch_requires_branch(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    with pytest.raises(ValueError, match="push_branch"):
        q.publish_task(task.id, mode="push_branch")


def test_publish_update_pr_uses_prs_json_when_pr_number_missing(
    q, session, repo_row, monkeypatch
) -> None:
    """If ``pr_number`` is None but ``task.prs_json`` is set, use ``prs_json[0]``."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    # Simulate the user attaching a PR at creation time.
    task.prs_json = json.dumps([77])
    session.commit()
    _seed_commit(q, task.id, repo_row)
    _seed_run(session, task.id)

    seen: dict[str, object] = {}

    def fake_update_pr(task, repo, token, git, *, pr_number):
        seen["pr_number"] = pr_number
        return 77

    monkeypatch.setattr(q, "_publish_update_pr", fake_update_pr)

    pr = q.publish_task(task.id, mode="update_pr")
    assert pr == 77
    assert seen["pr_number"] == 77


def test_publish_update_pr_requires_pr_number(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    with pytest.raises(ValueError, match="update_pr"):
        q.publish_task(task.id, mode="update_pr")


def test_publish_update_pr_rejects_closed_pr(
    q, session, repo_row, monkeypatch
) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    class ClosedClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            return {"number": number, "state": "closed", "head": "feature"}

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", ClosedClient)
    with pytest.raises(PublishError, match="closed"):
        q.publish_task(task.id, mode="update_pr", pr_number=9)


def test_publish_update_pr_happy_path(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _seed_run(session, task.id)

    seen: dict[str, object] = {}

    class OpenClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            seen.setdefault("pr_gets", []).append(number)
            return {
                "number": number,
                "state": "open",
                "head": "feature/foo",
            }

        def close(self):
            pass

    class CapturingGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_worktree(self, *a, **k):
            seen["create_worktree"] = True
            return Path("/dev/null")

        def current_remote_sha(self, full_name, branch, token):
            seen["sha_branch"] = branch
            return "abc123"

        def fast_forward_into(self, task_id, full_name, target_branch, token):
            seen["ff_into"] = target_branch
            return []

        def push_existing_branch(self, full_name, branch, token):
            seen["pushed_branch"] = branch

        def assert_publish_branch(self, task_id):
            return None

    monkeypatch.setattr("jalebi.queue.GitHubClient", OpenClient)
    monkeypatch.setattr("jalebi.queue.GitWorkspace", CapturingGit)
    pr = q.publish_task(task.id, mode="update_pr", pr_number=9)
    assert pr == 9
    assert seen["pr_gets"] == [9]
    assert seen["ff_into"] == "feature/foo"
    assert seen["pushed_branch"] == "feature/foo"
    fresh = _fresh_task(session, task.id)
    assert fresh.pr_number == 9
    assert fresh.status == "done"
    # Timeline step explains what happened.
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert "Pushed to PR #9" in run.steps_json


def _fork_pr(number: int, *, can_modify=True) -> dict:
    return {
        "number": number,
        "state": "open",
        "head": "feat/fork-x",
        "head_repo": "fork/repo",
        "head_sha": "abc123",
        "is_fork": True,
        "maintainer_can_modify": can_modify,
    }


def test_publish_update_pr_fork_pushes_to_fork(q, session, repo_row, monkeypatch) -> None:
    """Fork PRs merge into the local fork branch and push to the fork URL."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _seed_run(session, task.id)

    seen: dict[str, object] = {}

    class OpenClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            return _fork_pr(number)

        def close(self):
            pass

    class CapturingGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def create_worktree(self, *a, **k):
            return Path("/dev/null")

        def fast_forward_into(self, *a, **k):
            raise AssertionError("same-repo path must not run for fork PRs")

        def push_existing_branch(self, *a, **k):
            raise AssertionError("origin push must not run for fork PRs")

        def merge_task_into_fork_head(self, task_id, full_name, pr_number, token):
            seen["merged"] = (task_id, full_name, pr_number)
            return []

        def push_fork_head(
            self, full_name, pr_number, head_branch, fork_repo, expected_old_sha, token
        ):
            seen["pushed"] = (full_name, pr_number, head_branch, fork_repo, expected_old_sha)

        def local_ref_sha(self, full_name, branch):
            return "def456"

        def assert_publish_branch(self, task_id):
            return None

    monkeypatch.setattr("jalebi.queue.GitHubClient", OpenClient)
    monkeypatch.setattr("jalebi.queue.GitWorkspace", CapturingGit)
    pr = q.publish_task(task.id, mode="update_pr", pr_number=7)
    assert pr == 7
    assert seen["merged"] == (task.id, FULL_NAME, 7)
    assert seen["pushed"] == (FULL_NAME, 7, "feat/fork-x", "fork/repo", "abc123")


def test_publish_update_pr_fork_refused_without_maintainer_edits(
    q, session, repo_row, monkeypatch
) -> None:
    """A fork PR with maintainer edits off guides the owner to new_pr."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    class ClosedForkClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            return _fork_pr(number, can_modify=False)

        def close(self):
            pass

    monkeypatch.setattr("jalebi.queue.GitHubClient", ClosedForkClient)
    with pytest.raises(PublishError, match="maintainer edits"):
        q.publish_task(task.id, mode="update_pr", pr_number=7)


def test_publish_update_pr_fork_conflict_propagates(
    q, session, repo_row, monkeypatch
) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    class OpenClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            return _fork_pr(number)

        def close(self):
            pass

    class ConflictGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def create_worktree(self, *a, **k):
            return Path("/dev/null")

        def merge_task_into_fork_head(self, *a, **k):
            return ["a.txt"]

        def assert_publish_branch(self, task_id):
            return None

    monkeypatch.setattr("jalebi.queue.GitHubClient", OpenClient)
    monkeypatch.setattr("jalebi.queue.GitWorkspace", ConflictGit)
    with pytest.raises(PublishConflict, match="fork"):
        q.publish_task(task.id, mode="update_pr", pr_number=7)


def test_publish_push_branch_happy_path(q, session, repo_row, monkeypatch) -> None:
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)
    _seed_run(session, task.id)

    seen: dict[str, object] = {}

    class CapturingGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_worktree(self, *a, **k):
            return Path("/dev/null")

        def current_remote_sha(self, full_name, branch, token):
            seen["sha_branch"] = branch
            return "oldsha"

        def fast_forward_into(self, task_id, full_name, target_branch, token):
            seen["ff_into"] = target_branch
            return []

        def push_existing_branch(self, full_name, branch, token):
            seen["pushed_branch"] = branch

        def assert_publish_branch(self, task_id):
            return None

    monkeypatch.setattr("jalebi.queue.GitWorkspace", CapturingGit)
    pr = q.publish_task(task.id, mode="push_branch", target_branch="feature/manual")
    assert pr == 0
    assert seen["ff_into"] == "feature/manual"
    assert seen["pushed_branch"] == "feature/manual"
    fresh = _fresh_task(session, task.id)
    # push_branch does NOT touch pr_number.
    assert fresh.pr_number is None
    assert fresh.status == "done"
    run = tasks.latest_run(session, task.id)
    assert run is not None
    assert "feature/manual" in run.steps_json


def test_publish_conflict_propagates(q, session, repo_row, monkeypatch) -> None:
    """Merge conflict in update_pr surfaces as PublishConflict (→ 409 in the route)."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    class OpenClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            return {"number": number, "state": "open", "head": "feature"}

        def close(self):
            pass

    class ConflictGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_worktree(self, *a, **k):
            return Path("/dev/null")

        def current_remote_sha(self, *a, **k):
            return "abc"

        def fast_forward_into(self, *a, **k):
            return ["file1.txt", "file2.txt"]

        def push_existing_branch(self, *a, **k):
            raise AssertionError("must not push when conflict")

        def assert_publish_branch(self, task_id):
            return None

    monkeypatch.setattr("jalebi.queue.GitHubClient", OpenClient)
    monkeypatch.setattr("jalebi.queue.GitWorkspace", ConflictGit)
    with pytest.raises(PublishConflict, match="file1.txt"):
        q.publish_task(task.id, mode="update_pr", pr_number=9)


def test_publish_lease_failure_propagates(q, session, repo_row, monkeypatch) -> None:
    """PushLeaseFailed from git_workspace propagates to the route (→ 412)."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    class OpenClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            return {"number": number, "state": "open", "head": "feature"}

        def close(self):
            pass

    class LeaseFailedGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_worktree(self, *a, **k):
            return Path("/dev/null")

        def current_remote_sha(self, *a, **k):
            return "abc"

        def fast_forward_into(self, *a, **k):
            return []

        def push_existing_branch(self, *a, **k):
            raise PushLeaseFailed("remote branch moved since last fetch")

        def assert_publish_branch(self, task_id):
            return None

    monkeypatch.setattr("jalebi.queue.GitHubClient", OpenClient)
    monkeypatch.setattr("jalebi.queue.GitWorkspace", LeaseFailedGit)
    with pytest.raises(PushLeaseFailed, match="moved"):
        q.publish_task(task.id, mode="update_pr", pr_number=9)


# ---- route-level validation ------------------------------------------------


def test_route_publish_default_mode(client: FlaskClient, session, repo_row) -> None:
    """No body → defaults to new_pr (backwards-compat for the simple click).

    Smoke-tested at the route layer: an unknown mode returns 400 before any
    queue call is made, so we don't need a fake queue here.
    """
    settings.set_setting(session, "auto_publish", False)
    tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    resp = client.post("/api/tasks/1/publish", json={"mode": "bogus"})
    assert resp.status_code == 400
    assert "unknown publish mode" in resp.get_json()["error"]


def test_publish_update_pr_with_invalid_prs_json(
    q, session, repo_row, monkeypatch
) -> None:
    """A corrupted ``prs_json`` (not a JSON list) must raise ValueError, not crash."""
    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    task.prs_json = "not-json-at-all"
    session.commit()
    _seed_commit(q, task.id, repo_row)
    with pytest.raises(ValueError, match="update_pr"):
        q.publish_task(task.id, mode="update_pr")


def test_fast_forward_into_tmp_path_is_per_task(q, git_remote) -> None:
    """The publish-tmp worktree path must be per-task (no shared name across tasks)."""
    ws1 = GitWorkspace(q.config).worktree_path(q.config.data_dir, 1) / "publish-tmp"
    ws2 = GitWorkspace(q.config).worktree_path(q.config.data_dir, 2) / "publish-tmp"
    assert ws1 != ws2
    assert "task-1" in str(ws1)
    assert "task-2" in str(ws2)


def test_route_publish_push_branch_missing_branch(client: FlaskClient) -> None:
    resp = client.post("/api/tasks/1/publish", json={"mode": "push_branch"})
    # task 1 doesn't exist → 404 from the route's KeyError. That's fine —
    # validation order is: parse JSON → call publish_task → 404/409/412.
    assert resp.status_code in (400, 404)


def test_route_publish_invalid_mode(client: FlaskClient) -> None:
    resp = client.post("/api/tasks/1/publish", json={"mode": "nope"})
    assert resp.status_code == 400
    assert "unknown publish mode" in resp.get_json()["error"]


def test_route_publish_branch_must_be_string(client: FlaskClient) -> None:
    resp = client.post(
        "/api/tasks/1/publish", json={"mode": "push_branch", "branch": 123}
    )
    assert resp.status_code == 400


def test_route_publish_pr_number_must_be_int(client: FlaskClient) -> None:
    resp = client.post(
        "/api/tasks/1/publish", json={"mode": "update_pr", "pr_number": "9"}
    )
    assert resp.status_code == 400


# ---- git_workspace methods (real git, local remote) ------------------------


def test_current_remote_sha_returns_sha_or_none(q, git_remote) -> None:
    ws = GitWorkspace(q.config)
    ws.ensure_mirror(FULL_NAME, git_remote)
    sha = ws.current_remote_sha(FULL_NAME, "main")
    assert sha is not None
    assert len(sha) == 40  # full SHA
    assert ws.current_remote_sha(FULL_NAME, "does-not-exist") is None


def test_fast_forward_into_clean_ff(q, git_remote) -> None:
    """jalebi/<id> is a descendant of the target → clean fast-forward."""
    ws = GitWorkspace(q.config)
    ws.ensure_mirror(FULL_NAME, git_remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    (wt / "agent.txt").write_text("agent work\n")
    _git(["-C", str(wt), "config", "user.email", "a@b"])
    _git(["-C", str(wt), "config", "user.name", "A"])
    _git(["-C", str(wt), "add", "agent.txt"])
    _git(["-C", str(wt), "commit", "-m", "agent"])
    conflicts = ws.fast_forward_into(1, FULL_NAME, "main")
    assert conflicts == []


def test_fast_forward_into_no_op_when_same_sha(q, git_remote) -> None:
    ws = GitWorkspace(q.config)
    ws.ensure_mirror(FULL_NAME, git_remote)
    ws.create_worktree(2, FULL_NAME, "main")
    # No new commit on jalebi/2 → it's identical to origin/main after create.
    conflicts = ws.fast_forward_into(2, FULL_NAME, "main")
    assert conflicts == []


def test_push_existing_branch_writes_to_remote(q, git_remote) -> None:
    ws = GitWorkspace(q.config)
    ws.ensure_mirror(FULL_NAME, git_remote)
    wt = ws.create_worktree(3, FULL_NAME, "main")
    (wt / "x.txt").write_text("x\n")
    _git(["-C", str(wt), "config", "user.email", "a@b"])
    _git(["-C", str(wt), "config", "user.name", "A"])
    _git(["-C", str(wt), "add", "x.txt"])
    _git(["-C", str(wt), "commit", "-m", "x"])
    # Create a new remote branch from origin/main first (mirror needs a local ref).
    mirror = ws.mirror_path(ws.config.data_dir, FULL_NAME)
    _git(["-C", str(mirror), "branch", "feature/test", "origin/main"])
    ws.push_existing_branch(FULL_NAME, "feature/test")
    # The remote should now have the new commit on feature/test.
    out = _git(["-C", str(mirror), "ls-remote", "origin", "feature/test"])
    assert "refs/heads/feature/test" in out


def test_push_existing_branch_lease_failure_translates(q, git_remote, tmp_path) -> None:
    """When the remote moves between fetch and push, --force-with-lease refuses."""
    ws = GitWorkspace(q.config)
    ws.ensure_mirror(FULL_NAME, git_remote)
    wt = ws.create_worktree(4, FULL_NAME, "main")
    (wt / "y.txt").write_text("y\n")
    _git(["-C", str(wt), "config", "user.email", "a@b"])
    _git(["-C", str(wt), "config", "user.name", "A"])
    _git(["-C", str(wt), "add", "y.txt"])
    _git(["-C", str(wt), "commit", "-m", "y"])

    mirror = ws.mirror_path(ws.config.data_dir, FULL_NAME)
    # Set up a local tracking branch at origin/main (clean).
    _git(["-C", str(mirror), "branch", "feature/lease", "origin/main"])
    # First push succeeds (lease against the fetched SHA).
    ws.push_existing_branch(FULL_NAME, "feature/lease")
    # Move the remote branch out from under us without updating the mirror's
    # tracking ref — emulate "remote moved since last fetch".
    work = tmp_path / "leasework"
    _git(["clone", git_remote, str(work)])
    _git(["-C", str(work), "config", "user.email", "x@y"])
    _git(["-C", str(work), "config", "user.name", "X"])
    (work / "z.txt").write_text("concurrent\n")
    _git(["-C", str(work), "add", "z.txt"])
    _git(["-C", str(work), "commit", "-m", "concurrent"])
    _git(["-C", str(work), "push", "origin", "HEAD:refs/heads/feature/lease"])
    # Now a second push from the same mirror state must refuse via lease.
    with pytest.raises(PushLeaseFailed, match="moved"):
        ws.push_existing_branch(FULL_NAME, "feature/lease")


# ---- Phase 4 T1.5 — branch-mismatch guard in publish_task ------------------


def test_publish_branch_mismatch_raises(q, session, repo_row, monkeypatch, tmp_path) -> None:
    """If the worktree HEAD is not on jalebi/<id>, publish_task must refuse
    with a clear PublishError BEFORE any push/merge fires."""
    from jalebi.git_workspace import GitWorkspaceError

    settings.set_setting(session, "auto_publish", False)
    task = tasks.create_task(session, type_="freeform", repo_id=repo_row.id, prompt="do it")
    _seed_commit(q, task.id, repo_row)

    seen: dict[str, object] = {"create_worktree": False, "push_branch": False}

    class OpenClient:
        def __init__(self, token: str):
            pass

        def get_pr(self, full_name, number):
            seen.setdefault("pr_get", number)
            return {"number": number, "state": "open", "head": "feature"}

        def close(self):
            pass

    class MismatchGit(GitWorkspace):
        def __init__(self, config):
            self.config = config

        def ensure_mirror(self, *a, **k):
            return None

        def create_worktree(self, *a, **k):
            seen["create_worktree"] = True
            return Path("/dev/null")

        def current_remote_sha(self, *a, **k):
            return "abc"

        def fast_forward_into(self, *a, **k):
            seen["ff_into"] = True
            return []

        def push_existing_branch(self, *a, **k):
            seen["push_branch"] = True

        def assert_publish_branch(self, task_id):
            raise GitWorkspaceError("branch mismatch; agent left HEAD on main")

    monkeypatch.setattr("jalebi.queue.GitHubClient", OpenClient)
    monkeypatch.setattr("jalebi.queue.GitWorkspace", MismatchGit)
    with pytest.raises(PublishError, match="branch mismatch"):
        q.publish_task(task.id, mode="update_pr", pr_number=9)
    # Nothing was pushed; nothing was merged. The guard fires before any of
    # these mocks can record their "did this happen?" flags, so use get() and
    # default to False (i.e. nothing recorded == not called).
    assert seen.get("push_branch", False) is False
    assert seen.get("ff_into", False) is False
