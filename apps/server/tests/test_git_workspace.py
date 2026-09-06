import os
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


def test_merge_origin_into_failure_without_merge_raises(
    ws: GitWorkspace, remote: str, monkeypatch
) -> None:
    """A merge failure that never started a merge (no MERGE_HEAD — e.g. unrelated
    histories) must NOT run `merge --abort` (it would itself fail) and must raise
    GitWorkspaceError with the git stderr surfaced. The worktree stays clean."""
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")

    calls: list[list[str]] = []

    import subprocess as _sp

    original_run = _sp.run

    def fake_subprocess_run(args, **kwargs):
        calls.append(args)
        # Only the merge itself fails; fetch/rev-parse/abort pass through.
        if args[:3] == ["git", "-C", str(wt)] and "merge" in args and "--abort" not in args:
            return _sp.CompletedProcess(
                args, 128, "", "fatal: refusing to merge unrelated histories"
            )
        return original_run(args, **kwargs)

    monkeypatch.setattr("jalebi.git_workspace.subprocess.run", fake_subprocess_run)
    with pytest.raises(GitWorkspaceError, match="unrelated histories"):
        ws.merge_origin_into(wt, FULL_NAME, "main")

    # merge --abort must never be attempted when no merge is in progress.
    assert not any("--abort" in a for a in calls)


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


