"""Tests for the per-task worktree bootstrap (gh guard, git identity, AGENTS.md)."""

import json
import subprocess
import sys

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


def test_write_codex_guard_denies_gh(tmp_path) -> None:
    path = worktree_bootstrap.write_codex_guard(tmp_path)
    assert path is not None
    text = path.read_text()
    assert path == tmp_path / ".codex" / "rules" / "default.rules"
    assert 'prefix_rule(pattern=["gh"], decision="forbidden"' in text
    assert "gh is not permitted" in text
    # Inline self-tests (`codex execpolicy check`): gh forbidden, git allowed.
    assert 'match=["gh", "gh pr view 1"]' in text
    assert 'not_match=["git status"]' in text


def test_write_claude_guard_denies_gh(tmp_path) -> None:
    path = worktree_bootstrap.write_claude_guard(tmp_path)
    assert path is not None
    assert path == tmp_path / ".claude" / "settings.json"
    deny = json.loads(path.read_text())["permissions"]["deny"]
    assert {"Bash(gh *)", "Bash(gh)", "Bash(gh**)", "Bash(gh **)"} <= set(deny)


def test_write_claude_guard_does_not_clobber_repo_settings(tmp_path) -> None:
    _init_repo(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {"deny": ["Bash(rm *)"]}}\n')
    assert worktree_bootstrap.write_claude_guard(tmp_path) is None
    assert settings.read_text() == '{"permissions": {"deny": ["Bash(rm *)"]}}\n'
    # The guard (incl. the PreToolUse hook) is skipped entirely for repo-owned settings.
    assert not (tmp_path / ".claude" / "hooks").exists()


def test_write_claude_guard_writes_hook_and_deny_rules(tmp_path) -> None:
    settings_path = worktree_bootstrap.write_claude_guard(tmp_path)
    assert settings_path is not None
    settings = json.loads(settings_path.read_text())
    deny = settings["permissions"]["deny"]
    assert "Bash(gh **)" in deny
    assert "Read(~/.ssh/**)" in deny
    assert "Edit(~/.codex/**)" in deny
    hook_cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert hook_cmd == "python3 " + str(tmp_path / ".claude/hooks/jalebi_deny_external.py")
    hook = tmp_path / ".claude/hooks/jalebi_deny_external.py"
    assert hook.is_file()
    assert hook.read_text() == worktree_bootstrap.CLAUDE_HOOK_SCRIPT


