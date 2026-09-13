"""Git workspace manager: bare mirrors + per-task worktrees + token-authenticated push.

Uses the `git` CLI (not libgit2) via subprocess. The PAT is passed through the
GIT_CONFIG_* environment variables (``http.extraHeader``) so it never appears in
argv, URLs, or logs.
"""

import base64
import logging
import os
import re
import stat
import subprocess
import threading
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from jalebi.config import Config

BRANCH_PREFIX = "jalebi/"
GIT_TIMEOUT_SECONDS = 120

logger = logging.getLogger(__name__)


class GitWorkspaceError(Exception):
    """Raised when a git command fails or a precondition is unmet."""


class PushLeaseFailed(GitWorkspaceError):
    """``git push --force-with-lease`` refused because the remote moved.

    Surfaces to the UI as a 412 with the old/new SHAs so the owner can
    re-fetch, decide, and retry.
    """


# Resolver for the PR-head branch fallback: a zero-arg callable returning
# ``(head_repo_full_name, head_branch)`` or ``None`` when unresolvable.
# Lives outside GitWorkspace so the git layer never imports the GitHub
# client — the queue injects an API-backed one.
PrHeadResolver = Callable[[], tuple[str | None, str | None] | None]


def _fetch_pr_head_ref(
    mirror: Path,
    full_name: str,
    pr_number: int,
    ref: str,
    auth_env: dict[str, str] | None,
    head_resolver: PrHeadResolver | None = None,
) -> None:
    """Fetch ``refs/pull/<N>/head`` into ``ref``, same-repo branch fallback.

    GitHub does not always advertise the pull pseudo-ref (observed live: the
    API shows the PR open, ``refs/pull/<N>/merge`` exists, ``/head`` is
    missing and stays missing across close/reopen). When the direct fetch
    fails and ``head_resolver`` reports a head branch on the SAME repo,
    fetch ``refs/heads/<branch>`` into the same local ref instead —
    identical content for same-repo PRs. Fork heads live on another remote,
    so a fork (or unresolvable) head re-raises the original error; a failed
    branch fetch raises naming both attempts.
    """
    pull_spec = f"refs/pull/{pr_number}/head:{ref}"
    try:
        _run_git(["-C", str(mirror), "fetch", "origin", pull_spec], auth_env=auth_env)
        return
    except GitWorkspaceError as exc:
        try:
            resolved = head_resolver() if head_resolver is not None else None
        except Exception:
            # A failing resolver must never mask the original fetch error.
            logger.debug("pr-head resolver raised for %s#%s", full_name, pr_number)
            resolved = None
        branch = None
        if resolved:
            head_repo, head_branch = resolved
            if head_branch and (head_repo or "").lower() == full_name.lower():
                branch = head_branch
        if branch is None:
            raise
        try:
            _run_git(
                ["-C", str(mirror), "fetch", "origin", f"refs/heads/{branch}:{ref}"],
                auth_env=auth_env,
            )
        except GitWorkspaceError as branch_exc:
            raise GitWorkspaceError(
                f"git fetch {pull_spec} failed ({exc}); "
                f"same-repo branch fallback refs/heads/{branch} failed too "
                f"({branch_exc})"
            ) from branch_exc


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
    # Shared per-repo locks live on the CLASS, not the instance: every caller
    # constructs a fresh ``GitWorkspace`` (queue workers, routes, cleanup), so
    # instance-level locks would not serialize two workers hitting the same
    # bare mirror. Class-level locks make the documented per-repo serialization
    # real across the whole process.
    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, config: Config):
        self.config = config

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

    @staticmethod
    def screening_worktree_path(data_dir: Path, run_id: int) -> Path:
        return data_dir / "ws" / f"screen-{run_id}"

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

    def branch_exists(self, task_id: int, full_name: str) -> bool:
        """True if ``jalebi/<taskId>`` already exists as a local mirror branch."""
        mirror = self.mirror_path(self.config.data_dir, full_name)
        if not mirror.exists():
            return False
        return self.task_branch(task_id) in self._list_local_heads(mirror)

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
            # Prune stale worktree registrations: a task whose worktree directory
            # was deleted without being unregistered (e.g. task-delete cleanup)
            # leaves the mirror listing it as "missing but already registered",
            # which makes `worktree add` fail for a reused task id. Pruning first
            # clears those so a fresh task always gets a clean worktree.
            _run_git(["-C", str(mirror), "worktree", "prune"], auth_env=auth)
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
            else:
                # Directory already gone — clear any stale registration so the
                # task id can be reused later.
                _run_git(["-C", str(mirror), "worktree", "prune"])
            branches = _run_git(
                ["-C", str(mirror), "branch", "--format=%(refname:short)"]
            ).splitlines()
            if branch in branches:
                _run_git(["-C", str(mirror), "branch", "-D", branch])

    def reset_branch_to_base(self, task_id: int, full_name: str, base_branch: str) -> None:
        """Reset the task branch to the *current* ``origin/<base_branch>``.

        Used before the FIRST run of a task so a stale ``jalebi/<taskId>`` branch
        (reused after a DB wipe/restore, or a mirror that survived a task delete)
        can never contaminate a fresh run. Safe when the branch was just created
        fresh (a reset to the same commit is a no-op). Only the first run does
        this — reruns and follow-ups deliberately resume the accumulated work.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        with self._lock_for(full_name):
            if not mirror.exists() or not (ws / ".git").is_file():
                return  # nothing to reset; create_worktree will base it fresh
            # Fetch first so origin/<base> reflects the current remote state.
            _run_git(["-C", str(mirror), "fetch", "origin", "--prune"])
            # `checkout -B` (run inside the worktree, not the mirror) force-moves
            # the checked-out branch and updates the working tree to the current
            # base; then drop any stale untracked files so the first run starts
            # from a clean tree.
            _run_git(["-C", str(ws), "checkout", "-B", branch, f"origin/{base_branch}"])
            _run_git(["-C", str(ws), "clean", "-fd"])

    def create_review_worktree(
        self,
        task_id: int,
        full_name: str,
        pr_number: int,
        token: str | None = None,
        pr_head_resolver: PrHeadResolver | None = None,
    ) -> Path:
        """Check out PR ``pr_number``'s head into a detached review worktree.

        Fetches the PR head via ``refs/pull/<n>/head`` (works for same-repo and
        cross-repo PRs without touching the fork) and checks it out detached, so
        the reviewer can read/validate but never push. When the pull pseudo-ref
        is missing and ``pr_head_resolver`` reports a same-repo head branch,
        that branch is fetched instead (see ``_fetch_pr_head_ref``).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.review_worktree_path(self.config.data_dir, task_id)
        auth = _auth_env(token)
        ref = f"refs/remotes/origin/pr-{pr_number}"

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(f"mirror missing for {full_name}; call ensure_mirror first")
            # Always fetch the PR head so a re-run/follow-up reviews the *current*
            # head, not the one first checked out (the PR may have gained commits
            # since the initial review).
            _fetch_pr_head_ref(
                mirror, full_name, pr_number, ref, auth, pr_head_resolver
            )
            if not (ws / ".git").is_file():
                _run_git(
                    ["-C", str(mirror), "worktree", "add", "--detach", str(ws), ref],
                    auth_env=auth,
                )
            else:
                # Re-checkout the existing detached worktree to the current head.
                # Review worktrees never hold agent-pushed work, so a hard reset is
                # safe (untracked files such as node_modules are preserved).
                _run_git(["-C", str(ws), "reset", "--hard", ref], auth_env=auth)
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

    @staticmethod
    def pr_head_ref(pr_number: int) -> str:
        """Mirror-local ref tracking a PR head (``refs/remotes/origin/pr-<N>``)."""
        return f"refs/remotes/origin/pr-{pr_number}"

    @staticmethod
    def fork_branch(pr_number: int) -> str:
        """Mirror-local branch holding the merged fork-PR head (``fork-pr-<N>``)."""
        return f"fork-pr-{pr_number}"

    def fetch_pr_head(
        self, full_name: str, pr_number: int, token: str | None = None,
        pr_head_resolver: PrHeadResolver | None = None,
    ) -> str:
        """Fetch ``refs/pull/<N>/head`` into the mirror; return the local ref.

        Works for same-repo and fork PRs without touching the fork repo.
        Same-repo branch fallback applies (see ``_fetch_pr_head_ref``).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        ref = self.pr_head_ref(pr_number)
        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(f"mirror missing for {full_name}; call ensure_mirror first")
            _fetch_pr_head_ref(
                mirror, full_name, pr_number, ref, auth, pr_head_resolver
            )
        return ref

    def create_worktree_from_pr_head(
        self, task_id: int, full_name: str, pr_number: int, token: str | None = None,
        pr_head_resolver: PrHeadResolver | None = None,
    ) -> Path:
        """Create a writable ``jalebi/<taskId>`` worktree based on a PR head.

        The base is the current ``refs/pull/<N>/head`` commit (same-repo or
        fork) — not ``origin/<branch>`` — so a fix task can address review
        comments on a fork PR whose branch never exists on ``origin``.
        Same-repo branch fallback applies (see ``_fetch_pr_head_ref``).
        Reuses an existing worktree/branch for resumes (same as
        :meth:`create_worktree`).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        auth = _auth_env(token)
        ref = self.pr_head_ref(pr_number)

        if (ws / ".git").is_file():
            return ws

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(f"mirror missing for {full_name}; call ensure_mirror first")
            _run_git(["-C", str(mirror), "fetch", "origin", "--prune"], auth_env=auth)
            _fetch_pr_head_ref(
                mirror, full_name, pr_number, ref, auth, pr_head_resolver
            )
            _run_git(["-C", str(mirror), "worktree", "prune"], auth_env=auth)
            branches = self._list_local_heads(mirror)
            if branch in branches:
                _run_git(["-C", str(mirror), "worktree", "add", str(ws), branch])
            else:
                _run_git(
                    ["-C", str(mirror), "worktree", "add", "-b", branch, str(ws), ref]
                )
        return ws

    def reset_branch_to_pr_head(
        self, task_id: int, full_name: str, pr_number: int, token: str | None = None,
        pr_head_resolver: PrHeadResolver | None = None,
    ) -> None:
        """Reset ``jalebi/<taskId>`` to the *current* PR head (first-run only).

        Mirrors :meth:`reset_branch_to_base` for PR-head tasks so a stale
        branch can never contaminate a fresh run. Same-repo branch fallback
        applies (see ``_fetch_pr_head_ref``).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        ref = self.pr_head_ref(pr_number)
        auth = _auth_env(token)
        with self._lock_for(full_name):
            if not mirror.exists() or not (ws / ".git").is_file():
                return
            _fetch_pr_head_ref(
                mirror, full_name, pr_number, ref, auth, pr_head_resolver
            )
            _run_git(["-C", str(ws), "checkout", "-B", branch, ref])
            _run_git(["-C", str(ws), "clean", "-fd"])

    def merge_task_into_fork_head(
        self, task_id: int, full_name: str, pr_number: int, token: str | None = None
    ) -> list[str]:
        """Merge ``jalebi/<taskId>`` into the local ``fork-pr-<N>`` branch.

        The local fork branch is (re)created at the current PR head first, so
        the merge always targets what GitHub shows. Returns conflicting paths
        (empty = clean); on conflict the merge is aborted and the mirror is
        left clean. Caller pushes ``fork-pr-<N>`` to the fork afterwards.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        task_branch = self.task_branch(task_id)
        fork_local = self.fork_branch(pr_number)
        ref = self.pr_head_ref(pr_number)

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(
                    f"mirror missing for {full_name}; call ensure_mirror first"
                )
            _run_git(
                ["-C", str(mirror), "fetch", "origin", f"refs/pull/{pr_number}/head:{ref}"],
                auth_env=auth,
            )
            try:
                task_sha = _run_git(
                    ["-C", str(mirror), "rev-parse", f"refs/heads/{task_branch}"]
                )
            except GitWorkspaceError as exc:
                raise GitWorkspaceError(
                    f"cannot resolve branches for fork update: {exc}"
                ) from None
            # (Re)create the local fork branch exactly at the current PR head.
            _run_git(["-C", str(mirror), "branch", "-f", fork_local, ref], auth_env=auth)
            try:
                fork_sha = _run_git(
                    ["-C", str(mirror), "rev-parse", f"refs/heads/{fork_local}"]
                )
            except GitWorkspaceError as exc:
                raise GitWorkspaceError(
                    f"cannot resolve branches for fork update: {exc}"
                ) from None
            if task_sha == fork_sha:
                return []  # nothing to do

            temp_ws = self.worktree_path(self.config.data_dir, task_id) / "publish-fork-tmp"
            if temp_ws.exists():
                _run_git(["-C", str(mirror), "worktree", "remove", "--force", str(temp_ws)])
            _run_git(
                ["-C", str(mirror), "worktree", "add", "-B", fork_local, str(temp_ws), fork_local],
                auth_env=auth,
            )
            try:
                env = _clean_git_env(os.environ.copy())
                if auth:
                    env.update(auth)
                proc = subprocess.run(
                    ["git", "-C", str(temp_ws), "merge", task_branch],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=GIT_TIMEOUT_SECONDS,
                )
                if proc.returncode == 0:
                    return []
                try:
                    _run_git(
                        ["-C", str(temp_ws), "rev-parse", "--verify", "-q", "MERGE_HEAD"]
                    )
                except GitWorkspaceError:
                    raise GitWorkspaceError(
                        f"git merge failed: {(proc.stderr or '').strip()}"
                    ) from None
                try:
                    conflicts = _run_git(
                        ["-C", str(temp_ws), "diff", "--name-only", "--diff-filter=U"]
                    ).splitlines()
                except GitWorkspaceError:
                    conflicts = []
                _run_git(["-C", str(temp_ws), "merge", "--abort"])
                return conflicts
            finally:
                _run_git(["-C", str(mirror), "worktree", "remove", "--force", str(temp_ws)])

    def push_fork_head(
        self,
        full_name: str,
        pr_number: int,
        head_branch: str,
        fork_repo: str,
        expected_old_sha: str | None,
        token: str | None = None,
    ) -> None:
        """Push local ``fork-pr-<N>`` to ``<head_branch>`` on the fork repo.

        Pushes to ``https://github.com/<fork_repo>.git`` (never adds a remote —
        the queue owns fork writes; agents are still forbidden from adding
        remotes). Uses ``--force-with-lease`` so a concurrently-moved fork
        branch is refused as :class:`PushLeaseFailed` instead of clobbered.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        fork_local = self.fork_branch(pr_number)
        fork_url = f"https://github.com/{fork_repo}.git"
        with self._lock_for(full_name):
            env = _clean_git_env(os.environ.copy())
            if auth:
                env.update(auth)
            if expected_old_sha:
                lease = f"--force-with-lease={head_branch}:{expected_old_sha}"
            else:
                lease = "--force-with-lease"
            proc = subprocess.run(
                ["git", "-C", str(mirror), "push", lease, fork_url, f"{fork_local}:{head_branch}"],
                env=env,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if proc.returncode == 0:
                return
            stderr = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            if "(stale info)" in stderr:
                raise PushLeaseFailed(
                    f"remote fork branch {head_branch} moved since last fetch — "
                    "re-fetch and retry to confirm the new state"
                ) from None
            raise GitWorkspaceError(
                f"git push {lease} to fork {fork_repo} failed: {stderr}"
            )

    def create_detached_worktree(
        self,
        full_name: str,
        branch: str,
        worktree_path: Path,
        token: str | None = None,
    ) -> Path:
        """Check out ``origin/<branch>`` into a detached read-only worktree.

        Screening worktrees (PRD F10) audit a branch HEAD with a read-only
        prompt — the agent never pushes. The worktree is detached at the
        current ``origin/<branch>`` HEAD; on reuse it is hard-reset to the
        latest HEAD. Requires the mirror to exist (``ensure_mirror`` first).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        ref = f"refs/remotes/origin/{branch}"

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(f"mirror missing for {full_name}; call ensure_mirror first")
            _run_git(
                ["-C", str(mirror), "fetch", "origin", branch],
                auth_env=auth,
            )
            if not (worktree_path / ".git").is_file():
                _run_git(
                    ["-C", str(mirror), "worktree", "add", "--detach", str(worktree_path), ref],
                    auth_env=auth,
                )
            else:
                _run_git(["-C", str(worktree_path), "reset", "--hard", ref], auth_env=auth)
        return worktree_path

    def remove_detached_worktree(self, worktree_path: Path, full_name: str) -> None:
        """Remove a detached screening worktree (no-op if absent)."""
        mirror = self.mirror_path(self.config.data_dir, full_name)
        with self._lock_for(full_name):
            if not mirror.exists():
                return
            if (worktree_path / ".git").is_file():
                _run_git(
                    ["-C", str(mirror), "worktree", "remove", "--force", str(worktree_path)]
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
        """Number of commits on the worktree's HEAD beyond ``origin/<base_branch>``.

        Best-effort: returns 0 on any error (no upstream, missing worktree,
        unborn HEAD, etc) so the caller can treat "nothing ahead" as a
        soft signal instead of an exception.
        """
        try:
            out = _run_git(
                [
                    "-C",
                    str(worktree),
                    "rev-list",
                    "--count",
                    f"origin/{base_branch}..HEAD",
                ]
            )
        except GitWorkspaceError:
            return 0
        try:
            return int(out)
        except (TypeError, ValueError):
            return 0

    def working_tree_status(self, worktree: Path) -> list[str]:
        """Porcelain status lines: dirty (modified/staged/untracked) paths.

        Empty list = clean working tree. Used at run end to surface agent work
        that was never committed (a `done` run must not silently look clean).
        """
        out = _run_git(["-C", str(worktree), "status", "--porcelain"])
        return [ln for ln in out.splitlines() if ln.strip()]

    def diff_working_tree(self, worktree: Path) -> str:
        """Diff of uncommitted changes (working tree + staged) vs HEAD.

        Covers modified/staged tracked files; untracked files are not included
        (they are surfaced by name via ``working_tree_status``). Raises
        ``GitWorkspaceError`` when the worktree has no commits yet.
        """
        try:
            return _run_git(["-C", str(worktree), "diff", "HEAD"])
        except GitWorkspaceError:
            return _run_git(["-C", str(worktree), "diff", "--cached"])

    def diff_against_target(self, worktree: Path, target_branch: str) -> str:
        """Unified diff of the worktree's committed work vs ``origin/<target>``.

        Uses three-dot semantics (merge-base..HEAD, i.e. the PR diff). Falls back
        to a two-dot committed-range diff (``origin/<target>..HEAD`` — never the
        working tree, which would include uncommitted agent scratch files) if the
        branches have no common ancestor.
        """
        try:
            return _run_git(
                ["-C", str(worktree), "diff", f"origin/{target_branch}...HEAD"]
            )
        except GitWorkspaceError:
            return _run_git(
                ["-C", str(worktree), "diff", f"origin/{target_branch}..HEAD"]
            )

    def diff_against_base(self, worktree: Path, base_branch: str) -> str:
        """Cherry-pick-aware cumulative diff of ``HEAD`` vs the merge-base with
        ``origin/<base_branch>``.

        Drops patch-equivalent commits (``git log --cherry-pick --right-only``):
        a commit whose change is on the base (e.g. cherry-picked or rebased onto
        it) is not shown, and a fully-merged branch collapses to ``""``. Falls
        back to a plain two-dot range diff if there is no common ancestor
        (mirrors ``diff_against_target``). Read-only — never mutates the worktree.
        """
        try:
            base = _run_git(
                ["-C", str(worktree), "merge-base", f"origin/{base_branch}", "HEAD"]
            )
        except GitWorkspaceError:
            return _run_git(
                ["-C", str(worktree), "diff", f"origin/{base_branch}..HEAD"]
            )
        unique = _run_git(
            [
                "-C",
                str(worktree),
                "log",
                "--cherry-pick",
                "--right-only",
                "--reverse",
                f"{base}..HEAD",
                "--format=%H",
            ]
        )
        if not unique.strip():
            return ""  # every commit is patch-equivalent to the base — already merged
        return _run_git(["-C", str(worktree), "diff", f"{base}..HEAD"])

    # Untracked files are skipped once they exceed this size; their full content
    # would dominate the diff and the agent typically does not need them surfaced.
    _UNTRACKED_DIFF_SIZE_CAP = 1 * 1024 * 1024

    def diff_with_untracked(self, worktree: Path, tracked_diff: str) -> str:
        """Append synthesized add-file hunks for untracked files to ``tracked_diff``.

        ``git ls-files --others --exclude-standard`` lists untracked, non-ignored
        files. Each is rendered as a full ``diff --git`` / ``new file mode`` /
        ``--- /dev/null`` / ``+++ b/<rel>`` hunk so T3's unified-diff parser
        (which splits on ``diff --git``) renders them in place. Binary files
        (NUL byte in the first 8 KB) and files over
        ``_UNTRACKED_DIFF_SIZE_CAP`` are omitted, as are symlinks anywhere in
        a path. Pure read of the working tree.
        """
        lines = _run_git(
            ["-C", str(worktree), "ls-files", "--others", "--exclude-standard"]
        )
        extra: list[str] = []
        root = worktree.resolve()
        for rel in (ln.strip() for ln in lines.splitlines() if ln.strip()):
            relative = Path(rel)
            if relative.is_absolute() or any(
                part == ".." or part.lower() == ".git" for part in relative.parts
            ):
                continue
            try:
                if not (root / relative).resolve().is_relative_to(root):
                    continue
                # Open each component relative to its verified parent, refusing
                # symlinks at open time so a concurrent swap cannot bypass the
                # containment check. Nonblocking open also avoids hanging on FIFOs.
                with ExitStack() as opened:
                    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                    parent_fd = os.open(root, flags)
                    opened.callback(os.close, parent_fd)
                    for part in relative.parts[:-1]:
                        parent_fd = os.open(part, flags, dir_fd=parent_fd)
                        opened.callback(os.close, parent_fd)
                    fd = os.open(
                        relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=parent_fd,
                    )
                    with os.fdopen(fd, "rb") as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        if info.st_size > self._UNTRACKED_DIFF_SIZE_CAP:
                            continue
                        content = source.read(self._UNTRACKED_DIFF_SIZE_CAP + 1)
                if len(content) > self._UNTRACKED_DIFF_SIZE_CAP:
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            if b"\x00" in content[:8192]:
                continue  # binary
            text_lines = content.decode("utf-8", "replace").splitlines(keepends=True)
            extra.append(f"diff --git a/{rel} b/{rel}")
            extra.append("new file mode 100644")
            extra.append("--- /dev/null")
            extra.append(f"+++ b/{rel}")
            extra.append(f"@@ -0,0 +1,{len(text_lines)} @@")
            for ln in text_lines:
                extra.append(f"+{ln}" if ln.endswith("\n") else f"+{ln}")
        if not extra:
            return tracked_diff
        joined = "\n".join(extra) + "\n"
        return f"{tracked_diff.rstrip()}\n\n{joined}" if tracked_diff.strip() else joined

    # merge-tree output (--write-tree): one file-info line per unmerged path plus
    # a CONFLICT (<kind>) line. Paths come from the file-info lines (stable
    # across git versions and conflict kinds), kinds from the CONFLICT lines.
    # A content conflict lists stages 1+2+3; a modify/delete lists 1+2 or 1+3.
    # Any stage entry gives the path; we keep each path once.
    _CONFLICT_FILE_INFO = re.compile(r"^\S+ \S+ [123]\t(.+)$", re.MULTILINE)
    _CONFLICT_KIND = re.compile(r"^CONFLICT \(([^)]+)\):", re.MULTILINE)

    def predict_conflicts(
        self, full_name: str, task_id: int, base_ref: str
    ) -> list[tuple[str, str]]:
        """Predict merge conflicts between ``origin/<base_ref>`` and ``jalebi/<task_id>``.

        Runs ``git merge-tree --write-tree`` on the **mirror** (never the
        worktree — no worktree mutation, no abort dance). Returns
        ``[(kind, path), ...]``; empty when the merge would be clean. Requires
        git >= 2.38. The caller runs ``ensure_mirror`` first.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        if not mirror.exists():
            raise GitWorkspaceError(
                f"mirror missing for {full_name}; call ensure_mirror first"
            )
        env = _clean_git_env(os.environ.copy())
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(mirror),
                "merge-tree",
                "--write-tree",
                f"origin/{base_ref}",
                self.task_branch(task_id),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        if proc.returncode == 0:
            return []  # clean merge
        if proc.returncode != 1:
            raise GitWorkspaceError(
                f"git merge-tree failed: {(proc.stderr or proc.stdout or '').strip()}"
            )
        out = proc.stdout or ""
        paths: list[str] = []
        for m in self._CONFLICT_FILE_INFO.finditer(out):
            path = m.group(1)
            if path not in paths:
                paths.append(path)
        kinds = self._CONFLICT_KIND.findall(out) or ["content"] * len(paths)
        return list(zip(kinds[: len(paths)], paths))

    def assert_publish_branch(self, task_id: int) -> None:
        """Refuse to publish when the worktree HEAD is not on ``jalebi/<taskId>``.

        An agent may checkout/detach onto another branch; publishing would then
        push the wrong ref. Raises ``GitWorkspaceError`` with a clear message;
        the queue translates it into ``PublishError``.
        """
        ws = self.worktree_path(self.config.data_dir, task_id)
        expected = self.task_branch(task_id)
        if not (ws / ".git").is_file():
            raise GitWorkspaceError(f"worktree missing for task {task_id}")
        try:
            head = _run_git(["-C", str(ws), "symbolic-ref", "--short", "HEAD"])
        except GitWorkspaceError:
            head = "(detached)"
        if head != expected:
            raise GitWorkspaceError(
                f"branch mismatch; agent left HEAD on {head}, expected {expected}"
            )

    def rev_parse_head(self, worktree: Path) -> str:
        """Full SHA of the worktree's HEAD. Raises when the worktree is unborn."""
        return _run_git(["-C", str(worktree), "rev-parse", "HEAD"])

    def push_branch(self, task_id: int, full_name: str, token: str | None = None) -> None:
        """Push ``jalebi/<taskId>`` to the mirror's origin with token auth."""
        ws = self.worktree_path(self.config.data_dir, task_id)
        branch = self.task_branch(task_id)
        # Serialize with the mirror lock: a push racing a fetch/worktree-add on
        # the same repo surfaces as a spurious error otherwise.
        with self._lock_for(full_name):
            _run_git(["-C", str(ws), "push", "origin", branch], auth_env=_auth_env(token))

    def merge_origin_into(
        self, worktree: Path, full_name: str, base_branch: str, token: str | None = None
    ) -> list[str]:
        """Sync the task branch with ``origin/<base_branch>`` before pushing.

        Fetches origin (updating the mirror the worktree shares), then merges
        ``origin/<base_branch>`` into the worktree's current branch so the PR is
        up to date with its base and mergable. On conflict the merge is ABORTED
        (the worktree is left as it was) and the conflicting paths are returned;
        the caller surfaces them instead of pushing a conflicted branch.

        Returns a list of conflicting file paths (empty = clean merge).
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(
                    f"mirror missing for {full_name}; call ensure_mirror first"
                )
            _run_git(["-C", str(mirror), "fetch", "origin", "--prune"], auth_env=auth)
            env = _clean_git_env(os.environ.copy())
            if auth:
                env.update(auth)
            proc = subprocess.run(
                ["git", "-C", str(worktree), "merge", f"origin/{base_branch}"],
                env=env,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if proc.returncode == 0:
                return []
            # Merge failed. We deliberately run `git merge` via subprocess (not
            # _run_git) because its non-zero exit is meaningful: it is a conflict,
            # not an error to raise. If a merge is actually in progress
            # (MERGE_HEAD exists), abort it so the worktree is restored for the
            # agent/follow-up — never leave a conflicted or half-merged state.
            # Failures that never started a merge (unrelated histories, local
            # changes would be overwritten) leave no MERGE_HEAD and a clean
            # worktree, so there is nothing to abort.
            try:
                _run_git(
                    ["-C", str(worktree), "rev-parse", "--verify", "-q", "MERGE_HEAD"]
                )
            except GitWorkspaceError:
                raise GitWorkspaceError(
                    f"git merge failed: {(proc.stderr or '').strip()}"
                ) from None
            try:
                conflicts = _run_git(
                    ["-C", str(worktree), "diff", "--name-only", "--diff-filter=U"]
                ).splitlines()
            except GitWorkspaceError:
                conflicts = []
            _run_git(["-C", str(worktree), "merge", "--abort"])
            return conflicts

    # -- publish modes: update_pr + push_branch --------------------------

    def current_remote_sha(
        self, full_name: str, branch: str, token: str | None = None
    ) -> str | None:
        """Return the SHA of ``origin/<branch>``, or ``None`` if absent.

        Fetches first so the value reflects current remote state (and primes
        the mirror's tracking ref for ``--force-with-lease``). A failed
        fetch (e.g. the branch doesn't exist remotely) returns ``None``
        rather than raising — callers check the SHA before deciding what
        mode to use.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        with self._lock_for(full_name):
            try:
                _run_git(["-C", str(mirror), "fetch", "origin", branch], auth_env=auth)
            except GitWorkspaceError:
                return None
            try:
                return _run_git(
                    ["-C", str(mirror), "rev-parse", "--verify", f"refs/remotes/origin/{branch}"]
                )
            except GitWorkspaceError:
                return None

    def local_ref_sha(self, full_name: str, branch: str) -> str | None:
        """Return the SHA of the mirror's ``refs/heads/<branch>``, or ``None``.

        Used by the post-push log to read the local ref (which the push
        already updated implicitly), avoiding a redundant network fetch.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        try:
            return _run_git(
                ["-C", str(mirror), "rev-parse", "--verify", f"refs/heads/{branch}"]
            )
        except GitWorkspaceError:
            return None

    def fast_forward_into(
        self,
        task_id: int,
        full_name: str,
        target_branch: str,
        token: str | None = None,
    ) -> list[str]:
        """Merge ``jalebi/<task_id>`` into a local ``target_branch``.

        Caller is responsible for fetching the branch first (so the local
        tracking ref matches the remote — use ``current_remote_sha``).

        Returns a list of conflicting file paths (empty = clean merge). On
        conflict the merge is ABORTED so the mirror is left in a clean state.
        A fast-forward is preferred when possible; otherwise a real merge
        commit is created.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        task_branch = self.task_branch(task_id)

        with self._lock_for(full_name):
            if not mirror.exists():
                raise GitWorkspaceError(
                    f"mirror missing for {full_name}; call ensure_mirror first"
                )
            try:
                _run_git(
                    ["-C", str(mirror), "rev-parse", "--verify", f"refs/heads/{target_branch}"]
                )
            except GitWorkspaceError:
                _run_git(
                    ["-C", str(mirror), "branch", target_branch, f"origin/{target_branch}"],
                    auth_env=auth,
                )
            try:
                task_sha = _run_git(
                    ["-C", str(mirror), "rev-parse", f"refs/heads/{task_branch}"]
                )
                target_sha = _run_git(
                    ["-C", str(mirror), "rev-parse", f"refs/heads/{target_branch}"]
                )
            except GitWorkspaceError as exc:
                raise GitWorkspaceError(
                    f"cannot resolve branches for fast-forward: {exc}"
                ) from None
            if task_sha == target_sha:
                return []  # nothing to do

            temp_ws = self.worktree_path(self.config.data_dir, task_id) / "publish-tmp"
            if temp_ws.exists():
                _run_git(["-C", str(mirror), "worktree", "remove", "--force", str(temp_ws)])
            _run_git(
                [
                    "-C",
                    str(mirror),
                    "worktree",
                    "add",
                    "-B",
                    target_branch,
                    str(temp_ws),
                    target_branch,
                ],
                auth_env=auth,
            )
            try:
                env = _clean_git_env(os.environ.copy())
                if auth:
                    env.update(auth)
                proc = subprocess.run(
                    ["git", "-C", str(temp_ws), "merge", task_branch],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=GIT_TIMEOUT_SECONDS,
                )
                if proc.returncode == 0:
                    return []
                # Conflict path — same shape as merge_origin_into.
                try:
                    _run_git(
                        [
                            "-C",
                            str(temp_ws),
                            "rev-parse",
                            "--verify",
                            "-q",
                            "MERGE_HEAD",
                        ]
                    )
                except GitWorkspaceError:
                    raise GitWorkspaceError(
                        f"git merge failed: {(proc.stderr or '').strip()}"
                    ) from None
                try:
                    conflicts = _run_git(
                        [
                            "-C",
                            str(temp_ws),
                            "diff",
                            "--name-only",
                            "--diff-filter=U",
                        ]
                    ).splitlines()
                except GitWorkspaceError:
                    conflicts = []
                _run_git(["-C", str(temp_ws), "merge", "--abort"])
                return conflicts
            finally:
                _run_git(["-C", str(mirror), "worktree", "remove", "--force", str(temp_ws)])

    def push_existing_branch(
        self, full_name: str, branch: str, token: str | None = None
    ) -> None:
        """Force-push ``branch`` to ``origin`` with ``--force-with-lease``.

        Caller MUST have fetched the branch first (``current_remote_sha`` does
        this). The lease compares the local tracking ref to the remote; if the
        remote moved since the fetch, the push is refused and
        :class:`PushLeaseFailed` is raised so the UI can surface a 412.

        Lease detection: instead of pattern-matching git's stderr (which varies
        across versions), we read git's own structured error output. Modern
        git (>= 2.30) prints ``! [rejected] <remote_ref> -> <local_ref>
        (stale info)`` to stderr for a lease failure; the substring
        ``(stale info)`` is part of the protocol and is stable across
        localisations. We match on the parenthetical alone to avoid false
        positives from generic "non-fast-forward" messages that don't imply
        a stale lease.
        """
        mirror = self.mirror_path(self.config.data_dir, full_name)
        auth = _auth_env(token)
        with self._lock_for(full_name):
            env = _clean_git_env(os.environ.copy())
            if auth:
                env.update(auth)
            proc = subprocess.run(
                ["git", "-C", str(mirror), "push", "--force-with-lease", "origin", branch],
                env=env,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if proc.returncode == 0:
                return
            stderr = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            if "(stale info)" in stderr:
                raise PushLeaseFailed(
                    f"remote branch {branch} moved since last fetch — "
                    "re-fetch and retry to confirm the new state"
                ) from None
            raise GitWorkspaceError(
                f"git push --force-with-lease {branch} failed: {stderr}"
            )
