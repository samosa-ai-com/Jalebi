"""Per-task worktree bootstrap: gh guard + git identity + AGENTS.md context.

Jalebi never relies on the `gh` CLI — the agent must use the owner PAT via
git/curl only. This module hardens that contract at the worktree level:

- ``write_opencode_guard`` places an ``opencode.json`` in the worktree root whose
  ``permission.bash`` rules DENY ``gh`` invocations and whose
  ``permission.external_directory`` is ``deny`` (blocks file-tool and bash-path
  access outside the worktree; overrides the user's global ``"allow"``). Project
  config overrides the user's global opencode config, so the guard applies to any
  agent run in the worktree. The guard is adapter-specific: Codex/Claude get
  their own guard file through the same ``bootstrap_worktree`` hook.
- ``set_git_identity`` pins the worktree's commit author to Jalebi, so pushes
  are never authored by a stray local account.
- ``write_agent_md`` writes the task's ``AGENTS.md`` (built by ``prompts``) that
  carries the task context and hard constraints. A repo that tracks its own
  ``AGENTS.md`` keeps that content; Jalebi's section is appended inside markers.
- ``write_info_exclude`` keeps the bootstrap files out of ``git add .`` and
  artifact capture via the repo's shared ``info/exclude`` (never touching a
  tracked ``.gitignore``), and the pre-commit hook backstops the repo-tracked
  ``AGENTS.md`` case.

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
exit 0
"""


def write_opencode_guard(worktree: Path) -> Path:
    """Write the ``opencode.json`` that denies ``gh`` into ``worktree``."""
    path = worktree / "opencode.json"
    path.write_text(json.dumps(OPENCODE_GUARD, indent=2) + "\n")
    return path


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


def write_agent_md(worktree: Path, content: str = DEFAULT_AGENT_MD) -> Path:
    """Write (or update) the Jalebi ``AGENTS.md`` section, preserving any existing file.

    A repo that already tracks ``AGENTS.md`` keeps its own content; Jalebi's
    section is appended inside markers so re-bootstrapping is idempotent and the
    pre-commit hook can detect it. When no file exists, the whole file is the
    marked block.
    """
    path = worktree / "AGENTS.md"
    raw = path.read_text() if path.exists() else ""
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
    path.write_text(body)
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

    Each adapter gets its own guard file through this hook so the isolation
    contract (no gh, no access outside the worktree) survives a backend switch:
    opencode → ``opencode.json`` today; codex/claude → their config files later.
    """
    if cli == "opencode":
        write_opencode_guard(worktree)
        return
    # Future adapters write their own guard here; until then no guard is written
    # (the env/prompt layers still apply).


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
    _write_guard(worktree, cli)
    set_git_identity(worktree)
    write_agent_md(worktree, agent_md)
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


def remove_guard(worktree: Path) -> None:
    """Remove the Jalebi guard files from a worktree (cleanup).

    ``AGENTS.md`` is restored to its pre-bootstrap content (the marked section is
    stripped) and ``opencode.json`` is removed. The shared ``info/exclude`` lines
    and the pre-commit hook are only removed when this is the last live worktree
    of the repo.
    """
    for name in ("opencode.json",):
        try:
            (worktree / name).unlink()
        except FileNotFoundError:
            pass
    # Drop only Jalebi's materialized catalog-agent skills — never a repo's own
    # `.claude` content (e.g. settings.json / commands).
    shutil.rmtree(worktree / ".claude" / "skills", ignore_errors=True)
    md = worktree / "AGENTS.md"
    if md.is_file():
        restored = _strip_agent_md(md.read_text())
        if restored:
            md.write_text(restored + "\n")
        else:
            try:
                md.unlink()
            except FileNotFoundError:
                pass
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
