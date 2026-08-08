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


def test_write_opencode_guard_denies_gh_and_external_dirs(tmp_path) -> None:
    worktree_bootstrap.write_opencode_guard(tmp_path)
    guard = json.loads((tmp_path / "opencode.json").read_text())
    bash = guard["permission"]["bash"]
    assert bash["gh *"] == "deny"
    assert bash["/usr/bin/gh*"] == "deny"
    assert bash["*"] == "allow"
    # The agent must not be able to touch paths outside the worktree (overrides
    # the owner's global external_directory: "allow" via config merge).
    assert guard["permission"]["external_directory"] == "deny"


def test_set_git_identity(tmp_path) -> None:
    from jalebi import messaging

    _init_repo(tmp_path)
    worktree_bootstrap.set_git_identity(tmp_path)
    assert _git(["config", "user.name"], tmp_path) == messaging.CO_AUTHOR_NAME
    assert _git(["config", "user.email"], tmp_path) == messaging.CO_AUTHOR_EMAIL


def test_write_agent_md(tmp_path) -> None:
    path = worktree_bootstrap.write_agent_md(tmp_path, "no gh here")
    text = path.read_text()
    assert text.startswith(worktree_bootstrap.JALEBI_MD_START)
    assert text.endswith(worktree_bootstrap.JALEBI_MD_END + "\n")
    assert "no gh here" in text


