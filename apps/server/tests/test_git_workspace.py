import subprocess
from pathlib import Path

import pytest

from jalebi.config import Config
from jalebi.git_workspace import GitWorkspace, GitWorkspaceError

FULL_NAME = "owner/repo"


def _git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def ws(config: Config) -> GitWorkspace:
    return GitWorkspace(config)


@pytest.fixture
def remote(tmp_path) -> str:
    """A bare remote repo with a `main` branch containing one commit."""
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


def _add_commit(ws_path: Path, message: str) -> None:
    _git(["-C", str(ws_path), "config", "user.email", "t@example.com"])
    _git(["-C", str(ws_path), "config", "user.name", "Test"])
    (ws_path / "file.txt").write_text(f"hello {message}\n")
    _git(["-C", str(ws_path), "add", "file.txt"])
    _git(["-C", str(ws_path), "commit", "-m", message])


def test_naming_helpers() -> None:
    assert GitWorkspace.mirror_path(Path("/d"), FULL_NAME) == Path("/d/repos/owner__repo.git")
    assert GitWorkspace.worktree_path(Path("/d"), 7) == Path("/d/ws/task-7")
    assert GitWorkspace.task_branch(7) == "jalebi/7"


def test_mirror_clone(ws: GitWorkspace, remote: str) -> None:
    mirror = ws.ensure_mirror(FULL_NAME, remote)
    assert mirror.exists()
    refs = _git(["-C", str(mirror), "show-ref"]).splitlines()
    assert any("refs/remotes/origin/main" in line for line in refs)


def test_mirror_incremental_fetch(ws: GitWorkspace, remote: str, tmp_path) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    src = tmp_path / "src2"
    _git(["clone", remote, str(src)])
    _add_commit(src, "second")
    _git(["-C", str(src), "push", "origin", "main"])
    ws.ensure_mirror(FULL_NAME, remote)
    mirror = ws.mirror_path(ws.config.data_dir, FULL_NAME)
    log = _git(["-C", str(mirror), "log", "origin/main", "--format=%s"])
    assert "second" in log


def test_mirror_clone_auth_env(ws: GitWorkspace, monkeypatch) -> None:
    calls: list[tuple[list[str], dict | None]] = []

    def fake_run(args, cwd=None, auth_env=None):
        calls.append((list(args), auth_env))
        return ""

    monkeypatch.setattr("jalebi.git_workspace._run_git", fake_run)
    ws.ensure_mirror(FULL_NAME, "https://x", token="ghp_secret")
    clone_call = next(c for c in calls if "clone" in c[0])
    assert clone_call[1] is not None
    # The behavioral contract: the token value never appears in argv (it travels
    # via the http.extraHeader auth config env instead).
    assert "ghp_secret" not in " ".join(clone_call[0])
    assert "GIT_CONFIG_VALUE_0" in clone_call[1]
    assert "Authorization: basic" in clone_call[1]["GIT_CONFIG_VALUE_0"]
    assert "ghp_secret" not in clone_call[1]["GIT_CONFIG_VALUE_0"]  # base64, not plaintext


def test_worktree_create_and_resume(ws: GitWorkspace, remote: str) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    assert (wt / ".git").is_file()
    assert (wt / "file.txt").read_text() == "hello\n"
    branches = _git(
        [
            "-C",
            str(ws.mirror_path(ws.config.data_dir, FULL_NAME)),
            "branch",
            "--format=%(refname:short)",
        ]
    ).splitlines()
    assert "jalebi/1" in branches

    again = ws.create_worktree(1, FULL_NAME, "main")
    assert again == wt


def test_branch_exists(ws: GitWorkspace, remote: str) -> None:
    assert ws.branch_exists(1, FULL_NAME) is False
    ws.ensure_mirror(FULL_NAME, remote)
    ws.create_worktree(1, FULL_NAME, "main")
    assert ws.branch_exists(1, FULL_NAME) is True
    ws.remove_worktree(1, FULL_NAME)
    assert ws.branch_exists(1, FULL_NAME) is False


