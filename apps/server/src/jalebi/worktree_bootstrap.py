"""Per-task worktree bootstrap: gh guard + git identity + AGENTS.md context.

Jalebi never relies on the `gh` CLI — the agent must use the owner PAT via
git/curl only. This module hardens that contract at the worktree level with a
**per-CLI guard** (selected by ``write_guard(worktree, cli)``):

- ``opencode`` → ``opencode.json`` whose ``permission.bash`` rules DENY ``gh``
  and whose ``permission.external_directory`` is ``deny`` (project config
  overrides the user's global config; ``gh`` is also blocked by path variants).
- ``codex`` → ``.codex/rules/default.rules`` (Starlark execpolicy) whose
  ``prefix_rule`` forbids ``gh`` (loaded for trusted projects; the codex
  sandbox from the adapter is the confinement, this is the gh-specific deny).
- ``claude`` → ``.claude/settings.json`` with ``permissions.deny`` for ``gh``
  variants (deny rules apply in EVERY permission mode, incl. the adapter's
  ``--permission-mode bypassPermissions``). Skipped when the repo ships its own
  ``.claude/settings.json`` (never clobber repo config).

``set_git_identity`` pins the worktree's commit author to Jalebi, so pushes
are never authored by a stray local account. ``write_agent_md`` writes the
task's ``AGENTS.md`` (built by ``prompts``) that carries the task context and
hard constraints; claude runs ALSO get the same marked block in ``CLAUDE.md``
(claude reads ``CLAUDE.md``, not ``AGENTS.md``). A repo that tracks its own
AGENTS.md/CLAUDE.md keeps that content; Jalebi's section is appended inside
markers. ``write_info_exclude`` keeps the bootstrap files out of ``git add .``
and artifact capture via the repo's shared ``info/exclude`` (never touching a
tracked ``.gitignore``), and the pre-commit hook backstops the repo-tracked
cases.

These are layered with environment hygiene in the queue (git credential env,
no ``GH_TOKEN``/``GH_CONFIG_DIR``) — even a bypassed deny has no gh credentials.
"""

import json
import shutil
import subprocess
from pathlib import Path

from jalebi import messaging

GIT_TIMEOUT_SECONDS = 60

# Permission rules are order-sensitive: opencode applies the LAST matching rule,
# so the broad allow goes first and every gh variant is denied afterwards.
# ``external_directory: "deny"`` blocks built-in read/edit/write/glob/grep and
# bash commands that reference paths outside the worktree (opencode merges this
# project config over the user's global ``external_directory: "allow"``). URL-
# based tools (webfetch/websearch/MCP URL tools) are unaffected, so the user's
# MCP servers keep working.
OPENCODE_GUARD = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {
        "external_directory": "deny",
        "bash": {
            "*": "allow",
            "gh": "deny",
            "gh *": "deny",
            "gh**": "deny",
            "gh **": "deny",
            "/usr/bin/gh": "deny",
            "/usr/bin/gh*": "deny",
            "/usr/local/bin/gh": "deny",
            "/usr/local/bin/gh*": "deny",
            "command gh*": "deny",
            "which gh*": "deny",
            "type gh*": "deny",
            "hash gh*": "deny",
        }
    },
}

# codex execpolicy project rules (loaded for trusted projects; the codex
# sandbox from the adapter is the confinement — this is the gh-specific deny).
# Prefix match on the command token: the single rule covers `gh`, `gh pr view
# 1`, etc. (verified via `codex execpolicy check`).
CODEX_RULES_GUARD = (
    'prefix_rule(pattern=["gh"], decision="forbidden", '
    'justification="gh is not permitted in this environment")\n'
)

# claude settings deny rules apply in EVERY permission mode (incl. the
# adapter's `--permission-mode bypassPermissions`). Deny-only file is valid.
CLAUDE_GUARD = {
    "permissions": {
        "deny": [
            "Bash(gh *)",
            "Bash(gh)",
            "Bash(gh**)",
            "Bash(gh **)",
        ]
    }
}

GIT_USER_NAME = messaging.CO_AUTHOR_NAME
GIT_USER_EMAIL = messaging.CO_AUTHOR_EMAIL

JALEBI_MD_START = "<!-- jalebi:start -->"
JALEBI_MD_END = "<!-- jalebi:end -->"

