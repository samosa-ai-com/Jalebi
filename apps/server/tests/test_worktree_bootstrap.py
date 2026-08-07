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
