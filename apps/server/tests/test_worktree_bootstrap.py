"""Tests for the per-task worktree bootstrap (gh guard, git identity, AGENTS.md)."""

import json
import subprocess

from jalebi import worktree_bootstrap


def _git(args: list[str], cwd) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "f.txt").write_text("hi\n")
    _git(["add", "f.txt"], path)
    _git(["commit", "-qm", "init"], path)


def test_write_opencode_guard_denies_gh(tmp_path) -> None:
    worktree_bootstrap.write_opencode_guard(tmp_path)
    guard = json.loads((tmp_path / "opencode.json").read_text())
    bash = guard["permission"]["bash"]
    assert bash["gh *"] == "deny"
    assert bash["/usr/bin/gh*"] == "deny"
    assert bash["*"] == "allow"


def test_set_git_identity(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.set_git_identity(tmp_path)
    assert _git(["config", "user.name"], tmp_path) == "Jalebi"
    assert _git(["config", "user.email"], tmp_path) == "jalebi@localhost"


def test_write_agent_md(tmp_path) -> None:
    path = worktree_bootstrap.write_agent_md(tmp_path, "no gh here")
    assert path.read_text() == "no gh here"


def test_bootstrap_is_idempotent(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    first = (tmp_path / "opencode.json").read_text()
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    assert (tmp_path / "opencode.json").read_text() == first
    assert (tmp_path / "AGENTS.md").is_file()
    assert _git(["config", "user.name"], tmp_path) == "Jalebi"


def test_write_gitignore_appends_jalebi(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    worktree_bootstrap.write_gitignore(tmp_path)
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert ".jalebi/" in lines
    assert "node_modules/" in lines
    # idempotent — no duplicate
    worktree_bootstrap.write_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text().splitlines().count(".jalebi/") == 1


def test_precommit_hook_rejects_jalebi_staging(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path)

    (tmp_path / "file.txt").write_text("ok\n")
    _git(["add", "file.txt"], tmp_path)
    _git(["commit", "-m", "normal"], tmp_path)

    # A normal commit works; a forced-stage of .jalebi is rejected by the hook.
    (tmp_path / ".jalebi").mkdir(exist_ok=True)
    (tmp_path / ".jalebi" / "pr.md").write_text("# t\n")
    _git(["add", "-f", ".jalebi/pr.md"], tmp_path)  # -f bypasses the gitignore
    proc = subprocess.run(
        ["git", "commit", "-m", "should fail"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Jalebi" in proc.stderr


def test_bootstrap_marks_hook_executable(tmp_path) -> None:
    _init_repo(tmp_path)
    hook = worktree_bootstrap.write_precommit_hook(tmp_path)
    assert hook is not None
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111


def test_remove_guard_cleans_gitignore_and_hook(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    hook = worktree_bootstrap.write_precommit_hook(tmp_path)
    assert hook is not None and hook.is_file()
    assert (tmp_path / ".gitignore").is_file()

    worktree_bootstrap.remove_guard(tmp_path)
    assert (tmp_path / "opencode.json").exists() is False
    assert (tmp_path / "AGENTS.md").exists() is False
    assert (tmp_path / ".gitignore").exists() is False
    assert hook.exists() is False