DEFAULT_AGENT_MD = """\
# Jalebi task environment

You are working inside a git worktree prepared by Jalebi.

## Hard rules

1. **Never use the `gh` CLI.** It is blocked. Use `git` (credentials are
   provided via the environment) or the GitHub REST API via `curl -H
   "Authorization: Bearer $JALEBI_GITHUB_TOKEN"` if that variable is set.
2. **Never create forks.** Do not add git remotes pointing at another account.
3. **Do not push.** Jalebi pushes your branch and handles PRs. Commit locally on
   the task branch; for review tasks never modify files or push at all.
4. **Work only inside this worktree.** Do not read, write, or run anything
   outside the current directory (`~`, `/etc`, `/tmp`, other projects, Jalebi's
   data dir) — it is blocked, and this is the rule that matters.
5. Commit messages: short imperative summary; reference the task where useful.
6. When asked, write your PR title + description to `.jalebi/pr.md` as:
   `# <title>` on the first line, then the description body. Jalebi uses this
   file for the PR it opens.
7. For review tasks, write your review to `.jalebi/review.md` — Jalebi posts it.
8. Use only the GitHub token provided in `JALEBI_GITHUB_TOKEN` (if set) for
   anything GitHub-related. Do not use gh at all.
9. **Never commit anything under `.jalebi/`** — it is Jalebi-internal (your PR
   description/review live there). If you staged `.jalebi/` files, unstage with
   `git reset HEAD .jalebi/`. A pre-commit hook rejects them otherwise.
10. **Never commit this Jalebi AGENTS.md section** — the block delimited by the
    two HTML-comment markers at the end of ``AGENTS.md``. It is Jalebi
    infrastructure, not repository content. If you staged it, recover with:
    `git restore --staged AGENTS.md && git restore AGENTS.md`. The pre-commit
    hook rejects it otherwise.
11. **Docs:** only update documentation that already exists and is kept in sync
    (e.g. `CHANGELOG.md`, relevant `README.md` sections). Do NOT create new
    documentation/changelog files unless the task explicitly asks for them.
"""

# Patterns added to the repo's shared info/exclude so bootstrap files stay out of
# `git add .`, `git status`, and artifact capture. Root-anchored: they only hide
# the worktree-root files Jalebi creates (a repo that *tracks* AGENTS.md is
# unaffected — excludes never apply to tracked files; the hook covers that case).
# `.claude/skills/` holds catalog-agent skills materialized into the worktree
# (opencode's native skills loading) — Jalebi-internal, never committed.
INFO_EXCLUDE_LINES = (
    ".jalebi/",
    "/opencode.json",
    "/AGENTS.md",
    "/.claude/skills/",
    "/.codex/",
    "/CLAUDE.md",
    "/.claude/settings.json",
)

PRECOMMIT_HOOK = """#!/bin/sh
# Jalebi: never allow committing Jalebi-internal files.
if git diff --cached --name-only -z | tr '\\0' '\\n' | grep -q '^\\.jalebi/'; then
  echo "Jalebi: refusing to commit .jalebi/ (internal files)." >&2
  echo "Unstage them with: git reset HEAD .jalebi/" >&2
  exit 1
fi
if git diff --cached --name-only | grep -qx 'opencode.json'; then
  echo "Jalebi: refusing to commit opencode.json (Jalebi gh-guard)." >&2
  echo "Unstage it with: git reset HEAD opencode.json" >&2
  exit 1
fi
if git diff --cached --name-only | grep -qx '.codex/rules/default.rules'; then
  echo "Jalebi: refusing to commit .codex/rules/default.rules (Jalebi codex gh-guard)." >&2
  echo "Unstage it with: git reset HEAD .codex/rules/default.rules" >&2
  exit 1
fi
# A repo-tracked AGENTS.md that still carries the Jalebi bootstrap section must
# not be committed (it is Jalebi infrastructure, not repository content).
if git diff --cached --name-only | grep -qx 'AGENTS.md'; then
  if git show :AGENTS.md 2>/dev/null | grep -q 'jalebi:start'; then
    echo "Jalebi: refusing to commit AGENTS.md (contains the Jalebi bootstrap section)." >&2
    echo "Remove it from the commit with:" >&2
    echo "  git restore --staged AGENTS.md && git restore AGENTS.md" >&2
    exit 1
  fi
fi
# Same for CLAUDE.md (claude reads CLAUDE.md, not AGENTS.md).
if git diff --cached --name-only | grep -qx 'CLAUDE.md'; then
  if git show :CLAUDE.md 2>/dev/null | grep -q 'jalebi:start'; then
    echo "Jalebi: refusing to commit CLAUDE.md (contains the Jalebi bootstrap section)." >&2
    echo "Remove it from the commit with:" >&2
    echo "  git restore --staged CLAUDE.md && git restore CLAUDE.md" >&2
    exit 1
  fi
fi
exit 0
"""