def test_claude_hook_denies_outside_worktree_and_allows_inside(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.write_claude_guard(tmp_path)
    hook = tmp_path / ".claude" / "hooks" / "jalebi_deny_external.py"

    def run(payload: object) -> int:
        proc = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc.returncode

    inside = tmp_path / "src" / "x.py"
    inside.parent.mkdir()
    inside.write_text("x")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    try:
        # In-worktree file tools are allowed.
        assert run({"tool_name": "Read", "tool_input": {"file_path": str(inside)}}) == 0
        out = str(tmp_path / "out.txt")
        assert run({"tool_name": "Write", "tool_input": {"file_path": out}}) == 0
        assert run({"tool_name": "Grep", "tool_input": {"path": str(tmp_path)}}) == 0
        # Anything outside the worktree is denied (exit 2); `~` expands to home.
        assert run({"tool_name": "Read", "tool_input": {"file_path": str(outside)}}) == 2
        assert run({"tool_name": "Edit", "tool_input": {"file_path": str(outside)}}) == 2
        assert run({"tool_name": "Grep", "tool_input": {"path": str(outside.parent)}}) == 2
        assert run({"tool_name": "Read", "tool_input": {"file_path": "~/.ssh/id_rsa"}}) == 2
        # Bash is not intercepted (gh is denied by rules; git inside the worktree works).
        assert run({"tool_name": "Bash", "tool_input": {"command": "git status"}}) == 0
        # Malformed input fails open.
        assert run("not-json") == 0
    finally:
        outside.unlink(missing_ok=True)


def test_write_opencode_guard_does_not_clobber_repo_file(tmp_path) -> None:
    _init_repo(tmp_path)
    guard = tmp_path / "opencode.json"
    guard.write_text('{"repo": true}\n')
    assert worktree_bootstrap.write_opencode_guard(tmp_path) is None
    assert guard.read_text() == '{"repo": true}\n'


def test_write_codex_guard_does_not_clobber_repo_rules(tmp_path) -> None:
    _init_repo(tmp_path)
    rules = tmp_path / ".codex" / "rules" / "default.rules"
    rules.parent.mkdir(parents=True)
    rules.write_text("repo-owned-rule\n")
    assert worktree_bootstrap.write_codex_guard(tmp_path) is None
    assert rules.read_text() == "repo-owned-rule\n"


def test_remove_guard_preserves_repo_owned_opencode_and_codex_rules(tmp_path) -> None:
    _init_repo(tmp_path)
    guard = tmp_path / "opencode.json"
    guard.write_text('{"repo": true}\n')
    rules = tmp_path / ".codex" / "rules" / "default.rules"
    rules.parent.mkdir(parents=True)
    rules.write_text("repo-owned-rule\n")
    worktree_bootstrap.bootstrap_worktree(tmp_path)  # opencode guard skipped (file exists)
    worktree_bootstrap.remove_guard(tmp_path)
    assert guard.read_text() == '{"repo": true}\n'
    assert rules.read_text() == "repo-owned-rule\n"


def test_bootstrap_writes_skills_to_all_roots(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(
        tmp_path, skills=[{"name": "secure-coding", "content": "# x\n"}]
    )
    for root in (".claude", ".codex", ".agents"):
        path = tmp_path / root / "skills" / "secure-coding" / "SKILL.md"
        assert path.read_text() == "# x\n", root


def test_bootstrap_prunes_stale_skills_from_all_roots(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, skills=[{"name": "drop", "content": "d"}])
    worktree_bootstrap.bootstrap_worktree(tmp_path, skills=[{"name": "keep", "content": "k"}])
    for root in (".claude", ".codex", ".agents"):
        assert not (tmp_path / root / "skills" / "drop").exists(), root
        assert (tmp_path / root / "skills" / "keep" / "SKILL.md").read_text() == "k", root


def test_precommit_hook_rejects_staged_claude_settings(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")
    _git(["add", "-f", ".claude/settings.json"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-qm", "bad"], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert ".claude" in proc.stderr


def test_precommit_hook_allows_repo_owned_claude_settings(tmp_path) -> None:
    _init_repo(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {"deny": ["Bash(rm *)"]}}\n')
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")  # guard skipped
    _git(["add", "-f", ".claude/settings.json"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-qm", "track settings"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_precommit_hook_rejects_staged_skills(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(
        tmp_path, skills=[{"name": "secure-coding", "content": "# x\n"}]
    )
    _git(["add", "-f", ".claude/skills/secure-coding/SKILL.md"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-qm", "bad"], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "skills" in proc.stderr


def test_write_guard_dispatches_per_cli(tmp_path) -> None:
    for cli, filename in (
        ("opencode", "opencode.json"),
        ("codex", ".codex/rules/default.rules"),
        ("claude", ".claude/settings.json"),
    ):
        worktree_bootstrap.write_guard(tmp_path, cli)
        assert (tmp_path / filename).is_file(), f"{cli} should write {filename}"


def test_write_guard_unknown_cli_writes_nothing(tmp_path) -> None:
    worktree_bootstrap.write_guard(tmp_path, "gemini")
    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / ".claude").exists()


def test_bootstrap_claude_writes_claude_md_and_settings_guard(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, "jalebi instructions", cli="claude")
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (tmp_path / name).read_text()
        assert "jalebi instructions" in text
        assert worktree_bootstrap.JALEBI_MD_START in text
        assert worktree_bootstrap.JALEBI_MD_END in text
    deny = json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "Bash(gh *)" in deny
    # Re-bootstrap is idempotent: one marker block, updated content.
    worktree_bootstrap.bootstrap_worktree(tmp_path, "updated", cli="claude")
    text = (tmp_path / "CLAUDE.md").read_text()
    assert text.count("jalebi:start") == 1
    assert "updated" in text
    assert "jalebi instructions" not in text


def test_bootstrap_codex_writes_codex_guard(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="codex")
    assert (tmp_path / ".codex" / "rules" / "default.rules").is_file()
    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_write_claude_md_preserves_tracked_repo_content(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Repo\n\nOriginal claude instructions.\n")
    _git(["add", "CLAUDE.md"], tmp_path)
    _git(["commit", "-qm", "add claude.md"], tmp_path)
    worktree_bootstrap.write_claude_md(tmp_path, "jalebi instructions")
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "# Repo" in text and "Original claude instructions." in text
    assert text.count("jalebi:start") == 1
    # Rewrite replaces the block once.
    worktree_bootstrap.write_claude_md(tmp_path, "updated")
    assert (tmp_path / "CLAUDE.md").read_text().count("jalebi:start") == 1


def test_remove_guard_removes_codex_and_claude_guards(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="codex")
    worktree_bootstrap.remove_guard(tmp_path)
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / "opencode.json").exists()
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")
    worktree_bootstrap.remove_guard(tmp_path)
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_remove_guard_preserves_repo_codex_content(tmp_path) -> None:
    _init_repo(tmp_path)
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("model = 'x'\n")
    _git(["add", ".codex/config.toml"], tmp_path)
    _git(["commit", "-qm", "add codex config"], tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="codex")
    worktree_bootstrap.remove_guard(tmp_path)
    assert not (tmp_path / ".codex" / "rules" / "default.rules").exists()
    assert (codex_dir / "config.toml").read_text() == "model = 'x'\n"


def test_remove_guard_restores_repo_claude_md(tmp_path) -> None:
    _init_repo(tmp_path)
    original = "# Repo claude\n\nInstructions.\n"
    (tmp_path / "CLAUDE.md").write_text(original)
    _git(["add", "CLAUDE.md"], tmp_path)
    _git(["commit", "-qm", "add claude.md"], tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")
    worktree_bootstrap.remove_guard(tmp_path)
    assert (tmp_path / "CLAUDE.md").read_text() == original


def test_bootstrap_claude_skips_repo_settings_but_writes_claude_md(tmp_path) -> None:
    _init_repo(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {"deny": ["Bash(rm *)"]}}\n')
    worktree_bootstrap.bootstrap_worktree(tmp_path, "jalebi instructions", cli="claude")
    # Repo-owned settings preserved (guard skipped), but CLAUDE.md still written
    # and no opencode.json appears.
    assert settings.read_text() == '{"permissions": {"deny": ["Bash(rm *)"]}}\n'
    assert (tmp_path / "CLAUDE.md").is_file()
    assert not (tmp_path / "opencode.json").exists()


def test_precommit_hook_allows_tracked_codex_config(tmp_path) -> None:
    """A repo that tracks its own .codex/config.toml can still commit it (only
    the Jalebi-written default.rules is rejected)."""
    _init_repo(tmp_path)
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("model = 'x'\n")
    _git(["add", ".codex/config.toml"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-qm", "track codex config"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_remove_guard_keeps_repo_owned_claude_settings(tmp_path) -> None:
    _init_repo(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"permissions": {"deny": ["Bash(rm *)"]}}\n')
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")
    worktree_bootstrap.remove_guard(tmp_path)
    assert settings.read_text() == '{"permissions": {"deny": ["Bash(rm *)"]}}\n'


def test_info_exclude_covers_codex_and_claude(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")
    common = worktree_bootstrap._git_common_dir(tmp_path)
    exclude = (common / "info" / "exclude").read_text()
    for line in worktree_bootstrap.INFO_EXCLUDE_LINES:
        assert line in exclude, f"{line} missing from info/exclude"
    # A plain `git add .` must not stage any guard/bootstrap file.
    _git(["add", "-A"], tmp_path)
    staged = _git(["diff", "--cached", "--name-only"], tmp_path)
    assert staged == ""
    status = _git(["status", "--porcelain"], tmp_path)
    for marker in (".codex", "CLAUDE.md", ".claude", "opencode.json"):
        assert marker not in status, f"{marker} leaked into git status"


def test_precommit_hook_rejects_staged_codex_guard(tmp_path) -> None:
    _init_repo(tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="codex")
    _git(["add", "-f", ".codex/rules/default.rules"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-qm", "bad"], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert ".codex" in proc.stderr


def test_precommit_hook_rejects_marked_claude_md(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Repo\n")
    _git(["add", "CLAUDE.md"], tmp_path)
    _git(["commit", "-qm", "track claude.md"], tmp_path)
    worktree_bootstrap.bootstrap_worktree(tmp_path, cli="claude")
    _git(["add", "CLAUDE.md"], tmp_path)
    proc = subprocess.run(
        ["git", "commit", "-qm", "bad"], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "CLAUDE.md" in proc.stderr
    # After remove_guard strips the marker, the staged file has no marker block.
    worktree_bootstrap.remove_guard(tmp_path)
    _git(["add", "CLAUDE.md"], tmp_path)
    staged = _git(["show", ":CLAUDE.md"], tmp_path)
    assert "jalebi:start" not in staged
