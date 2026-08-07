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


def _clean_git_env(env: dict[str, str]) -> dict[str, str]:
    """Make a git subprocess environment hermetic.

    Inherited ``GIT_CONFIG_*`` vars could inject a credential helper, a
    ``url.insteadOf`` rewrite, or stray config keys into a command Jalebi runs,
    and an inherited ``GIT_DIR``/``GIT_WORK_TREE`` would redirect the operation
    to an unrelated repository. Strip them all and pin system/global config to
    nothing — Jalebi pins identity per worktree and authenticates exclusively via
    the ``GIT_CONFIG_*`` ``http.extraHeader`` it sets itself.
    """
    for key in list(env):
        if key.startswith("GIT_CONFIG") or key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_COMMON_DIR",
            "GIT_CEILING_DIRECTORIES",
            "GIT_OBJECT_DIRECTORY",
        ):
            del env[key]
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _run_git(
    args: list[str],
    cwd: Path | str | None = None,
    auth_env: dict[str, str] | None = None,
) -> str:
    """Run a git command; raise ``GitWorkspaceError`` on non-zero exit."""
    env = _clean_git_env(os.environ.copy())
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


def auth_env(token: str | None) -> dict[str, str]:
    """Public alias of ``_auth_env`` for building agent subprocess envs."""
    return _auth_env(token)


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

    @staticmethod
    def review_worktree_path(data_dir: Path, task_id: int) -> Path:
        return data_dir / "ws" / f"task-{task_id}-review"

    def _lock_for(self, full_name: str) -> threading.Lock:
        with self._locks_guard:
            if full_name not in self._locks:
                self._locks[full_name] = threading.Lock()
            return self._locks[full_name]

    def _list_local_heads(self, mirror: Path) -> list[str]:
        out = _run_git(
            ["-C", str(mirror), "for-each-ref", "--format=%(refname:short)", "refs/heads"]
        )
        return out.splitlines()

    def ensure_mirror(self, full_name: str, clone_url: str, token: str | None = None) -> Path:
        """Clone (or fetch) the bare mirror for ``full_name``. Returns the mirror path.

        Uses ``git clone --bare`` with remote branches tracked under
        ``refs/remotes/origin/*`` (normalized after clone so local-path sources behave
        like network sources), so fetching never touches the local ``jalebi/<taskId>``
        worktree branches. A local ``refs/heads/<default>`` is kept in sync with
        ``origin/<default>`` purely so the mirror HEAD is valid (``git worktree add``
        requires HEAD under ``refs/heads``).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        with self._lock_for(full_name):
            if not mirror.exists():
                mirror.parent.mkdir(parents=True, exist_ok=True)
                _run_git(["clone", "--bare", clone_url, str(mirror)], auth_env=auth)
            _run_git(
                [
                    "-C",
                    str(mirror),
                    "config",
                    "remote.origin.fetch",
                    "+refs/heads/*:refs/remotes/origin/*",
                ],
                auth_env=auth,
            )
            _run_git(
                ["-C", str(mirror), "config", "remote.origin.mirror", "false"],
                auth_env=auth,
            )
            _run_git(["-C", str(mirror), "fetch", "origin", "--prune"], auth_env=auth)
            _run_git(["-C", str(mirror), "remote", "set-head", "origin", "-a"], auth_env=auth)
            try:
                origin_head = _run_git(
                    ["-C", str(mirror), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
                )
                if "/" in origin_head:
                    default = origin_head.split("/", 1)[1]
                    _run_git(
                        [
                            "-C",
                            str(mirror),
                            "update-ref",
                            f"refs/heads/{default}",
                            f"refs/remotes/origin/{default}",
                        ],
                        auth_env=auth,
                    )
                    _run_git(
                        ["-C", str(mirror), "symbolic-ref", "HEAD", f"refs/heads/{default}"],
                        auth_env=auth,
                    )
            except GitWorkspaceError:
                pass
        return mirror

    def create_worktree(
        self, task_id: int, full_name: str, base_branch: str = "main", token: str | None = None
    ) -> Path:
        """Create a worktree on ``jalebi/<taskId>`` based on ``origin/<base_branch>``.

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
            _run_git(["-C", str(mirror), "fetch", "origin", "--prune"], auth_env=auth)
            branches = self._list_local_heads(mirror)
            if branch in branches:
                _run_git(["-C", str(mirror), "worktree", "add", str(ws), branch])
            else:
                _run_git(
                    [
                        "-C",
                        str(mirror),
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(ws),
                        f"origin/{base_branch}",
                    ]
                )
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

    def create_review_worktree(
        self,
        task_id: int,
        full_name: str,
        pr_number: int,
        token: str | None = None,
    ) -> Path:
        """Check out PR ``pr_number``'s head into a detached review worktree.

        Fetches the PR head via ``refs/pull/<n>/head`` (works for same-repo and
        cross-repo PRs without touching the fork) and checks it out detached, so
        the reviewer can read/validate but never push.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.review_worktree_path(self.config.data_dir, task_id)
        auth = _auth_env(token)
        ref = f"refs/remotes/origin/pr-{pr_number}"

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(f"mirror missing for {full_name}; call ensure_mirror first")
            if not (ws / ".git").is_file():
                _run_git(
                    [
                        "-C",
                        str(mirror),
                        "fetch",
                        "origin",
                        f"refs/pull/{pr_number}/head:{ref}",
                    ],
                    auth_env=auth,
                )
                _run_git(
                    ["-C", str(mirror), "worktree", "add", "--detach", str(ws), ref],
                    auth_env=auth,
                )
        return ws

    def remove_review_worktree(
        self, task_id: int, full_name: str, pr_number: int | None = None
    ) -> None:
        """Remove a review worktree and its mirror-local PR ref (no-op if absent)."""
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.review_worktree_path(self.config.data_dir, task_id)
        with self._lock_for(full_name):
            if not mirror.exists():
                return
            if (ws / ".git").is_file():
                _run_git(["-C", str(mirror), "worktree", "remove", "--force", str(ws)])
            if pr_number is not None:
                _run_git(
                    [
                        "-C",
                        str(mirror),
                        "update-ref",
                        "-d",
                        f"refs/remotes/origin/pr-{pr_number}",
                    ]
                )
        return None

    def list_branches(self, full_name: str, token: str | None = None) -> list[str]:
        """Branch names available in the mirror (from ``origin/*``)."""
        mirror = self.mirror_path(self.config.data_dir, full_name)
        if not mirror.exists():
            return []
        out = _run_git(
            ["-C", str(mirror), "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"],
        )
        return sorted(
            name.split("/", 1)[1]
            for name in out.splitlines()
            if "/" in name and name != "origin/HEAD"
        )

    def commits_ahead(self, worktree: Path, base_branch: str) -> int:
        """Number of commits on the worktree's HEAD beyond ``origin/<base_branch>``."""
        out = _run_git(
            ["-C", str(worktree), "rev-list", "--count", f"origin/{base_branch}..HEAD"]
        )
        return int(out or "0")

    def push_branch(self, task_id: int, full_name: str, token: str | None = None) -> None:
        """Push ``jalebi/<taskId>`` to the mirror's origin with token auth."""
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        _run_git(["-C", str(ws), "push", "origin", branch], auth_env=_auth_env(token))