def write_opencode_guard(worktree: Path) -> Path:
    """Write the ``opencode.json`` that denies ``gh`` into ``worktree``."""
    path = worktree / "opencode.json"
    path.write_text(json.dumps(OPENCODE_GUARD, indent=2) + "\n")
    return path


def write_codex_guard(worktree: Path) -> Path:
    """Write ``.codex/rules/default.rules`` denying ``gh`` (codex project rules)."""
    path = worktree / ".codex" / "rules" / "default.rules"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CODEX_RULES_GUARD)
    return path


def write_claude_guard(worktree: Path) -> Path | None:
    """Write ``.claude/settings.json`` denying ``gh``; never clobber a repo-owned file.

    Returns the settings path, or ``None`` when the repo already ships its own
    ``.claude/settings.json`` (deny skipped there; CLAUDE.md + env hygiene still
    apply). ``remove_guard`` deletes the file only while it still matches exactly
    what Jalebi wrote, so repo-owned or repo-edited settings survive.
    """
    path = worktree / ".claude" / "settings.json"
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CLAUDE_GUARD, indent=2) + "\n")
    return path


def write_guard(worktree: Path, cli: str) -> None:
    """Write the per-CLI guard file (opencode/codex/claude); unknown cli → none.

    The guard is the worktree-level confinement: no ``gh``, and (for opencode)
    no access outside the worktree. Codex's sandbox and claude's (absent) disk
    confinement live at the adapter layer — this file is the gh-specific deny.
    """
    if cli == "opencode":
        write_opencode_guard(worktree)
        return
    if cli == "codex":
        write_codex_guard(worktree)
        return
    if cli == "claude":
        write_claude_guard(worktree)
        return


def _run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return (proc.stdout or "").strip()


def set_git_identity(worktree: Path) -> None:
    """Pin the worktree's commit author to the Jalebi identity."""
    _run_git(["config", "user.name", GIT_USER_NAME], worktree)
    _run_git(["config", "user.email", GIT_USER_EMAIL], worktree)


def _jalebi_block(content: str) -> str:
    return f"{JALEBI_MD_START}\n{content}\n{JALEBI_MD_END}"


def _jalebi_block_span(raw: str) -> tuple[int, int] | None:
    """Locate the Jalebi marker block ``(start, end)``, or ``None``.

    The block Jalebi writes is appended at the very END of the file, so its
    closing marker must be the last non-whitespace content. ``rfind`` the closing
    marker, verify nothing but whitespace follows it, then ``rfind`` the opening
    marker before it. This stays correct even when the repo's own AGENTS.md
    content happens to quote the marker strings mid-file, and when the Jalebi
    rules reference them.
    """
    end = raw.rfind(JALEBI_MD_END)
    if end == -1:
        return None
    if raw[end + len(JALEBI_MD_END):].strip():
        return None  # closing marker is not at the end of the file
    start = raw.rfind(JALEBI_MD_START, 0, end)
    if start == -1:
        return None
    return start, end + len(JALEBI_MD_END)


def _agent_md_body(raw: str, content: str) -> str:
    """Merge ``content`` into existing ``raw`` text as the marked Jalebi block
    (replace an existing block, or append one at the end)."""
    span = _jalebi_block_span(raw)
    if span is not None:
        start, end = span
        pre = raw[:start]
        post = raw[end:]
        body = (
            (pre.rstrip() + "\n\n" if pre.strip() else "")
            + _jalebi_block(content)
            + ("\n\n" + post.lstrip() if post.strip() else "\n")
        )
    else:
        base = raw.rstrip()
        body = (base + "\n\n" if base else "") + _jalebi_block(content) + "\n"
    return body


def write_agent_md(worktree: Path, content: str = DEFAULT_AGENT_MD) -> Path:
    """Write (or update) the Jalebi ``AGENTS.md`` section, preserving any existing file.

    A repo that already tracks ``AGENTS.md`` keeps its own content; Jalebi's
    section is appended inside markers so re-bootstrapping is idempotent and the
    pre-commit hook can detect it. When no file exists, the whole file is the
    marked block.
    """
    path = worktree / "AGENTS.md"
    raw = path.read_text() if path.exists() else ""
    path.write_text(_agent_md_body(raw, content))
    return path