def test_reset_branch_to_base_discards_stale_work(ws: GitWorkspace, remote: str) -> None:
    """A stale jalebi/<id> branch (left by a wiped/restored DB) must be reset to
    the CURRENT origin/<base> before a fresh first run — stale work must never
    contaminate a new task that reuses the same task id."""
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    _add_commit(wt, "stale work")
    # Simulate a stale branch surviving a DB wipe: drop the worktree but keep the
    # mirror branch (as if the task's DB row was deleted while the mirror lived).
    mirror = ws.mirror_path(ws.config.data_dir, FULL_NAME)
    _git(["-C", str(mirror), "worktree", "remove", "--force", str(wt)])
    assert ws.branch_exists(1, FULL_NAME) is True

    # A fresh run reuses the stale branch, then must be reset to origin/main.
    wt2 = ws.create_worktree(1, FULL_NAME, "main")
    ws.reset_branch_to_base(1, FULL_NAME, "main")

    # The stale commit is gone; the worktree is back on the base content.
    assert _git(["-C", str(wt2), "log", "--format=%s"]) == "initial"
    assert (wt2 / "file.txt").read_text() == "hello\n"
    assert not _git(["-C", str(wt2), "status", "--porcelain"]).strip()


def test_worktree_requires_mirror(ws: GitWorkspace) -> None:
    with pytest.raises(GitWorkspaceError):
        ws.create_worktree(1, FULL_NAME, "main")


def test_worktree_push(ws: GitWorkspace, remote: str) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    _add_commit(wt, "change")
    ws.push_branch(1, FULL_NAME)
    refs = _git(["-C", remote, "show-ref", "--heads"]).splitlines()
    assert any("refs/heads/jalebi/1" in line for line in refs)
    assert "change" in _git(["-C", remote, "log", "jalebi/1", "--format=%s"])


def test_merge_origin_into_clean_merges_target(ws: GitWorkspace, remote: str, tmp_path) -> None:
    """Publish-time sync: origin/<target> advanced after the worktree branched
    off it — merging it in must succeed and fold target's commits into the task
    branch."""
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    _add_commit(wt, "agent work")

    # Target advances on the remote after the worktree was created (a DIFFERENT
    # file, so the merge is clean — _add_commit would touch file.txt and clash).
    src = tmp_path / "src2"
    _git(["clone", remote, str(src)])
    (src / "other.txt").write_text("new file\n")
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    _git(["-C", str(src), "add", "other.txt"])
    _git(["-C", str(src), "commit", "-m", "target advanced"])
    _git(["-C", str(src), "push", "origin", "main"])

    conflicts = ws.merge_origin_into(wt, FULL_NAME, "main")
    assert conflicts == []
    log = _git(["-C", str(wt), "log", "--format=%s"])
    assert "target advanced" in log
    assert "agent work" in log
    # The merge is a commit on the branch (no conflicted state left behind).
    assert not _git(["-C", str(wt), "status", "--porcelain"]).strip()