def test_create_worktree_from_pr_head_based_on_pr_commit(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """A PR-head worktree starts at the PR head commit on a writable branch."""
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
    wt = ws.create_worktree_from_pr_head(11, FULL_NAME, 1)
    assert _git(["-C", str(wt), "rev-parse", "HEAD"]) == pr_commit
    assert (wt / "prfile.txt").exists()
    assert _git(["-C", str(wt), "symbolic-ref", "--short", "HEAD"]) == "jalebi/11"
    # Resume reuses the existing worktree/branch.
    assert ws.create_worktree_from_pr_head(11, FULL_NAME, 1) == wt


def test_merge_task_into_fork_head_merges_cleanly(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """merge_task_into_fork_head lands the task branch on fork-pr-<N>."""
    src = tmp_path / "clone"
    _git(["clone", remote, str(src)])
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    (src / "prfile.txt").write_text("pr change\n")
    _git(["-C", str(src), "add", "prfile.txt"])
    _git(["-C", str(src), "commit", "-m", "pr change"])
    pr_commit = _git(["-C", str(src), "rev-parse", "HEAD"])
    _git(["-C", str(src), "push", remote, f"{pr_commit}:refs/heads/pr-branch"])
    _git(["-C", remote, "update-ref", "refs/pull/2/head", pr_commit])

    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree_from_pr_head(12, FULL_NAME, 2)
    _add_commit(wt, "fix from jalebi")
    conflicts = ws.merge_task_into_fork_head(12, FULL_NAME, 2)
    assert conflicts == []
    mirror = ws.mirror_path(ws.config.data_dir, FULL_NAME)
    fork_log = _git(["-C", str(mirror), "log", "fork-pr-2", "--format=%s"])
    assert "fix from jalebi" in fork_log
    assert "pr change" in fork_log


# ---- Phase 4 T1.2 — diff_against_base --------------------------------------


def test_diff_against_base_shows_only_unique_work(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """The cherry-pick-aware merge-base diff must show the agent's work and NOT
    show an unrelated main advance that was later picked up by the branch."""
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    _add_commit(wt, "agent unique work")  # commit unique to jalebi/1

    # main advances with a DIFFERENT file — would normally appear in a naive
    # two-dot diff but is invisible to the merge-base diff (it's base-side).
    src = tmp_path / "src2"
    _git(["clone", remote, str(src)])
    (src / "other.txt").write_text("target advanced\n")
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    _git(["-C", str(src), "add", "other.txt"])
    _git(["-C", str(src), "commit", "-m", "target advanced"])
    _git(["-C", str(src), "push", "origin", "main"])

    diff = ws.diff_against_base(wt, "main")
    assert "agent unique work" in diff
    assert "other.txt" not in diff


def test_diff_against_base_collapses_when_merged(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """When ``jalebi/1`` points at the same commit as ``origin/main`` (the
    branch has been fast-forwarded / rebased onto main, or the only commit was
    applied as patch-equivalent), the cumulative diff collapses to empty.

    We force the branch to origin/main's SHA to make the collapse
    deterministic — ``git log --cherry-pick`` is heuristic and can miss
    trivially-equivalent single-line patches.
    """
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    _add_commit(wt, "agent unique work")  # commit unique to jalebi/1

    # Mirror the same change onto origin/main.
    src = tmp_path / "src2"
    _git(["clone", remote, str(src)])
    (src / "file.txt").write_text("hello agent unique work\n")
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    _git(["-C", str(src), "add", "file.txt"])
    _git(["-C", str(src), "commit", "-m", "agent unique work on main"])
    _git(["-C", str(src), "push", "origin", "main"])

    ws.ensure_mirror(FULL_NAME, remote)
    main_sha = _git(
        [
            "-C",
            str(ws.mirror_path(ws.config.data_dir, FULL_NAME)),
            "rev-parse",
            "origin/main",
        ]
    )
    # Force jalebi/1 to main's SHA so the diff is fully collapsed.
    _run_git = __import__("jalebi.git_workspace", fromlist=["_run_git"])._run_git
    _run_git(
        [
            "-C",
            str(ws.mirror_path(ws.config.data_dir, FULL_NAME)),
            "update-ref",
            "refs/heads/jalebi/1",
            main_sha,
        ]
    )

    diff = ws.diff_against_base(wt, "main")
    assert diff == ""


# ---- Phase 4 T1.3 — diff_with_untracked -------------------------------------


def test_diff_with_untracked_appends_pseudo_hunks(ws: GitWorkspace, remote: str) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    (wt / "new.txt").write_text("hello\nworld\n")
    (wt / "binary.bin").write_bytes(b"\x00\x01\x03 binary contents")
    (wt / ".gitignore").write_text("ignored.txt\n")
    (wt / "ignored.txt").write_text("should be skipped")
    (wt / "file.txt").write_text("hello\nagent change\n")
    _git(["-C", str(wt), "add", "file.txt"])
    _git(
        [
            "-C",
            str(wt),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "modify file.txt",
        ]
    )

    tracked = ws.diff_against_target(wt, "main")
    combined = ws.diff_with_untracked(wt, tracked)

    # The tracked hunk is preserved (modified file.txt).
    assert "file.txt" in combined
    # The new untracked file has a synthetic add-hunk.
    assert "new file mode" in combined
    assert "+++ b/new.txt" in combined
    # Binary is skipped (NUL byte in first 8 KB).
    assert "binary.bin" not in combined
    # .gitignore-matched file is skipped (the +++ b/ignored.txt hunk never
    # appears — only the untracked .gitignore file itself does).
    assert "+++ b/ignored.txt" not in combined


@pytest.mark.parametrize("target_kind", ["outside", "inside", "missing", "git"])
def test_untracked_diff_skips_symlinks(ws, remote, tmp_path, target_kind) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    outside = tmp_path / "private.txt"
    outside.write_text("private sentinel\n")
    targets = {
        "outside": outside,
        "inside": wt / "file.txt",
        "missing": tmp_path / "missing.txt",
        "git": wt / ".git",
    }
    (wt / "leak.txt").symlink_to(targets[target_kind])
    (wt / "safe.txt").write_text("safe text\n")

    diff = ws.diff_with_untracked(wt, "tracked sentinel")
    assert "tracked sentinel" in diff
    assert "+++ b/safe.txt" in diff
    assert "leak.txt" not in diff
    assert "private sentinel" not in diff


@pytest.mark.parametrize("unsafe_path", ["alias/private.txt", "../private.txt", ".Git/config"])
def test_untracked_diff_rejects_unsafe_paths(ws, tmp_path, monkeypatch, unsafe_path) -> None:
    wt = tmp_path / "worktree"
    wt.mkdir()
    (tmp_path / "private.txt").write_text("private sentinel")
    (wt / "alias").symlink_to(tmp_path, target_is_directory=True)
    (wt / ".Git").mkdir()
    (wt / ".Git" / "config").write_text("private sentinel")
    monkeypatch.setattr("jalebi.git_workspace._run_git", lambda *a: unsafe_path)

    assert ws.diff_with_untracked(wt, "tracked") == "tracked"


@pytest.mark.parametrize("component", ["nested", "new.txt"])
def test_untracked_diff_rejects_symlink_swap(ws, tmp_path, monkeypatch, component) -> None:
    wt = tmp_path / "worktree"
    (wt / "nested").mkdir(parents=True)
    (wt / "nested" / "new.txt").write_text("safe text")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "new.txt").write_text("private sentinel")
    monkeypatch.setattr("jalebi.git_workspace._run_git", lambda *a: "nested/new.txt")
    real_open = os.open

    def swap_before_open(path, flags, *, dir_fd=None):
        if path == component and dir_fd is not None:
            target = wt / "nested" if component == "nested" else wt / "nested" / "new.txt"
            target.rename(target.with_name("saved"))
            target.symlink_to(outside if component == "nested" else outside / "new.txt")
        return real_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr("jalebi.git_workspace.os.open", swap_before_open)
    assert ws.diff_with_untracked(wt, "tracked") == "tracked"


# ---- Phase 4 T1.4 — predict_conflicts ---------------------------------------


def test_predict_conflicts_clean_and_conflicting(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """Clean merge → []; same-file divergent change → [('content', 'file.txt')]."""
    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")

    # Advance main with an unrelated file — should be a clean merge.
    src = tmp_path / "src2"
    _git(["clone", remote, str(src)])
    (src / "other.txt").write_text("target advanced\n")
    _git(["-C", str(src), "config", "user.email", "t@example.com"])
    _git(["-C", str(src), "config", "user.name", "Test"])
    _git(["-C", str(src), "add", "other.txt"])
    _git(["-C", str(src), "commit", "-m", "target advanced"])
    _git(["-C", str(src), "push", "origin", "main"])

    # Refetch so origin/main moves on the mirror.
    ws.ensure_mirror(FULL_NAME, remote)
    assert ws.predict_conflicts(FULL_NAME, 1, "main") == []

    # Now diverge both sides on the same file.
    (wt / "file.txt").write_text("hello\nagent side\n")
    _git(["-C", str(wt), "add", "file.txt"])
    _git(
        [
            "-C",
            str(wt),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "agent side",
        ]
    )
    (src / "file.txt").write_text("hello\nremote side\n")
    _git(["-C", str(src), "add", "file.txt"])
    _git(["-C", str(src), "commit", "-m", "remote side"])
    _git(["-C", str(src), "push", "origin", "main"])
    ws.ensure_mirror(FULL_NAME, remote)

    conflicts = ws.predict_conflicts(FULL_NAME, 1, "main")
    assert ("content", "file.txt") in conflicts


# ---- Phase 4 T1.5 — assert_publish_branch -----------------------------------


def test_assert_publish_branch_ok(ws: GitWorkspace, remote: str) -> None:
    ws.ensure_mirror(FULL_NAME, remote)
    ws.create_worktree(1, FULL_NAME, "main")
    # HEAD on jalebi/1 → no-op.
    ws.assert_publish_branch(1)


def test_assert_publish_branch_mismatch_raises(
    ws: GitWorkspace, remote: str, tmp_path
) -> None:
    """If the worktree HEAD is on a different branch (the agent checked out /
    detached), publishing must refuse with a clear error."""
    from jalebi.git_workspace import GitWorkspaceError

    ws.ensure_mirror(FULL_NAME, remote)
    wt = ws.create_worktree(1, FULL_NAME, "main")
    # Move HEAD off the jalebi branch onto main.
    _git(["-C", str(wt), "checkout", "main"])
    with pytest.raises(GitWorkspaceError, match="branch mismatch"):
        ws.assert_publish_branch(1)