def write_claude_md(worktree: Path, content: str = DEFAULT_AGENT_MD) -> Path:
    """Write the SAME marked Jalebi block to ``CLAUDE.md`` (claude reads it, not AGENTS.md).

    Mirrors ``write_agent_md``: repo-tracked ``CLAUDE.md`` content is preserved
    and the Jalebi section is appended inside markers.
    """
    path = worktree / "CLAUDE.md"
    raw = path.read_text() if path.exists() else ""
    path.write_text(_agent_md_body(raw, content))
    return path


def _git_common_dir(worktree: Path) -> Path:
    out = _run_git(["rev-parse", "--git-common-dir"], worktree)
    common = Path(out).expanduser()
    if not common.is_absolute():
        common = worktree / common
    return common.resolve()


def write_info_exclude(worktree: Path) -> Path | None:
    """Add the bootstrap-file patterns to the repo's shared ``info/exclude``.

    ``info/exclude`` lives in the common gitdir, so one write covers every
    worktree of the mirror and never mutates a tracked ``.gitignore``. Returns
    the exclude path, or ``None`` if the worktree has no git dir yet.
    """
    try:
        common = _git_common_dir(worktree)
    except RuntimeError:
        return None
    info = common / "info"
    info.mkdir(parents=True, exist_ok=True)
    path = info / "exclude"
    lines = path.read_text().splitlines() if path.exists() else []
    for line in INFO_EXCLUDE_LINES:
        if line not in lines:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n")
    return path


def write_precommit_hook(worktree: Path) -> Path | None:
    """Install a pre-commit hook that rejects staged Jalebi-internal files.

    Worktrees share the common gitdir's hooks, so one hook covers every worktree.
    Returns the hook path, or ``None`` if the worktree has no git dir yet.
    """
    try:
        hooks = _git_common_dir(worktree) / "hooks"
    except RuntimeError:
        return None
    hook = hooks / "pre-commit"
    hooks.mkdir(parents=True, exist_ok=True)
    hook.write_text(PRECOMMIT_HOOK)
    hook.chmod(0o755)
    return hook


def _write_guard(worktree: Path, cli: str) -> None:
    """Write the per-CLI guard file that confines the agent to the worktree.

    Kept as a thin alias so existing callers (queue, tests) are unchanged; the
    public entry point is ``write_guard``.
    """
    write_guard(worktree, cli)


def write_agent_skills(worktree: Path, skills: list[dict[str, str]]) -> list[Path]:
    """Materialize catalog-agent skills into ``.claude/skills/<name>/SKILL.md``.

    opencode loads skills from ``.claude/skills`` (PRD F6.4 — the
    ``OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`` env must stay unset, which the queue
    never sets). Each skill is a SKILL.md so the CLI discovers them like its own.
    """
    written: list[Path] = []
    base = worktree / ".claude" / "skills"
    for skill in skills:
        name = str(skill.get("name", "")).strip()
        content = str(skill.get("content", ""))
        if not name:
            continue
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "SKILL.md"
        path.write_text(content)
        written.append(path)
    return written


def _write_skills(worktree: Path, skills: list[dict[str, str]] | None) -> None:
    """Write catalog-agent skills into the worktree (dropping stale ones).

    When an agent's skill list changes, a re-bootstrap of the same worktree must
    not leave orphaned ``SKILL.md`` files behind — opencode would keep
    auto-discovering them, so the run wouldn't match the catalog. Any existing
    skill subdir not in the new name set is removed.
    """
    base = worktree / ".claude" / "skills"
    if skills:
        names = {str(skill.get("name", "")).strip() for skill in skills}
        if base.is_dir():
            for child in base.iterdir():
                if child.is_dir() and child.name not in names:
                    shutil.rmtree(child, ignore_errors=True)
        write_agent_skills(worktree, skills)
    else:
        # No agent selected: ensure no stale skills linger from a previous run
        # that used a catalog agent in this worktree.
        if base.is_dir():
            shutil.rmtree(base, ignore_errors=True)


def bootstrap_worktree(
    worktree: Path,
    agent_md: str = DEFAULT_AGENT_MD,
    cli: str = "opencode",
    skills: list[dict[str, str]] | None = None,
) -> None:
    """Apply the full bootstrap: guard + identity + AGENTS.md + excludes + skills.

    Idempotent.
    """
    worktree.mkdir(parents=True, exist_ok=True)
    write_guard(worktree, cli)
    set_git_identity(worktree)
    write_agent_md(worktree, agent_md)
    if cli == "claude":
        # Claude reads CLAUDE.md, not AGENTS.md — carry the same rules there.
        write_claude_md(worktree, agent_md)
    write_info_exclude(worktree)
    write_precommit_hook(worktree)
    _write_skills(worktree, skills)