def test_write_agent_md_preserves_tracked_repo_content(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Repo agents\n\nOriginal repo instructions.\n")
    _git(["add", "AGENTS.md"], tmp_path)
    _git(["commit", "-qm", "add agents.md"], tmp_path)
    worktree_bootstrap.write_agent_md(tmp_path, "jalebi instructions")
    text = (tmp_path / "AGENTS.md").read_text()
    assert "# Repo agents" in text
    assert "Original repo instructions." in text
    assert "jalebi instructions" in text
    assert worktree_bootstrap.JALEBI_MD_START in text
    # Idempotent re-bootstrap: repo content preserved once, Jalebi block replaced.
    worktree_bootstrap.write_agent_md(tmp_path, "updated instructions")
    text2 = (tmp_path / "AGENTS.md").read_text()
    assert text2.count("Original repo instructions.") == 1
    assert "updated instructions" in text2
    assert "jalebi instructions" not in text2


def test_write_agent_md_handles_repo_content_quoting_markers(tmp_path) -> None:
    """A repo AGENTS.md that itself mentions the marker strings must be untouched."""
    _init_repo(tmp_path)
    original = (
        "# Repo agents\n\n"
        "See the `<!-- jalebi:start -->` docs for how we signal blocks — "
        "remember the `<!-- jalebi:end -->` comment too.\n"
    )
    (tmp_path / "AGENTS.md").write_text(original)
    _git(["add", "AGENTS.md"], tmp_path)
    _git(["commit", "-qm", "add agents.md"], tmp_path)

    worktree_bootstrap.write_agent_md(tmp_path, "jalebi instructions")
    text = (tmp_path / "AGENTS.md").read_text()
    # The repo's own marker-quoting sentence is fully preserved.
    assert "how we signal blocks" in text
    assert "comment too" in text

    worktree_bootstrap.remove_guard(tmp_path)
    assert (tmp_path / "AGENTS.md").read_text() == original


def test_remove_guard_keeps_preexisting_untracked_agent_md(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Local notes\n")
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    worktree_bootstrap.remove_guard(tmp_path)
    assert (tmp_path / "AGENTS.md").read_text() == "# Local notes\n"


def test_bootstrap_is_idempotent(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    first = (tmp_path / "opencode.json").read_text()
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    assert (tmp_path / "opencode.json").read_text() == first
    assert (tmp_path / "AGENTS.md").is_file()
    assert _git(["config", "user.name"], tmp_path) == "Jalebi"


def test_bootstrap_writes_agent_skills(tmp_path) -> None:
    _init_repo(tmp_path)
    skills = [
        {"name": "secure-coding", "content": "# Secure coding\nNever eval."},
        {"name": "owasp-top10", "content": "# OWASP\n"},
    ]
    worktree_bootstrap.bootstrap_worktree(tmp_path, skills=skills)
    sk = tmp_path / ".claude" / "skills"
    assert (sk / "secure-coding" / "SKILL.md").read_text() == "# Secure coding\nNever eval."
    assert (sk / "owasp-top10" / "SKILL.md").read_text() == "# OWASP\n"


def test_bootstrap_without_skills_clears_stale(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, skills=[{"name": "old", "content": "x"}])
    assert (tmp_path / ".claude" / "skills" / "old" / "SKILL.md").is_file()
    # A re-bootstrap without skills (task no longer uses a catalog agent) drops them.
    worktree_bootstrap.bootstrap_worktree(tmp_path, skills=None)
    assert not (tmp_path / ".claude" / "skills" / "old").exists()


def test_bootstrap_removes_skills_removed_from_agent(tmp_path) -> None:
    """A changed skill list prunes subdirs that are no longer referenced (a
    rerun of the same worktree after the agent was edited must match the catalog)."""
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(
        tmp_path,
        skills=[
            {"name": "keep", "content": "k"},
            {"name": "drop", "content": "d"},
        ],
    )
    assert (tmp_path / ".claude" / "skills" / "drop" / "SKILL.md").is_file()
    worktree_bootstrap.bootstrap_worktree(
        tmp_path,
        skills=[{"name": "keep", "content": "k2"}, {"name": "new", "content": "n"}],
    )
    assert not (tmp_path / ".claude" / "skills" / "drop").exists()
    assert (tmp_path / ".claude" / "skills" / "keep" / "SKILL.md").read_text() == "k2"
    assert (tmp_path / ".claude" / "skills" / "new" / "SKILL.md").is_file()


def test_skills_excluded_from_git_add(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(
        tmp_path, skills=[{"name": "secure-coding", "content": "# x\n"}]
    )
    _git(["add", "."], tmp_path)
    staged = _git(["diff", "--cached", "--name-only"], tmp_path)
    assert ".claude" not in staged
    assert staged == ""  # bootstrap files (incl. skills) never get staged


def test_remove_guard_removes_skills(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(
        tmp_path, skills=[{"name": "secure-coding", "content": "# x\n"}]
    )
    assert (tmp_path / ".claude" / "skills").is_dir()
    # A repo's own .claude content must be preserved — only Jalebi's skills dir goes.
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    worktree_bootstrap.remove_guard(tmp_path)
    assert not (tmp_path / ".claude" / "skills").exists()
    assert (tmp_path / ".claude" / "settings.json").read_text() == "{}"


def test_info_exclude_keeps_bootstrap_files_out(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path)

    # git add . must not stage opencode.json / AGENTS.md / .jalebi
    (tmp_path / ".jalebi").mkdir(exist_ok=True)
    (tmp_path / ".jalebi" / "pr.md").write_text("# t\n")
    _git(["add", "."], tmp_path)
    staged = _git(["diff", "--cached", "--name-only"], tmp_path)
    assert staged == ""  # nothing new to stage — bootstrap files are excluded
    # git status does not show them either
    status = _git(["status", "--porcelain"], tmp_path)
    assert "opencode.json" not in status
    assert "AGENTS.md" not in status
    assert ".jalebi" not in status


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


def test_precommit_hook_rejects_marked_agents_md(tmp_path) -> None:
    # A repo that tracks AGENTS.md: the marked Jalebi section must not be committed.
    _init_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Repo agents\n")
    _git(["add", "AGENTS.md"], tmp_path)
    _git(["commit", "-qm", "track agents.md"], tmp_path)

    worktree_bootstrap.bootstrap_worktree(tmp_path)
    assert "jalebi:start" in (tmp_path / "AGENTS.md").read_text()

    proc = subprocess.run(
        ["git", "add", "AGENTS.md"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    proc = subprocess.run(
        ["git", "commit", "-m", "should fail"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "AGENTS.md" in proc.stderr

    # Removing the marker block lets a legitimate AGENTS.md commit through.
    (tmp_path / "AGENTS.md").write_text("# Repo agents\n\nUpdated instructions.\n")
    _git(["add", "AGENTS.md"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-m", "legit update"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_bootstrap_marks_hook_executable(tmp_path) -> None:
    _init_repo(tmp_path)
    hook = worktree_bootstrap.write_precommit_hook(tmp_path)
    assert hook is not None
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111


def test_remove_guard_restores_agent_md_and_cleans_up(tmp_path) -> None:
    _init_repo(tmp_path)
    original = "# Repo agents\n\nOriginal instructions.\n"
    (tmp_path / "AGENTS.md").write_text(original)
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    hook = worktree_bootstrap.write_precommit_hook(tmp_path)
    assert hook is not None and hook.is_file()

    worktree_bootstrap.remove_guard(tmp_path)
    assert (tmp_path / "opencode.json").exists() is False
    # AGENTS.md restored exactly to its original content (marker block stripped).
    assert (tmp_path / "AGENTS.md").read_text() == original
    assert hook.exists() is False


def test_remove_guard_on_bootstrap_only_files(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path)
    assert (tmp_path / "AGENTS.md").is_file()
    worktree_bootstrap.remove_guard(tmp_path)
    assert (tmp_path / "AGENTS.md").exists() is False
    assert (tmp_path / "opencode.json").exists() is False


def test_linked_worktrees_share_and_keep_guards(tmp_path) -> None:
    """Linked worktrees share the mirror's info/exclude + hook; remove_guard on
    one must not strip guards another live worktree still needs."""
    mirror = tmp_path / "mirror.git"
    src = tmp_path / "src"
    _git(["init", "--bare", str(mirror)], tmp_path)
    _git(["init", str(src)], tmp_path)
    _git(["-C", str(src), "config", "user.email", "t@example.com"], tmp_path)
    _git(["-C", str(src), "config", "user.name", "Test"], tmp_path)
    (src / "f.txt").write_text("hi\n")
    _git(["-C", str(src), "add", "f.txt"], tmp_path)
    _git(["-C", str(src), "commit", "-m", "init"], tmp_path)
    _git(["-C", str(src), "branch", "-M", "main"], tmp_path)
    _git(["-C", str(src), "remote", "add", "origin", str(mirror)], tmp_path)
    _git(["-C", str(src), "push", "-u", "origin", "main"], tmp_path)

    wt1 = tmp_path / "wt1"
    wt2 = tmp_path / "wt2"
    _git(["-C", str(mirror), "worktree", "add", "-b", "jalebi/1", str(wt1), "main"], tmp_path)
    _git(["-C", str(mirror), "worktree", "add", "-b", "jalebi/2", str(wt2), "main"], tmp_path)

    worktree_bootstrap.bootstrap_worktree(wt1)
    common = worktree_bootstrap._git_common_dir(wt1)
    assert common == mirror.resolve()
    exclude = common / "info" / "exclude"
    for line in worktree_bootstrap.INFO_EXCLUDE_LINES:
        assert line in exclude.read_text()
    hook = common / "hooks" / "pre-commit"
    assert hook.is_file()

    # wt2 is still live → remove_guard keeps the shared guards.
    worktree_bootstrap.remove_guard(wt1)
    assert hook.is_file()
    assert all(
        line in exclude.read_text() for line in worktree_bootstrap.INFO_EXCLUDE_LINES
    )

    # Last worktree gone → remove_guard strips the shared guards.
    _git(["-C", str(mirror), "worktree", "remove", "--force", str(wt2)], tmp_path)
    worktree_bootstrap.remove_guard(wt1)
    assert hook.exists() is False
    # git init's default comment lines may remain; Jalebi's must be gone.
    assert all(
        line not in exclude.read_text() for line in worktree_bootstrap.INFO_EXCLUDE_LINES
    )