def test_merge_origin_into_conflict_aborts_and_reports(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """A conflicting target advance must be aborted (worktree restored) and the
    conflicting paths reported — never left in a conflicted state."""
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    (wt / "file.txt").write_text("hello\nagent\n")
    _git(["-C", str(wt), "config", "user.email", "t@example.com"])
    _git(["-C", str(wt), "config", "user.name", "Test"])
    _git(["-C", str(wt), "add", "file.txt"])
    _git(["-C", str(wt), "commit", "-m", "agent work"])

    # Target changes the same file differently, then advances.
    src = tmp_path / "src2"
    _git(["clone", remote, str(src)])
    (src / "file.txt").write_text("hello\nremote\n")
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    _git(["-C", str(src), "add", "file.txt"])
    _git(["-C", str(src), "commit", "-m", "remote changed same file"])
    _git(["-C", str(src), "push", "origin", "main"])

    conflicts = ws.merge_origin_into(wt, FULL_NAME, "main")
    assert conflicts == ["file.txt"]
    # The merge was aborted: no MERGE_HEAD and no conflicted files left.
    proc = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert _git(["-C", str(wt), "status", "--porcelain"]).strip() == ""



def test_push_auth_env(ws: GitWorkspace, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(args, cwd=None, auth_env=None):
        captured["args"] = args
        captured["auth_env"] = auth_env
        return ""

    monkeypatch.setattr("jalebi.git_workspace._run_git", fake_run)
    ws.push_branch(1, FULL_NAME, token="ghp_secret")
    # Behavioral contract (T-14): token never in argv; auth via extraHeader env.
    assert "ghp_secret" not in " ".join(captured["args"])
    assert captured["auth_env"]["GIT_CONFIG_VALUE_0"].startswith("Authorization: basic ")
    assert "ghp_secret" not in captured["auth_env"]["GIT_CONFIG_VALUE_0"]


def test_worktree_remove(ws: GitWorkspace, remote: str) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    ws.remove_worktree(1, FULL_NAME)
    assert not wt.exists()
    branches = _git(
        ["-C", str(ws.mirror_path(ws.config.data_dir, FULL_NAME)), "branch"]
    ).splitlines()
    assert "jalebi/1" not in branches


def test_worktree_remove_noop(ws: GitWorkspace) -> None:
    ws.remove_worktree(99, FULL_NAME)


def test_create_worktree_prunes_missing_but_registered(ws: GitWorkspace, remote: str) -> None:
    """A task whose worktree directory was deleted WITHOUT unregistering it (e.g.
    task-delete cleanup) leaves the mirror listing it as 'missing but already
    registered' — create_worktree must prune that so a reused task id gets a
    clean worktree instead of failing."""
    import shutil

    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    # Simulate a delete that removed the directory but not the registration:
    # delete the worktree dir as the task-delete route would, WITHOUT pruning.
    shutil.rmtree(wt, ignore_errors=True)
    assert not (wt / ".git").exists()

    # Recreating the same task id must succeed (prune clears the registration).
    wt2 = ws.create_worktree(1, FULL_NAME, "main")
    assert (wt2 / ".git").is_file()
    assert wt2 == wt


def test_remove_worktree_with_missing_dir_prunes(ws: GitWorkspace, remote: str) -> None:
    """remove_worktree on an already-deleted directory must still clear the
    mirror registration so the task id can be reused."""
    import shutil

    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    shutil.rmtree(wt, ignore_errors=True)
    ws.remove_worktree(1, FULL_NAME)  # dir already gone — must prune, not crash
    # The stale registration is gone: recreating the worktree succeeds.
    wt2 = ws.create_worktree(1, FULL_NAME, "main")
    assert (wt2 / ".git").is_file()


def test_clean_git_env_strips_inherited_state(monkeypatch) -> None:
    from jalebi.git_workspace import _clean_git_env

    env = {
        "PATH": "/usr/bin",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_2": "url.insteadOf",
        "GIT_DIR": "/elsewhere",
        "GIT_WORK_TREE": "/x",
        "HOME": "/home/u",
    }
    cleaned = _clean_git_env(dict(env))
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["HOME"] == "/home/u"
    assert "GIT_CONFIG_COUNT" not in cleaned
    assert "GIT_CONFIG_KEY_2" not in cleaned
    assert "GIT_DIR" not in cleaned
    assert "GIT_WORK_TREE" not in cleaned
    assert cleaned["GIT_CONFIG_NOSYSTEM"] == "1"
    assert cleaned["GIT_CONFIG_GLOBAL"]


def test_review_worktree_detached_at_pr_head(ws: GitWorkspace, remote: str, tmp_path) -> None:
    """create_review_worktree checks out refs/pull/N/head DETACHED (T-2)."""
    # A commit reachable ONLY via refs/pull/1/head (not origin/main), so the
    # test fails if the PR-ref fetch is dropped in favour of the default branch.
    src = tmp_path / "clone"
    _git(["clone", remote, str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "prfile.txt").write_text("pr change\n")
    _git(["-C", str(src), "add", "prfile.txt"])
    _git(["-C", str(src), "commit", "-m", "pr change"])
    pr_commit = _git(["-C", str(src), "rev-parse", "HEAD"])
    _git(["-C", str(src), "push", remote, f"{pr_commit}:refs/heads/pr-branch"])
    _git(["-C", remote, "update-ref", "refs/pull/1/head", pr_commit])

    ws.ensure_mirror(FULL_NAME, remote)
    rwt = ws.create_review_worktree(1, FULL_NAME, 1)
    assert rwt.name.endswith("-review")

    assert _git(["-C", str(rwt), "rev-parse", "HEAD"]) == pr_commit
    assert (rwt / "prfile.txt").exists()
    # detached: symbolic-ref -q returns non-zero / empty for a detached HEAD.
    proc = subprocess.run(
        ["git", "-C", str(rwt), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert proc.stdout.strip() == ""