def _strip_agent_md(raw: str) -> str:
    """Return ``raw`` with the Jalebi marker block removed (used by remove_guard)."""
    span = _jalebi_block_span(raw)
    if span is None:
        return raw.strip()
    start, end = span
    pre = raw[:start]
    post = raw[end:]
    restored = pre.rstrip() + ("\n\n" + post.lstrip() if post.strip() else "")
    return restored.strip()


def _has_other_worktrees(worktree: Path) -> bool:
    """True if the repo's common gitdir hosts other live worktrees.

    The info/exclude patterns and pre-commit hook live in the SHARED common
    gitdir. With concurrent tasks on one repo, removing them when another
    worktree still relies on them would silently re-expose bootstrap files.
    Being conservative (fail toward keeping the guards) is safe — they are
    idempotent to write.
    """
    try:
        out = _run_git(["worktree", "list", "--porcelain"], worktree)
    except RuntimeError:
        return True
    blocks = [b for b in out.split("\n\n") if b.strip()]
    # A bare mirror reports itself as a worktree entry with a "bare" line; it
    # has no files to guard, so it doesn't count as a live worktree.
    real = [b for b in blocks if "bare" not in b.splitlines()]
    return len(real) > 1


def _strip_agent_md_file(worktree: Path, filename: str) -> None:
    """Strip the Jalebi marker block from ``filename`` (restore or delete)."""
    md = worktree / filename
    if not md.is_file():
        return
    restored = _strip_agent_md(md.read_text())
    if restored:
        md.write_text(restored + "\n")
    else:
        try:
            md.unlink()
        except FileNotFoundError:
            pass


def remove_guard(worktree: Path) -> None:
    """Remove the Jalebi guard files from a worktree (cleanup).

    ``AGENTS.md`` (and ``CLAUDE.md``) are restored to their pre-bootstrap content
    (the marked section is stripped); ``opencode.json``, the codex
    ``.codex/rules/default.rules``, and the Jalebi-written
    ``.claude/settings.json`` are removed. Only Jalebi's own files are touched —
    repo-owned content (.codex config/rules, .claude/settings.json, tracked
    CLAUDE.md) is preserved. The shared ``info/exclude`` lines and the
    pre-commit hook are only removed when this is the last live worktree of the
    repo.
    """
    for name in ("opencode.json",):
        try:
            (worktree / name).unlink()
        except FileNotFoundError:
            pass
    # Codex guard: remove only the file Jalebi writes (a repo may own other
    # `.codex/` content); prune the dirs only when they become empty.
    codex_rules = worktree / ".codex" / "rules" / "default.rules"
    if codex_rules.is_file():
        try:
            codex_rules.unlink()
        except FileNotFoundError:
            pass
        for directory in (codex_rules.parent, worktree / ".codex"):
            try:
                directory.rmdir()  # OSError when non-empty → leave it (repo-owned)
            except OSError:
                pass
    # Claude guard: remove only the exact file Jalebi wrote — never a repo-owned
    # or repo-edited `.claude/settings.json`.
    claude_settings = worktree / ".claude" / "settings.json"
    if claude_settings.is_file():
        try:
            if claude_settings.read_text() == json.dumps(CLAUDE_GUARD, indent=2) + "\n":
                claude_settings.unlink()
        except FileNotFoundError:
            pass
    # Drop only Jalebi's materialized catalog-agent skills — never a repo's own
    # `.claude` content (e.g. settings.json / commands).
    shutil.rmtree(worktree / ".claude" / "skills", ignore_errors=True)
    _strip_agent_md_file(worktree, "AGENTS.md")
    _strip_agent_md_file(worktree, "CLAUDE.md")
    try:
        common = _git_common_dir(worktree)
    except RuntimeError:
        return
    if _has_other_worktrees(worktree):
        return  # another live worktree still needs the shared excludes + hook
    exclude = common / "info" / "exclude"
    if exclude.is_file():
        remaining = [
            ln for ln in exclude.read_text().splitlines() if ln not in INFO_EXCLUDE_LINES
        ]
        if remaining:
            exclude.write_text("\n".join(remaining) + "\n")
        else:
            try:
                exclude.unlink()
            except FileNotFoundError:
                pass
    hook = common / "hooks" / "pre-commit"
    if hook.is_file() and PRECOMMIT_HOOK.strip() in (hook.read_text() or ""):
        try:
            hook.unlink()
        except FileNotFoundError:
            pass
