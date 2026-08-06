"""Git workspace manager: bare mirrors + per-task worktrees + token-authenticated push.

Uses the `git` CLI (not libgit2) via subprocess. The PAT is passed through the
GIT_CONFIG_* environment variables (``http.extraHeader``) so it never appears in
argv, URLs, or logs.
"""

import base64
import os
import subprocess
import threading
from pathlib import Path

from jalebi.config import Config

BRANCH_PREFIX = "jalebi/"
GIT_TIMEOUT_SECONDS = 120


class GitWorkspaceError(Exception):
    """Raised when a git command fails or a precondition is unmet."""


def _run_git(
    args: list[str],
    cwd: Path | str | None = None,
    auth_env: dict[str, str] | None = None,
) -> str:
    """Run a git command; raise ``GitWorkspaceError`` on non-zero exit."""
    env = os.environ.copy()
    if auth_env:
        env.update(auth_env)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        raise GitWorkspaceError(f"git {' '.join(args)} failed: {stderr}")
    return proc.stdout.strip()


def _auth_env(token: str | None) -> dict[str, str]:
    """Build the env vars that authenticate git over HTTPS with a PAT.

    GitHub requires Basic auth for git-over-HTTPS; the token is used as the
    ``x-access-token`` username. Set via GIT_CONFIG_* env so it never appears
    in argv, URLs, or logs.
    """
    if not token:
        return {}
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: basic {encoded}",
    }


class GitWorkspace:
    def __init__(self, config: Config):
        self.config = config
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def mirror_path(data_dir: Path, full_name: str) -> Path:
        safe = full_name.replace("/", "__")
        return data_dir / "repos" / f"{safe}.git"

    @staticmethod
    def worktree_path(data_dir: Path, task_id: int) -> Path:
        return data_dir / "ws" / f"task-{task_id}"

    @staticmethod
    def task_branch(task_id: int) -> str:
        return f"{BRANCH_PREFIX}{task_id}"

    def _lock_for(self, full_name: str) -> threading.Lock:
        with self._locks_guard:
            if full_name not in self._locks:
                self._locks[full_name] = threading.Lock()
            return self._locks[full_name]

    def ensure_mirror(self, full_name: str, clone_url: str, token: str | None = None) -> Path:
        """Clone (or fetch) the bare mirror for ``full_name``. Returns the mirror path."""
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        with self._lock_for(full_name):
            if not mirror.exists():
                mirror.parent.mkdir(parents=True, exist_ok=True)
                _run_git(["clone", "--mirror", clone_url, str(mirror)], auth_env=auth)
            else:
                _run_git(["-C", str(mirror), "fetch", "--prune"], auth_env=auth)
        return mirror

    def create_worktree(
        self, task_id: int, full_name: str, base_branch: str = "main", token: str | None = None
    ) -> Path:
        """Create a worktree on ``jalebi/<taskId>`` based on ``base_branch``.

        Reuses an existing worktree for the task (resume path).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        auth = _auth_env(token)

        if (ws / ".git").is_file():
            return ws

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(f"mirror missing for {full_name}; call ensure_mirror first")
            _run_git(["-C", str(mirror), "fetch", "--prune"], auth_env=auth)
            branches = _run_git(
                ["-C", str(mirror), "branch", "--format=%(refname:short)"]
            ).splitlines()
            if branch in branches:
                _run_git(["-C", str(mirror), "worktree", "add", str(ws), branch])
            else:
                _run_git(["-C", str(mirror), "worktree", "add", "-b", branch, str(ws), base_branch])
        return ws

    def remove_worktree(self, task_id: int, full_name: str) -> None:
        """Remove the task's worktree and its local branch (no-op if absent)."""
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        with self._lock_for(full_name):
            if not mirror.exists():
                return
            if (ws / ".git").is_file():
                _run_git(["-C", str(mirror), "worktree", "remove", "--force", str(ws)])
            branches = _run_git(
                ["-C", str(mirror), "branch", "--format=%(refname:short)"]
            ).splitlines()
            if branch in branches:
                _run_git(["-C", str(mirror), "branch", "-D", branch])

    def push_branch(self, task_id: int, full_name: str, token: str | None = None) -> None:
        """Push ``jalebi/<taskId>`` to the mirror's origin with token auth.

        The mirror uses ``--mirror``, so the per-invocation ``mirror=false``
        override lets us push an explicit refspec.
        """
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        _run_git(
            ["-C", str(ws), "-c", "remote.origin.mirror=false", "push", "origin", branch],
            auth_env=_auth_env(token),
        )
