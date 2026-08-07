"""Per-task worktree bootstrap: gh guard + git identity + AGENTS.md context.

Jalebi never relies on the `gh` CLI — the agent must use the owner PAT via
git/curl only. This module hardens that contract at the worktree level:

- ``write_opencode_guard`` places an ``opencode.json`` in the worktree root whose
  ``permission.bash`` rules DENY ``gh`` invocations. Project config overrides the
  user's global opencode config, so the guard applies to any agent run in the
  worktree.
- ``set_git_identity`` pins the worktree's commit author to Jalebi, so pushes
  are never authored by a stray local account.
- ``write_agent_md`` writes the task's ``AGENTS.md`` (built by ``prompts``) that
  carries the task context and hard constraints.

These are layered with environment hygiene in the queue (git credential env,
no ``GH_TOKEN``/``GH_CONFIG_DIR``) — even a bypassed deny has no gh credentials.
"""

import json
import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 60

# Permission rules are order-sensitive: opencode applies the LAST matching rule,
# so the broad allow goes first and every gh variant is denied afterwards.
OPENCODE_GUARD = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {
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

GIT_USER_NAME = "Jalebi"
GIT_USER_EMAIL = "jalebi@localhost"

DEFAULT_AGENT_MD = """\
# Jalebi task environment

You are working inside a git worktree prepared by Jalebi.

## Hard rules

1. **Never use the `gh` CLI.** It is blocked. Use `git` (credentials are
   provided via the environment) or the GitHub REST API via `curl -H
   "Authorization: Bearer $JALEBI_GITHUB_TOKEN"`.
2. **Never create forks.** Push only to the `origin` remote, on the task branch.
3. **Never open or edit pull requests through `gh` or the API** — Jalebi
   handles publishing. Just commit to the branch.
4. Commit messages: short imperative summary; reference the task where useful.
5. When asked, write your PR title + description to `.jalebi/pr.md` as:
   `# <title>` on the first line, then the description body. Jalebi uses this
   file for the PR it opens.
6. For review tasks, write your review to `.jalebi/review.md` — Jalebi posts it.
7. Use only the GitHub token provided in `JALEBI_GITHUB_TOKEN` for anything
   GitHub-related. Do not use gh at all.
8. **Never commit anything under `.jalebi/`** — it is Jalebi-internal (your PR
   description/review live there). If you staged `.jalebi/` files, unstage with
   `git reset HEAD .jalebi/`. A pre-commit hook rejects them otherwise.
9. **Docs:** only update documentation that already exists and is kept in sync
   (e.g. `CHANGELOG.md`, relevant `README.md` sections). Do NOT create new
   documentation/changelog files unless the task explicitly asks for them.
"""

GITIGNORE_LINE = ".jalebi/"

PRECOMMIT_HOOK = """#!/bin/sh
# Jalebi: never allow committing Jalebi-internal files under .jalebi/.
if git diff --cached --name-only -z | tr '\\0' '\\n' | grep -q '^\\.jalebi/'; then
  echo "Jalebi: refusing to commit .jalebi/ (internal files)." >&2
  echo "Unstage them with: git reset HEAD .jalebi/" >&2
  exit 1
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


def write_agent_md(worktree: Path, content: str = DEFAULT_AGENT_MD) -> Path:
    """Write the task's ``AGENTS.md`` (task context + constraints)."""
    path = worktree / "AGENTS.md"
    path.write_text(content)
    return path


def write_gitignore(worktree: Path) -> Path:
    """Ensure the worktree ignores ``.jalebi/`` (keeps it out of git and artifacts).

    Appends the line to an existing ``.gitignore`` rather than clobbering it.
    """
    path = worktree / ".gitignore"
    lines = path.read_text().splitlines() if path.exists() else []
    if GITIGNORE_LINE not in lines:
        lines.append(GITIGNORE_LINE)
        path.write_text("\n".join(lines) + "\n")
    return path


def _git_common_dir(worktree: Path) -> Path:
    out = _run_git(["rev-parse", "--git-common-dir"], worktree)
    common = Path(out).expanduser()
    if not common.is_absolute():
        common = worktree / common
    return common.resolve()


def write_precommit_hook(worktree: Path) -> Path | None:
    """Install a pre-commit hook that rejects staged ``.jalebi/`` files.

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


def bootstrap_worktree(worktree: Path, agent_md: str = DEFAULT_AGENT_MD) -> None:
    """Apply the full bootstrap: guard + identity + AGENTS.md + .jalebi guards.

    Idempotent.
    """
    worktree.mkdir(parents=True, exist_ok=True)
    write_opencode_guard(worktree)
    set_git_identity(worktree)
    write_agent_md(worktree, agent_md)
    write_gitignore(worktree)
    write_precommit_hook(worktree)


def remove_guard(worktree: Path) -> None:
    """Remove the Jalebi guard files from a worktree (cleanup)."""
    for name in ("opencode.json", "AGENTS.md"):
        try:
            (worktree / name).unlink()
        except FileNotFoundError:
            pass
    # Drop the .jalebi/ ignore line we added (keep any pre-existing lines).
    gitignore = worktree / ".gitignore"
    if gitignore.is_file():
        remaining = [ln for ln in gitignore.read_text().splitlines() if ln != GITIGNORE_LINE]
        if remaining:
            gitignore.write_text("\n".join(remaining) + "\n")
        else:
            try:
                gitignore.unlink()
            except FileNotFoundError:
                pass
    try:
        hook = _git_common_dir(worktree) / "hooks" / "pre-commit"
    except RuntimeError:
        return
    if hook.is_file() and PRECOMMIT_HOOK.strip() in (hook.read_text() or ""):
        try:
            hook.unlink()
        except FileNotFoundError:
            pass
