"""Task queue + worker pool + run lifecycle (PRD F3, F16)."""

import json
import logging
import os
import queue
import signal
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from sqlalchemy import func, select

from jalebi import (
    artifacts,
    catalog,
    checkruns,
    clock,
    envvars,
    masking,
    messaging,
    notify,
    prompts,
    reviews,
    secrets,
    settings,
    tasks,
    worktree_bootstrap,
)
from jalebi.adapters import get_adapter
from jalebi.adapters.types import AgentEvent
from jalebi.config import Config
from jalebi.db import CatalogAgent, Repo, Run, Session, Task, now
from jalebi.events import TaskEvents
from jalebi.git_workspace import GitWorkspace, GitWorkspaceError, PushLeaseFailed
from jalebi.github import GitHubClient

logger = logging.getLogger(__name__)

MAX_STEPS = 500
MAX_STEP_TEXT = 2000
MAX_DIFF_BYTES = 512 * 1024
KILL_GRACE_SECONDS = 5
DEFAULT_TIMEOUT_MINUTES = 60
STALL_TIMEOUT_SECONDS = 600  # default no-output stall threshold (settings-overridable)
MAX_RECOVERY_TIMEOUT_MINUTES = 180  # cap on auto-recovery timeout escalation

GIT_USER_NAME = "Jalebi"
GIT_USER_EMAIL = "jalebi@localhost"


class PublishError(Exception):
    """Base error for the publish step (push + open PR)."""


class PublishConflict(PublishError):
    """The task branch conflicts with the PR target; nothing was pushed."""


_GIT_ENV_PREFIXES = ("GIT_CONFIG",)
_GIT_ENV_KEYS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_OBJECT_DIRECTORY",
)


def _build_agent_env(token: str | None) -> dict[str, str | None]:
    """Env for the agent subprocess: the selected account's PAT + bot identity.

    ``gh`` is deliberately never authenticated: any inherited ``GH_TOKEN``/
    ``GITHUB_TOKEN`` are stripped so even a guard bypass cannot act via gh.
    Inherited ``GIT_CONFIG_*``/``GIT_DIR`` state is stripped so the agent's git
    commands cannot be redirected by the parent shell's environment.

    The agent gets ``JALEBI_GITHUB_TOKEN`` = the **selected account's** PAT for
    GitHub REST API use (curl), but NO git push credentials — ``auth_env`` is
    deliberately not applied, so the agent structurally cannot ``git push``;
    Jalebi is the only pusher. If ``token`` is None there is no token to expose.
    """
    env: dict[str, str | None] = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_GIT_ENV_PREFIXES) and key not in _GIT_ENV_KEYS
    }
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": GIT_USER_NAME,
            "GIT_AUTHOR_EMAIL": GIT_USER_EMAIL,
            "GIT_COMMITTER_NAME": GIT_USER_NAME,
            "GIT_COMMITTER_EMAIL": GIT_USER_EMAIL,
            "GH_CONFIG_DIR": "/nonexistent-jalebi-gh",
            "GH_TOKEN": None,  # None → removed from the inherited environ
            "GITHUB_TOKEN": None,
        }
    )
    if token:
        env["JALEBI_GITHUB_TOKEN"] = token
    else:
        # Never leak an inherited JALEBI_GITHUB_TOKEN (e.g. from .env) into the
        # agent environment.
        env["JALEBI_GITHUB_TOKEN"] = None
    return env


class _RunState:
    """Live run bookkeeping for timeout/cancel coordination."""

    def __init__(self, handle):
        self.handle = handle
        self.reason: str | None = None  # "timeout" | "cancelled" | "stalled"
        self.last_event = time.monotonic()  # updated as agent events stream in
        self.stall_timeout: float = STALL_TIMEOUT_SECONDS  # live setting, set at start
        # Last agent message text seen (for progress notifications), updated by
        # the event loop in _stream_and_finish.
        self.last_step_text: str | None = None
        self.last_phase: str | None = None
        self.last_progress_notify = time.monotonic()


def _kill_group(pid: int, sig: int = signal.SIGTERM) -> None:
    """Signal ``pid``; if it is its own process-group leader, signal the whole group.

    Agent children are spawned with ``start_new_session=True`` so they become
    group leaders; killing the group reaches MCP servers and other grandchildren
    that would otherwise be orphaned by a single-process kill.
    """
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _kill_proc(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if pid is None:
        # Test double without a real pid — fall back to the duck-typed API.
        try:
            proc.terminate()
            proc.wait(timeout=KILL_GRACE_SECONDS)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        return
    _kill_group(pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except Exception:
        pass
    if proc.poll() is None:
        _kill_group(pid, signal.SIGKILL)


def _kill_pid(pid: int) -> None:
    """Terminate a process (and its group) by pid (orphaned agent after a crash)."""
    _kill_group(pid, signal.SIGTERM)
    for _ in range(KILL_GRACE_SECONDS * 5):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    _kill_group(pid, signal.SIGKILL)


class TaskQueue:
    def __init__(
        self,
        config: Config,
        db_session_factory=None,
    ):
        self.config = config
        self._db_session_factory = db_session_factory
        # Phase 4 T4.3 — wire TaskEvents with DB persistence when running
        # under create_app. Standalone TaskQueue() (tests) stays memory-only.
        # No automatic prune: timeline data is never auto-deleted (owner
        # decision) — it grows until pruned manually via Settings → Data.
        self.events = TaskEvents(
            db_session_factory=db_session_factory,
        )
        # Items: ("task", task_id) | ("followup", task_id, body) | None (stop).
        self._queue: queue.Queue[object] = queue.Queue()
        self._running: dict[int, _RunState] = {}
        self._running_lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._pool_lock = threading.Lock()
        self._target_concurrency = 0

    # -- pool lifecycle ----------------------------------------------------

    def start(self, concurrency: int) -> None:
        """Spawn ``concurrency`` workers (0 = paused queue)."""
        self.set_concurrency(concurrency)

    def set_concurrency(self, n: int) -> int:
        """Resize the pool live (0 = pause). Returns live worker count."""
        if n < 0:
            n = 0
        self._target_concurrency = n
        if n == 0:
            return 0  # workers pause (checked each loop) but stay alive
        active = self._live_workers()
        if n > active:
            for _ in range(n - active):
                thread = threading.Thread(
                    target=self._worker_loop, daemon=True, name="jalebi-worker"
                )
                thread.start()
                with self._pool_lock:
                    self._workers.append(thread)
        elif n < active:
            for _ in range(active - n):
                self._queue.put(None)
            deadline = time.monotonic() + 3
            while self._live_workers() > n and time.monotonic() < deadline:
                time.sleep(0.05)
        return self._live_workers()

    def _live_workers(self) -> int:
        with self._pool_lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            return len(self._workers)

    def stop(self) -> None:
        active = self._live_workers()
        for _ in range(active):
            self._queue.put(None)
        for thread in self._workers:
            thread.join(timeout=5)

    def enqueue(self, task_id: int) -> None:
        self._queue.put(("task", task_id))

    def enqueue_followup(
        self,
        task_id: int,
        body: str,
        pat_name: str | None = None,
        model: str | None = None,
        auto: bool = False,
        cli: str | None = None,
    ) -> None:
        """Queue a session resume. ``auto=True`` marks an auto-recovery resume
        (no ``followups`` row is recorded — it isn't a user follow-up). ``cli``
        is an optional backend override; a backend different from the task's own
        starts a fresh session seeded with the prior conversation."""
        self._queue.put(("followup", task_id, body, pat_name, model, auto, cli))

    # -- worker loop -------------------------------------------------------

    def _worker_loop(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                try:
                    item = self._queue.get(timeout=1)
                except queue.Empty:
                    continue
                if item is None:
                    return
                if not isinstance(item, tuple):
                    continue
                parts = cast("Sequence[Any]", item)
                if self._target_concurrency == 0:
                    self._queue.put(item)
                    time.sleep(0.5)
                    continue
                try:
                    if parts[0] == "followup":
                        auto = bool(parts[5]) if len(parts) > 5 else False
                        cli = parts[6] if len(parts) > 6 else None
                        self._run_followup(
                            parts[1], parts[2], parts[3], parts[4], auto=auto, cli=cli
                        )
                    else:
                        self._run_task(parts[1])
                except Exception:
                    logger.exception("worker crashed on %s", item)
        finally:
            with self._pool_lock:
                self._workers = [w for w in self._workers if w is not current]

    # -- cancellation ------------------------------------------------------

    def cancel(self, task_id: int) -> bool:
        """Kill a running task's process; returns True if it was running."""
        with self._running_lock:
            state = self._running.get(task_id)
        if state is None:
            return False
        state.reason = "cancelled"
        if state.handle is not None:
            _kill_proc(state.handle.proc)
        return True

    def recover(self) -> int:
        """Recover from a crash: mark interrupted, kill orphans, requeue queued."""
        session = Session()
        try:
            running_runs = list(
                session.execute(select(Run).where(Run.status == "running")).scalars()
            )
            for run in running_runs:
                if run.pid:
                    _kill_pid(run.pid)
                run.status = "interrupted"
                run.finished_at = now()
            task_ids = {r.task_id for r in running_runs}
            running_tasks = list(
                session.execute(select(Task).where(Task.status == "running")).scalars()
            )
            for task in running_tasks:
                task.status = "interrupted"
                task.updated_at = now()
                task_ids.add(task.id)
            queued = list(
                session.execute(select(Task).where(Task.status == "queued")).scalars()
            )
            for task in queued:
                self.enqueue(task.id)
            session.commit()
            # Count interrupted *tasks* even when they had no running run row.
            run_task_ids = {r.task_id for r in running_runs}
            orphan_tasks = sum(1 for t in running_tasks if t.id not in run_task_ids)
            return len(running_runs) + len(queued) + orphan_tasks
        finally:
            session.close()

    # -- run lifecycle -----------------------------------------------------

    @staticmethod
    def _worktree_base(task: Task) -> str:
        """Branch the task's worktree is created from (and reset to).

        issue_fix uses the SINGLE-target model (PRD F8 superseded): the worktree
        is based on the target/PR-base branch, so the PR diff is exactly the
        agent's fix and merges cleanly by construction. Other types keep the
        source branch as the worktree base.
        """
        if task.type == "issue_fix":
            return task.target_branch or task.source_branch or "main"
        return task.source_branch or "main"

    @staticmethod
    def _pr_head_number(task: Task) -> int | None:
        """PR number when the task is based on a PR head sentinel, else None."""
        return tasks.pr_head_source_number(task.source_branch)

    def _ensure_task_worktree(
        self, git: GitWorkspace, task: Task, repo: Repo, token: str | None
    ):
        """Create (or reuse) the task's writable worktree, PR-head aware.

        Freeform-family tasks with ``source_branch == "pr/<N>/head"`` are based
        on the current PR head commit (same-repo or fork) so review feedback on
        a fork PR can be addressed even though the fork branch never exists on
        ``origin``. All other tasks use :meth:`_worktree_base`.
        """
        pr_number = self._pr_head_number(task)
        if pr_number is not None:
            if task.type in ("issue_fix", "pr_review"):
                raise RuntimeError(
                    f"task {task.id}: pr-head source is only valid for freeform tasks"
                )
            return git.create_worktree_from_pr_head(task.id, repo.full_name, pr_number, token)
        return git.create_worktree(task.id, repo.full_name, self._worktree_base(task), token)

    def _reset_task_branch(
        self, git: GitWorkspace, task: Task, repo: Repo, token: str | None
    ) -> None:
        """First-run reset to the current base (PR-head aware)."""
        pr_number = self._pr_head_number(task)
        if pr_number is not None:
            git.reset_branch_to_pr_head(task.id, repo.full_name, pr_number, token)
        else:
            git.reset_branch_to_base(task.id, repo.full_name, self._worktree_base(task))

    @staticmethod
    def _agent_token_for(task: Task, token: str | None) -> str | None:
        """The token to expose to the agent subprocess.

        Every task type gets the **selected account's** token as
        ``JALEBI_GITHUB_TOKEN`` — freeform included — so the agent acts as the
        exact account the owner picked for the task. There is no default or
        fallback: if resolution produced ``None`` there is no token to expose.
        """
        return token

    def _agent_env(
        self, session, task: Task, repo: Repo, token: str | None
    ) -> dict[str, str | None]:
        """The agent subprocess env: base env + the task's selected env vars.

        Env vars are merged on top of the Jalebi-built env (never the other way
        around), so a task cannot override the token/identity/git hygiene the
        queue pins. Values are already masked at ingest via the masker built
        with them as secrets.
        """
        env = _build_agent_env(self._agent_token_for(task, token))
        task_env = envvars.values_for_names(session, repo.id, envvars.task_env_names(task))
        env.update({k: v for k, v in task_env.items() if v is not None})
        return env

    def _catalog_agent(self, session, task: Task) -> CatalogAgent | None:
        """The enabled catalog agent backing ``task``, or ``None``.

        ``tasks.agent_id`` is a plain slug (no FK). A task whose agent was
        deleted or disabled after creation falls back to the default build agent
        rather than failing the run — the task's stored ``model``/``cli`` (which
        the route resolved from the agent at creation) still apply.
        """
        if not task.agent_id:
            return None
        agent = catalog.agent_by_slug(session, task.agent_id)
        if agent is None or not agent.enabled:
            logger.warning(
                "task %s references missing/disabled catalog agent %s; using defaults",
                task.id,
                task.agent_id,
            )
            return None
        return agent

    def _agent_run_opts(
        self, session, task: Task, cli: str
    ) -> tuple[str, list[dict[str, str]] | None]:
        """Resolve the effective CLI + skills for a catalog agent.

        Returns ``(cli, skills)``. Precedence for the CLI is the same as for the
        model everywhere else: **explicit task-level override > live agent pin >
        default**. ``cli`` already carries the task's override + settings default,
        so the agent's pinned ``cli`` applies only when the task didn't pin one.
        ``skills`` is the agent's skill list for the bootstrap.
        """
        agent = self._catalog_agent(session, task)
        if agent is None:
            return cli, None
        effective_cli = task.cli or agent.cli or cli
        return effective_cli, catalog.resolve_skills(session, agent)

    def _prepare_run(self, session, task: Task, cli: str) -> Run:
        """Open a fresh run row and flip the task to ``running``."""
        seq = session.execute(
            select(func.max(Run.seq)).where(Run.task_id == task.id)
        ).scalar()
        seq = int(seq) + 1 if seq is not None else 1
        run = Run(
            task_id=task.id,
            seq=seq,
            cli=cli,
            model=task.model,
            started_at=now(),
            status="running",
        )
        session.add(run)
        task.status = "running"
        task.updated_at = now()
        # A new run re-arms attention: a dismissal from a previous run must
        # not hide this run's future needs_you (committed with the run below).
        tasks.clear_attention_dismissal(session, task)
        session.commit()
        session.refresh(run)
        # Each run gets a fresh seq + replay buffer so a stale subscriber's seq
        # watermark can't discard the new run's events (SSE backfill, F4).
        # Phase 4 T4.3 — per-run scoping so a stale tab can't silently drop a
        # new run's low seqs against the prior run's high watermark.
        self.events.reset(task.id, run_id=run.id)
        return run

    def _stream_and_finish(
        self,
        session,
        task: Task,
        repo: Repo,
        run: Run,
        git: GitWorkspace,
        token: str,
        masker,
        handle,
        state: _RunState,
        *,
        publish: bool = True,
        worktree: Path | None = None,
    ) -> None:
        """Stream a handle's events to the SSE bus, then finalize run + task."""
        steps: list[dict[str, object]] = []
        last_event_type: str | None = None
        for event in handle.events():
            state.last_event = time.monotonic()
            if state.reason == "cancelled" and event.type == "error":
                # A kill surfaces as "opencode exited with code -15"; replace it
                # with a clean cancellation marker so the timeline never shows a
                # scary error for an intentional cancel.
                event = AgentEvent(type="message", text="Run cancelled by user.")
            if event.type in ("step", "message", "tool_call", "done", "error"):
                entry = self._step_from_event(event, masker)
                # Phase 4 T4.3 — durable SSE timeline. events.publish persists
                # each event on its own short-lived session (never this worker
                # session — sharing it poisoned workers on lock contention).
                self.events.publish(task.id, entry, run_id=run.id)
                # Persist tool_call too so a reload doesn't lose console lines.
                steps.append(entry)
                if event.type in ("step", "message", "done", "error"):
                    last_event_type = event.type
                # Track the latest message text/phase for progress notifications.
                if event.type in ("message", "tool_call") and entry.get("text"):
                    state.last_step_text = str(entry["text"])
                    phase = entry.get("phase")
                    state.last_phase = str(phase) if phase is not None else None
            if event.type in ("done", "error"):
                break

        with self._running_lock:
            self._running.pop(task.id, None)

        if state.reason == "stalled":
            # The agent process produced nothing for the stall timeout; the
            # stall watchdog killed it. Surface a clear diagnostic instead of a
            # run that looks like it is still "running". The "stall" sentinel is
            # a stable machine-readable marker for _run_stalled (the text is for
            # humans).
            steps.append(
                {
                    "type": "error",
                    "phase": None,
                    "stall": True,
                    "text": (
                        f"Agent produced no output for {state.stall_timeout}s — "
                        "the agent process hung and was terminated. Re-run the task "
                        "or check the agent/opencode configuration."
                    ),
                    "ts": clock.to_iso(now()),
                }
            )

        run.session_id = handle.session_id
        run.finished_at = now()
        run.steps_json = json.dumps(steps[-MAX_STEPS:])
        # T1.6: capture HEAD at run end (with -dirty suffix when the agent left
        # uncommitted material). Best-effort — never block on a transient git issue.
        try:
            end_worktree = worktree or GitWorkspace.worktree_path(self.config.data_dir, task.id)
            run.git_sha_end = self._stamp_git_sha(git, end_worktree)
        except Exception:
            logger.debug("git_sha_end capture failed for task %s", task.id)

        final_status: str = (
            "timed_out"
            if state.reason == "timeout"
            else "cancelled"
            if state.reason == "cancelled"
            else "done"
            if last_event_type == "done"
            else "failed"
        )
        run.status = final_status
        task.status = final_status
        task.updated_at = now()
        if final_status == "done":
            # Deliverable met: the auto-recovery escalation (which is derived
            # from retry_count) resets so the next run starts from the base
            # timeout again.
            task.retry_count = 0

        if (
            publish
            and run.status == "done"
            and self._should_auto_publish(session, task)
        ):
            if self._branch_ahead(task, git):
                try:
                    task.pr_number = self._publish(task, repo, token, git, masker=masker)
                    self._publish_status(session, task, repo, git, token)
                    session.commit()
                except Exception as exc:
                    task.status = "needs_approval"
                    logger.warning("auto-publish failed for task %s: %s", task.id, exc)
                    steps.append(
                        {
                            "type": "error",
                            "phase": None,
                            "text": f"publish failed: {exc}",
                            "ts": clock.to_iso(now()),
                        }
                    )
                    run.steps_json = json.dumps(steps[-MAX_STEPS:])

        # Push a terminal notification (done/failed/timed_out/cancelled or a
        # needs_approval publish failure), with the agent's final message. The
        # run-level masker already includes the env-var values + token, so a
        # value the agent echoed is redacted in the push too.
        #
        # Smart-recovery coalescing: a failure that will be auto-recovered
        # notifies only on the FIRST attempt (so the owner knows), while
        # intermediate attempts stay on the timeline silently. A final give-up
        # (attempt cap / non-retryable) notifies once from _maybe_recover with
        # the give-up reason included — never twice.
        decision, _reason = self._recovery_decision(session, task, run)
        if decision == "recover":
            if (task.retry_count or 0) == 0:
                self._notify_terminal(session, task, repo, state, masker=masker)
        elif decision in ("give_up_cap", "non_retryable"):
            pass  # _maybe_recover sends the single give-up notification.
        else:
            self._notify_terminal(session, task, repo, state, masker=masker)

        # Capture agent-produced (untracked) files from the worktree (PRD F18).
        # Text files are masked at ingest; files containing a known secret value
        # or exceeding the size cap are dropped and surfaced as a note (PRD F17).
        if worktree is None:
            worktree = GitWorkspace.worktree_path(self.config.data_dir, task.id)
        captured, skipped = artifacts.capture_run_artifacts(
            session,
            run,
            worktree,
            self.config.data_dir,
            masker=masker,
            secret_values=secrets.all_token_values(self.config) + [token],
        )
        run.artifacts_json = json.dumps(captured) if captured else None
        if skipped:
            steps.append(
                {
                    "type": "message",
                    "phase": None,
                    "text": (
                        f"Skipped {len(skipped)} artifact(s) — too large or contained "
                        "a secret value: " + ", ".join(skipped[:10])
                    ),
                    "ts": clock.to_iso(now()),
                }
            )
            run.steps_json = json.dumps(steps[-MAX_STEPS:])

        # Run-end diff snapshot (PRD §12 diff viewer). Only for code tasks — the
        # review worktree is detached at the PR head, so diffing it would show the
        # PR's own changes, not Jalebi's work. Best-effort: a failure must never
        # flip a done run to failed.
        if task.type != "pr_review":
            target = task.target_branch or task.source_branch or "main"
            try:
                diff = git.diff_against_target(worktree, target)
                if diff:
                    # Mask BEFORE truncating: a secret straddling the size-cap
                    # boundary must not survive as a partially-visible fragment.
                    masked = masker(diff)
                    raw = masked.encode("utf-8", "ignore")
                    if len(raw) > MAX_DIFF_BYTES:
                        masked = (
                            raw[:MAX_DIFF_BYTES].decode("utf-8", "ignore")
                            + "\n… (diff truncated)"
                        )
                    run.diff_text = masked
            except Exception:
                logger.debug("diff capture failed for task %s", task.id)

        # Uncommitted-work visibility (flaw #1): a done run whose working tree is
        # dirty must not silently look clean. Surface the uncommitted files as a
        # timeline step; if the agent produced NO commits but left work behind,
        # capture the working-tree diff so it is still visible in the diff viewer.
        if run.status == "done" and task.type != "pr_review":
            try:
                dirty = git.working_tree_status(worktree)
            except Exception:
                dirty = []
            if dirty:
                # Porcelain lines are "<XY> <path>": drop the two status codes and
                # any quoting, keep the path.
                names = ", ".join(
                    ln.split(None, 1)[1].strip() for ln in dirty[:10]
                )
                steps.append(
                    {
                        "type": "message",
                        "phase": None,
                        "text": (
                            f"Agent left {len(dirty)} uncommitted file(s) in the "
                            f"worktree: {names}."
                        ),
                        "ts": clock.to_iso(now()),
                    }
                )
                if not run.diff_text:
                    try:
                        wd = git.diff_working_tree(worktree)
                        if wd:
                            masked = masker(wd)
                            raw = masked.encode("utf-8", "ignore")
                            if len(raw) > MAX_DIFF_BYTES:
                                masked = (
                                    raw[:MAX_DIFF_BYTES].decode("utf-8", "ignore")
                                    + "\n… (diff truncated)"
                                )
                            run.diff_text = masked
                    except Exception:
                        logger.debug("working-tree diff capture failed for task %s", task.id)
                run.steps_json = json.dumps(steps[-MAX_STEPS:])

    def _resolve_timeout(self, session, task: Task) -> int:
        """Effective per-run timeout: the task's own, escalated by auto-recovery.

        Auto-recovery escalates the timeout on each attempt (so sub-agent-heavy
        work isn't cut short), but it is **derived** from ``task.retry_count``
        rather than mutating ``task.timeout_minutes`` — a manual rerun after a
        success (or a fresh task) always starts from the base timeout.
        """
        if task.timeout_minutes is not None:
            base = task.timeout_minutes
        else:
            raw = (
                settings.get_setting(session, "default_timeout_minutes")
                or DEFAULT_TIMEOUT_MINUTES
            )
            base = raw if isinstance(raw, int) and raw > 0 else DEFAULT_TIMEOUT_MINUTES
        retries = task.retry_count or 0
        if retries <= 0:
            return base
        policy = settings.get_setting(session, "retry_policy") or {}
        if not isinstance(policy, dict) or not policy.get("auto_retry"):
            return base
        multiplier = policy.get("timeout_multiplier")
        multiplier = multiplier if isinstance(multiplier, (int, float)) and multiplier >= 1 else 2
        cap = policy.get("max_timeout_minutes")
        cap = cap if isinstance(cap, int) and cap >= 1 else MAX_RECOVERY_TIMEOUT_MINUTES
        return min(max(1, int(base * (multiplier**retries))), cap)

    @staticmethod
    def _enabled_cli(session, cli: str) -> str:
        """Fall back to the first enabled backend when ``cli`` was disabled.

        Tasks/screens pinned to a backend that the owner later disabled must
        still run instead of 500ing mid-dispatch; the substitution is logged.
        """
        enabled = settings.get_setting(session, "enabled_backends")
        if not isinstance(enabled, list) or not enabled:
            return cli
        if cli in enabled:
            return cli
        fallback = next((c for c in enabled if isinstance(c, str) and c), "opencode")
        logger.warning("backend %s is disabled; running on %s instead", cli, fallback)
        return fallback

    def _run_task(self, task_id: int) -> None:
        session = Session()
        run: Run | None = None
        # Plain-int snapshot for the except handler (see _run_review): reading
        # run.id on a poisoned/expired session can raise, which would skip the
        # run-failed marking and freeze the run at `running`.
        run_id: int | None = None
        state: _RunState | None = None
        # Initialized here so the exception path can (best-effort) close out the
        # commit status even if the failure happened mid-setup.
        repo: Repo | None = None
        token: str | None = None
        git: GitWorkspace | None = None
        try:
            task = session.get(Task, task_id)
            if task is None:
                return
            if task.status == "cancelled":  # cancelled while queued
                return
            repo = session.get(Repo, task.repo_id)
            if repo is None:
                task.status = "failed"
                session.commit()
                return

            token = secrets.resolve_token(self.config, task.pat_name)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            raw_patterns = settings.get_setting(session, "secret_patterns") or []
            patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
            # The task's selected env vars are secret values too: they are added
            # to the masker so the agent's output never leaks them.
            task_env = envvars.values_for_names(session, repo.id, envvars.task_env_names(task))
            masker = masking.build_masker(
                secrets.all_token_values(self.config) + [token] + list(task_env.values()),
                patterns,
            )
            resolved_cli = str(
                task.cli
                or settings.get_setting(session, "default_backend")
                or "opencode"
            )
            cli, agent_skills = self._agent_run_opts(session, task, resolved_cli)
            cli = self._enabled_cli(session, cli)
            agent = self._catalog_agent(session, task)
            effective_model = task.model or (agent.model if agent is not None else None)
            if not effective_model:
                # The global default model applies only when the resolved backend
                # IS the default backend (a single model can't be valid for every
                # backend); other backends use the CLI's own default.
                default_backend = str(
                    settings.get_setting(session, "default_backend") or "opencode"
                )
                if cli == default_backend:
                    default_model = settings.get_setting(session, "default_model")
                    effective_model = str(default_model) if default_model else None
            effective_prompt = task.prompt
            if agent is not None and agent.custom_instructions:
                effective_prompt = f"{task.prompt}\n\n{agent.custom_instructions}"
            linked = self._task_pr_number(task)
            if linked is not None:
                effective_prompt = (
                    f"{effective_prompt}\n\n"
                    f"(Linked PR: #{linked} in `{repo.full_name}`. Its description and "
                    "review comments are in the worktree's AGENTS.md under \"Linked pull "
                    'request" (or `.jalebi/pr.md`). If you cannot see them, fetch the PR '
                    "and its reviews with the GitHub token in `JALEBI_GITHUB_TOKEN`:\n"
                    f'  curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN" '
                    f"https://api.github.com/repos/{repo.full_name}/pulls/{linked}\n"
                    f'  curl -H "Authorization: Bearer $JALEBI_GITHUB_TOKEN" '
                    f"https://api.github.com/repos/{repo.full_name}/pulls/{linked}/reviews\n)"
                )
            timeout = self._resolve_timeout(session, task)

            # Register cancellation state BEFORE committing "running" so a cancel
            # racing the status flip is never lost.
            state = _RunState(None)
            with self._running_lock:
                self._running[task.id] = state

            # Re-read the status fresh: the snapshot taken at the top may be stale
            # (a cancel committed by the route between then and now). Any cancel
            # landing after this point hits the registered state via queue.cancel.
            session.expire(task)
            if task.status == "cancelled":
                with self._running_lock:
                    self._running.pop(task.id, None)
                return

            if task.type == "pr_review":
                run = self._run_review(session, task, repo, cli, timeout, token, masker, state)
                session.commit()
                self._maybe_recover(session, task, run, repo, state, masker)
                return

            # Phase 4 T4.1 — a task with unmet deps is gated to `blocked`; a
            # re-dispatch (e.g. a followup or web-rerun landing while still
            # blocked) must not start spawn.
            if tasks.has_unmet_deps(session, task.id):
                task.status = "blocked"
                task.updated_at = now()
                session.commit()
                return

            run = self._prepare_run(session, task, cli)
            run.pat_name = task.pat_name
            session.commit()
            run_id = run.id

            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            # A stale jalebi/<taskId> branch from a wiped/restored DB (or a mirror
            # that survived a task delete) must never contaminate a fresh first
            # run. We only reset when a stale branch pre-exists AND no worktree
            # was already created for this task (tests and resumes create the
            # worktree first and must keep their work). Reruns (seq > 1) resume.
            stale_branch = git.branch_exists(task.id, repo.full_name)
            wt_path = GitWorkspace.worktree_path(self.config.data_dir, task.id)
            worktree_existed = (wt_path / ".git").is_file()
            wt = self._ensure_task_worktree(git, task, repo, token)
            if run.seq == 1 and stale_branch and not worktree_existed:
                self._reset_task_branch(git, task, repo, token)
            worktree_bootstrap.bootstrap_worktree(
                wt,
                prompts.build_agent_md(task, repo, agent=agent, cli=cli, session=session),
                cli=cli,
                skills=agent_skills,
            )

            adapter = get_adapter(cli)
            state.handle = adapter.start(
                str(wt),
                effective_prompt,
                model=effective_model,
                env=self._agent_env(session, task, repo, token),
            )
            run.pid = getattr(state.handle.proc, "pid", None)
            run.model = effective_model  # record the effective (possibly agent-pinned) model
            run.git_sha_start = self._stamp_git_sha(git, wt)  # T1.6: pre-spawn HEAD
            session.commit()
            self._start_status(session, task, repo, run, git, token)
            self._start_watchdog(task, state, timeout, stall_timeout=self._stall_timeout(session))

            if state.reason == "cancelled":
                _kill_proc(state.handle.proc)

            self._stream_and_finish(
                session, task, repo, run, git, token, masker, state.handle, state
            )
            session.commit()
            self._complete_status(session, task, repo, run, git, token)
            session.commit()
            self._maybe_recover(session, task, run, repo, state, masker)
        except Exception:
            # Roll back FIRST (see _run_review): the session may be poisoned by
            # a failed flush, and even reading run.id can raise on it. The
            # logger uses the plain-int task_id arg, never the ORM object.
            try:
                session.rollback()
            except Exception:
                pass
            logger.exception("task %s run failed", task_id)
            if state is not None and state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            task = session.get(Task, task_id)
            if task is not None and task.status != "cancelled":
                task.status = "failed"
                task.updated_at = now()
            if run_id is not None:
                # Re-fetch after rollback — the pre-rollback object may be stale.
                run = session.get(Run, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = now()
            session.commit()
            # Close out the commit status so a crashed run doesn't leave a
            # permanently-blocking `pending` on the head SHA (best-effort; the
            # status API is non-fatal). Only when setup got far enough to matter.
            if (
                task is not None
                and run is not None
                and repo is not None
                and token is not None
                and git is not None
            ):
                try:
                    self._complete_status(session, task, repo, run, git, token)
                except Exception:
                    logger.exception("could not set terminal status after run failure")
        finally:
            session.close()
            self.events.close(task_id)

    def _run_review(
        self,
        session,
        task: Task,
        repo: Repo,
        cli: str,
        timeout: int,
        token: str,
        masker,
        state: _RunState,
    ) -> Run:
        """Run a PR-review task in an isolated worktree and post the review (F7).

        The review worktree is checked out at the PR head (detached) and the agent
        is told to review only — it never pushes. On success Jalebi reads the
        agent's ``.jalebi/review.md`` and posts it as a GitHub PR review COMMENT.
        """
        pr_number = self._task_pr_number(task)
        if pr_number is None:
            raise RuntimeError("pr_review task has no PR number")

        run = self._prepare_run(session, task, cli)
        run.pat_name = task.pat_name
        session.commit()
        # Plain-int snapshots for the except handler below: after a session
        # failure (e.g. a locked flush), attribute access on ORM objects can
        # raise while lazy-loading expired state — the handler must never touch
        # the objects themselves (task 63: even the logger call crashed).
        task_id = task.id
        run_id = run.id

        git: GitWorkspace | None = None
        try:
            # If this reviewer task has an assignment, mark it running (with the run).
            reviews.set_assignment_status(session, task.id, "running", run_id=run.id)
            cli, agent_skills = self._agent_run_opts(session, task, cli)
            agent = self._catalog_agent(session, task)
            effective_model = task.model or (agent.model if agent is not None else None)
            effective_prompt = task.prompt
            if agent is not None and agent.custom_instructions:
                effective_prompt = f"{task.prompt}\n\n{agent.custom_instructions}"
            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            wt = git.create_review_worktree(task.id, repo.full_name, pr_number, token)
            worktree_bootstrap.bootstrap_worktree(
                wt,
                prompts.build_agent_md(task, repo, agent=agent, cli=cli, session=session),
                cli=cli,
                skills=agent_skills,
            )

            adapter = get_adapter(cli)
            state.handle = adapter.start(
                str(wt), effective_prompt, model=effective_model, env=_build_agent_env(token)
            )
            run.pid = getattr(state.handle.proc, "pid", None)
            run.model = effective_model  # record the effective (possibly agent-pinned) model
            run.git_sha_start = self._stamp_git_sha(git, wt)  # T1.6: pre-spawn HEAD
            session.commit()
            self._start_status(session, task, repo, run, git, token)
            self._start_watchdog(task, state, timeout, stall_timeout=self._stall_timeout(session))

            if state.reason == "cancelled":
                _kill_proc(state.handle.proc)

            self._stream_and_finish(
                session,
                task,
                repo,
                run,
                git,
                token,
                masker,
                state.handle,
                state,
                publish=False,
                worktree=wt,
            )
            session.commit()

            self._post_review(
                session, task, repo, pr_number, wt, run, token, masker
            )
            # Complete AFTER _post_review so a review that failed to post (or a
            # done run with no review content that was flipped to failed) yields
            # a failure status — a green status for a failed review would defeat
            # the merge gate.
            self._complete_status(session, task, repo, run, git, token)
            session.commit()
        except Exception:
            # Roll back FIRST: the session may be poisoned by a failed flush
            # (PendingRollbackError). Everything below uses only plain-int ids
            # and freshly re-fetched rows — never the possibly-stale objects.
            try:
                session.rollback()
            except Exception:
                pass
            logger.exception("pr_review task %s failed", task_id)
            if state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            run = session.get(Run, run_id)
            task = session.get(Task, task_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = now()
            if task is not None and task.status not in ("cancelled",):
                task.status = "failed"
                task.updated_at = now()
            session.commit()
            # Best-effort: a crashed review must not leave a forever-`pending`
            # status on the PR head (never raise out of the handler).
            if git is not None and task is not None and run is not None:
                try:
                    self._complete_status(session, task, repo, run, git, token)
                except Exception:
                    logger.exception("could not set terminal status after review failure")
            try:
                reviews.set_assignment_status(session, task_id, "failed", run_id=run_id)
            except Exception:
                logger.exception("could not mark review assignment failed")
        return run

    def _post_review(
        self,
        session,
        task: Task,
        repo: Repo,
        pr_number: int,
        worktree: Path,
        run: Run,
        token: str,
        masker,
    ) -> None:
        """Post a ``pr_review`` run's deliverable to GitHub and reconcile the assignment.

        Shared by the initial review run and follow-up resumes, which both finish
        with ``run.status`` decided and the review written to the review
        worktree's ``.jalebi/review.md`` (or the last assistant message):

        - non-``done`` run → assignment resolved to ``failed`` (else it would sit
          ``running`` forever while the task shows the real terminal status);
        - ``done`` run with no review content → the run/task are **flipped to
          ``failed``** with a diagnostic step, because a review task that produced
          no review has no deliverable (previously this silently stayed ``done``);
        - ``done`` run with content → review posted to the PR; assignment →
          ``posted`` (or ``failed`` if the GitHub post errors).
        """
        if run.status != "done":
            reviews.set_assignment_status(session, task.id, "failed", run_id=run.id)
            return

        review_text = self._read_review(worktree)
        if not review_text:
            review_text = self._last_message(session, task.id)
        if not review_text:
            # A done run with no review content has nothing to post. Flip the run
            # AND the task so the timeline/status never claim a review was
            # delivered that does not exist.
            run.status = "failed"
            run.finished_at = now()
            task.status = "failed"
            task.updated_at = now()
            steps = json.loads(run.steps_json or "[]")
            steps.append(
                {
                    "type": "error",
                    "phase": None,
                    "text": (
                        "Run finished without writing a review; nothing to post "
                        "to the PR."
                    ),
                    "ts": clock.to_iso(now()),
                }
            )
            run.steps_json = json.dumps(steps[-MAX_STEPS:])
            session.commit()
            reviews.set_assignment_status(session, task.id, "failed", run_id=run.id)
            return

        review_text = masker(review_text)
        review_body = messaging.wrap_pr_review(review_text)
        try:
            client = GitHubClient(token)
            try:
                client.post_pr_review(repo.full_name, pr_number, review_body)
            finally:
                client.close()
            steps = json.loads(run.steps_json or "[]")
            steps.append(
                {
                    "type": "message",
                    "phase": None,
                    "text": f"Review posted to PR #{pr_number}.",
                    "ts": clock.to_iso(now()),
                }
            )
            run.steps_json = json.dumps(steps[-MAX_STEPS:])
            session.commit()
            reviews.set_assignment_status(session, task.id, "posted", run_id=run.id)
        except Exception as exc:
            logger.warning("posting review for task %s failed: %s", task.id, exc)
            steps = json.loads(run.steps_json or "[]")
            steps.append(
                {
                    "type": "error",
                    "phase": None,
                    "text": f"posting review to PR #{pr_number} failed: {exc}",
                    "ts": clock.to_iso(now()),
                }
            )
            run.steps_json = json.dumps(steps[-MAX_STEPS:])
            # A review that could not be posted has no deliverable on GitHub —
            # flip the run/task so the commit status (set after _post_review)
            # reports failure, not success, and the merge gate stays closed.
            run.status = "failed"
            run.finished_at = now()
            task.status = "failed"
            task.updated_at = now()
            session.commit()
            reviews.set_assignment_status(session, task.id, "failed", run_id=run.id)

    @staticmethod
    def _task_pr_number(task: Task) -> int | None:
        """The PR number a pr_review task targets (first entry of ``prs_json``)."""
        try:
            prs = json.loads(task.prs_json) if task.prs_json else []
        except (ValueError, TypeError):
            prs = []
        return int(prs[0]) if prs else None

    @staticmethod
    def _read_review(worktree: Path) -> str:
        review = worktree / ".jalebi" / "review.md"
        if review.is_file():
            return review.read_text().strip()
        return ""

    @staticmethod
    def _last_message(session, task_id: int) -> str:
        run = tasks.latest_run(session, task_id)
        if run is None or not run.steps_json:
            return ""
        for step in reversed(json.loads(run.steps_json)):
            if step.get("type") == "message" and step.get("text"):
                return step["text"]
        return ""

    def _notify_enabled(self, session, key: str) -> bool:
        """Whether notifications are configured AND this event type is on."""
        if not str(settings.get_setting(session, "ntfy_topic") or "").strip():
            return False
        return bool(settings.get_setting(session, key))

    def _notify(
        self, session, task: Task, repo_full_name: str, state: _RunState, *, masker
    ) -> None:
        """Best-effort push for a terminal/progress notification (never raises)."""
        try:
            notify.send(
                session,
                title=(
                    f"Task #{task.id} {task.status} — {repo_full_name}"
                ),
                message=self._notify_message(task, repo_full_name, state),
                tags=self._notify_tags(task.status),
                click=self._notify_click(task.id),
                actions=[self._notify_open_action(task.id)],
                masker=masker,
            )
        except Exception:
            logger.warning("notification for task %s failed", task.id, exc_info=True)

    def _notify_click(self, task_id: int) -> str:
        """The URL to open when the notification is tapped (the Jalebi task page)."""
        return f"http://127.0.0.1:{self.config.port}/tasks/{task_id}"

    def _notify_open_action(self, task_id: int) -> dict[str, object]:
        """A 'view' action button that opens the Jalebi task page."""
        return {
            "action": "view",
            "label": "Open task",
            "url": f"http://127.0.0.1:{self.config.port}/tasks/{task_id}",
        }

    @staticmethod
    def _notify_message(task: Task, repo_full_name: str, state: _RunState) -> str:
        """Markdown body for the push (rendered by ntfy when `markdown: true`)."""
        status_label = task.status.replace("_", " ")
        lines = [f"**{task.type}** task in `{repo_full_name}` **{status_label}**."]
        if task.pr_number:
            lines.append(f"PR: #{task.pr_number}")
        if state.last_step_text:
            text = state.last_step_text.strip()
            lines.append("")
            lines.append(f"> {text[:300]}")
        return "\n".join(lines)

    @staticmethod
    def _notify_tags(status: str) -> str | None:
        if status == "done":
            return notify.TAGS_OK
        if status == "timed_out":
            return notify.TAGS_CLOCK
        if status in ("failed", "cancelled"):
            return notify.TAGS_FAIL
        if status == "needs_approval":
            return notify.TAGS_APPROVE
        return notify.TAGS_CLOCK

    def _notify_terminal(
        self, session, task: Task, repo: Repo, state: _RunState, *, masker=None
    ) -> None:
        """Send the terminal-status notification when the matching toggle is on.

        ``masker`` is the run-level masker (PATs + env-var values + secret
        patterns); when omitted a best-effort masker is built from settings.
        """
        status = task.status
        key = {
            "done": "notify_on_done",
            "failed": "notify_on_failed",
            "timed_out": "notify_on_failed",
            "cancelled": "notify_on_failed",
            "needs_approval": "notify_on_needs_approval",
        }.get(status)
        if key is None or not self._notify_enabled(session, key):
            return
        if masker is None:
            masker = self._build_masker(session)
        self._notify(session, task, repo.full_name, state, masker=masker)

    def _build_masker(self, session, secret_values: list[str] | None = None):
        """Masker for notification text (PATs + env-var values + secret patterns).

        ``secret_values`` are extra values to redact (e.g. a task's env vars),
        so a value the agent echoed can never reach the push channel.
        """
        config = self.config
        raw_patterns = settings.get_setting(session, "secret_patterns") or []
        patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
        values = list(secrets.all_token_values(config))
        if secret_values:
            values += [v for v in secret_values if v]
        return masking.build_masker(values, patterns)

    def _prior_conversation(self, run) -> str:
        """Extract a compact "prior conversation" from a run's (masked) timeline.

        Used when a follow-up's backend differs from the session's own: the new
        backend can't resume the old session, so its fresh run is seeded with the
        previous run's assistant messages + tool calls as context.
        """
        try:
            steps = json.loads(run.steps_json or "[]")
        except (ValueError, TypeError):
            return ""
        lines: list[str] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            t = s.get("type")
            text = str(s.get("text") or "").strip()
            if t == "message" and text:
                lines.append(text)
            elif t == "tool_call" and text:
                lines.append(f"(tool) {text[:400]}")
        return "\n".join(lines[-40:])

    def _run_followup(
        self,
        task_id: int,
        body: str,
        pat_name: str | None = None,
        model: str | None = None,
        auto: bool = False,
        cli: str | None = None,
    ) -> None:
        cli_param = cli
        """Resume a completed task's session in its own worktree (PRD F11).

        ``auto=True`` marks an auto-recovery resume: no ``followups`` row is
        recorded (it isn't a user follow-up; the recovery step is already on the
        failed run's timeline). ``cli`` is an optional backend override: a
        backend different from the session's own cannot resume it (each CLI owns
        its session format), so it starts a **fresh run seeded with the prior
        conversation** instead.
        """
        session = Session()
        run: Run | None = None
        # Plain-int snapshot for the except handler (see _run_review): reading
        # run.id on a poisoned/expired session can raise, which would skip the
        # run-failed marking and freeze the run at `running`.
        run_id: int | None = None
        state: _RunState | None = None
        repo: Repo | None = None
        token: str | None = None
        git: GitWorkspace | None = None
        try:
            task = session.get(Task, task_id)
            if task is None:
                return
            if task.status == "cancelled":  # cancelled while queued (e.g. a pending recovery)
                return
            repo = session.get(Repo, task.repo_id)
            if repo is None:
                task.status = "failed"
                session.commit()
                return
            prev = tasks.latest_resumable_run(session, task_id)
            if prev is None or not prev.session_id:
                raise RuntimeError(f"task {task_id} has no resumable session")
            prev_session_id = prev.session_id

            token = secrets.resolve_token(self.config, pat_name or task.pat_name)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            raw_patterns = settings.get_setting(session, "secret_patterns") or []
            patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
            task_env = envvars.values_for_names(session, repo.id, envvars.task_env_names(task))
            masker = masking.build_masker(
                secrets.all_token_values(self.config) + [token] + list(task_env.values()),
                patterns,
            )
            # ``cli`` is the follow-up's backend override; a backend different
            # from the session's own (``prev.cli``) cannot resume it — each CLI
            # owns its session format — so it starts a fresh run seeded with the
            # prior conversation instead (see ``fork`` below).
            resolved_cli = str(
                cli
                or task.cli
                or prev.cli
                or settings.get_setting(session, "default_backend")
                or "opencode"
            )
            timeout = self._resolve_timeout(session, task)
            effective_model = model or task.model or prev.model

            cli, agent_skills = self._agent_run_opts(session, task, resolved_cli)
            cli = self._enabled_cli(session, cli)
            agent = self._catalog_agent(session, task)
            if agent is not None and agent.model:
                effective_model = effective_model or agent.model
            if not effective_model:
                default_backend = str(
                    settings.get_setting(session, "default_backend") or "opencode"
                )
                if cli == default_backend:
                    default_model = settings.get_setting(session, "default_model")
                    effective_model = str(default_model) if default_model else None
            if agent is not None and agent.custom_instructions:
                body = f"{body}\n\n{agent.custom_instructions}"

            # A backend different from the session's own (``prev.cli``) can't
            # resume it. The follow-up's explicit override (the ``cli`` parameter
            # captured in ``followup_cli`` before catalog resolution) drives the
            # decision: a user-supplied override always takes precedence — a
            # legacy run with no stored cli and an explicit follow-up cli is
            # treated as a backend change (the prior session id is unusable on
            # the new backend, so the run would otherwise fail mid-stream
            # rather than fork).
            followup_cli = cli_param
            if followup_cli is not None and (prev.cli is None or followup_cli != prev.cli):
                fork = True
            else:
                fork = bool(prev.cli) and cli != prev.cli

            state = _RunState(None)
            with self._running_lock:
                self._running[task.id] = state

            run = self._prepare_run(session, task, cli)
            run.pat_name = pat_name or task.pat_name
            run.model = effective_model
            session.commit()
            run_id = run.id

            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            if task.type == "pr_review":
                # The session to resume lives in the review worktree (detached at
                # the PR head). Resuming it from the task worktree makes opencode's
                # headless --session resume produce an empty stream and hang, so the
                # follow-up must run from the matching review worktree.
                pr_number = self._task_pr_number(task)
                if pr_number is None:
                    raise RuntimeError(f"pr_review task {task.id} has no PR number to resume")
                wt = git.create_review_worktree(task.id, repo.full_name, pr_number, token)
            else:
                wt = self._ensure_task_worktree(git, task, repo, token)
            worktree_bootstrap.bootstrap_worktree(
                wt,
                prompts.build_agent_md(task, repo, agent=agent, cli=cli, session=session),
                cli=cli,
                skills=agent_skills,
            )

            adapter = get_adapter(cli)
            if fork:
                state.handle = adapter.start(
                    str(wt),
                    prompts.build_followup_prompt(
                        task,
                        repo,
                        body,
                        history=self._prior_conversation(prev),
                    ),
                    model=effective_model,
                    env=self._agent_env(session, task, repo, token),
                )
            else:
                state.handle = adapter.resume(
                    str(wt),
                    prev_session_id,
                    prompts.build_followup_prompt(task, repo, body),
                    model=effective_model,
                    env=self._agent_env(session, task, repo, token),
                )
            run.pid = getattr(state.handle.proc, "pid", None)
            run.git_sha_start = self._stamp_git_sha(git, wt)  # T1.6: pre-spawn HEAD
            session.commit()
            self._start_status(session, task, repo, run, git, token)
            self._start_watchdog(task, state, timeout, stall_timeout=self._stall_timeout(session))

            if state.reason == "cancelled":
                _kill_proc(state.handle.proc)

            # Review follow-ups never push code: the resumed agent only edits the
            # review worktree (publish=False), and artifacts are captured from that
            # worktree so a freshly written review.md is not missed.
            self._stream_and_finish(
                session,
                task,
                repo,
                run,
                git,
                token,
                masker,
                state.handle,
                state,
                publish=task.type != "pr_review",
                worktree=wt,
            )
            if task.type == "pr_review":
                pr_number = self._task_pr_number(task)
                if pr_number is not None:
                    self._post_review(
                        session, task, repo, pr_number, wt, run, token, masker
                    )
            # Complete AFTER the review post (see _run_review) so a failed review
            # yields a failure status, not a green one.
            self._complete_status(session, task, repo, run, git, token)
            session.commit()
            if not auto:
                # Auto-recovery resumes are not user follow-ups; only real
                # follow-ups get a row (the recovery step is on the failed run).
                tasks.add_followup(
                    session,
                    task.id,
                    prev.id,
                    body,
                    pat_name=pat_name or task.pat_name,
                    model=effective_model,
                )
            session.commit()
            self._maybe_recover(session, task, run, repo, state, masker)
        except Exception:
            # Roll back FIRST (see _run_review): the session may be poisoned by
            # a failed flush, and even reading run.id can raise on it. The
            # logger uses the plain-int task_id arg, never the ORM object.
            try:
                session.rollback()
            except Exception:
                pass
            logger.exception("follow-up for task %s failed", task_id)
            if state is not None and state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            task = session.get(Task, task_id)
            if task is not None and task.status not in ("cancelled", "done"):
                task.status = "failed"
                task.updated_at = now()
            if run_id is not None:
                run = session.get(Run, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = now()
            session.commit()
            # Best-effort: close out the pending status on a failed follow-up too
            # (never raise out of the handler).
            if (
                task is not None
                and run is not None
                and repo is not None
                and token is not None
                and git is not None
            ):
                try:
                    self._complete_status(session, task, repo, run, git, token)
                except Exception:
                    logger.exception("could not set terminal status after follow-up failure")
        finally:
            session.close()
            self.events.close(task_id)

    def publish_task(
        self,
        task_id: int,
        *,
        mode: str = "new_pr",
        target_branch: str | None = None,
        pr_number: int | None = None,
    ) -> int:
        """Manually publish a task's branch. Returns the PR number (0 if push_branch).

        ``mode`` selects the publish behaviour:

        - ``new_pr`` (default) — push ``jalebi/<id>`` and open a new PR into
          ``task.target_branch`` (or reuse an existing open PR with that head).
        - ``update_pr`` — fast-forward ``jalebi/<id>`` into an existing PR's
          head branch and force-push with ``--force-with-lease``. Requires
          ``pr_number`` (or ``task.prs_json[0]``). The existing PR is reused;
          no new PR is opened and no issue comment is posted.
        - ``push_branch`` — fast-forward ``jalebi/<id>`` into ``target_branch``
          directly and force-push. No PR interaction.

        Validation lives in the route handler (``routes/tasks.py``) so the
        caller surfaces 400s on bad input before any work starts.
        """
        session = Session()
        try:
            task = session.get(Task, task_id)
            if task is None:
                raise KeyError(f"task {task_id} not found")
            repo = session.get(Repo, task.repo_id)
            if repo is None:
                raise KeyError(f"repo for task {task_id} not found")
            # Resolve the task's own account (not the primary) so a manual publish
            # matches the account the run used.
            token = secrets.resolve_token(self.config, task.pat_name)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            raw_patterns = settings.get_setting(session, "secret_patterns") or []
            patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
            masker = masking.build_masker(secrets.all_token_values(self.config) + [token], patterns)
            git = GitWorkspace(self.config)
            # No-op gate: a branch with zero commits ahead of the target has
            # nothing to publish — refuse instead of pushing an empty PR. A
            # missing worktree (never ran / no commits) counts as nothing ahead.
            worktree = GitWorkspace.worktree_path(self.config.data_dir, task.id)
            try:
                ahead = git.commits_ahead(worktree, task.target_branch or "main")
            except Exception:
                ahead = 0
            if ahead <= 0:
                raise PublishError(
                    "the task branch has no commits ahead of the target branch — "
                    "nothing to publish"
                )
            # Mode-specific validation (route catches ValueError → 400).
            if mode not in ("new_pr", "update_pr", "push_branch"):
                raise ValueError(f"unknown publish mode: {mode!r}")
            if mode == "push_branch" and not target_branch:
                raise ValueError("push_branch mode requires `branch`")
            if mode == "update_pr":
                if pr_number is None:
                    try:
                        linked = json.loads(task.prs_json) if task.prs_json else []
                    except (ValueError, TypeError):
                        linked = []
                    if linked:
                        pr_number = int(linked[0])
                    else:
                        raise ValueError("update_pr mode requires `pr_number`")
            pr_number = self._publish(
                task,
                repo,
                token,
                git,
                masker=masker,
                mode=mode,
                target_branch=target_branch,
                pr_number=pr_number,
            )
            # ``push_branch`` returns 0 (no PR interaction); leave the existing
            # ``task.pr_number`` untouched in that case. For ``new_pr`` /
            # ``update_pr`` the return value is the real PR number to record.
            if mode != "push_branch":
                task.pr_number = pr_number
            task.status = "done"
            task.updated_at = now()
            self._publish_status(session, task, repo, git, token)
            # Append a timeline step so the owner sees what mode actually ran.
            run = tasks.latest_run(session, task.id)
            if run is not None:
                steps = json.loads(run.steps_json or "[]")
                if mode == "new_pr":
                    text = f"Published as PR #{pr_number}." if pr_number else "Published."
                elif mode == "update_pr":
                    text = f"Pushed to PR #{pr_number} (existing PR head branch)."
                else:  # push_branch
                    text = f"Pushed to branch `{target_branch}`."
                steps.append(
                    {"type": "message", "phase": None, "text": text, "ts": clock.to_iso(now())}
                )
                run.steps_json = json.dumps(steps[-MAX_STEPS:])
            session.commit()
            self._cascade_unblock(session, task.id)
            return pr_number
        finally:
            session.close()

    # -- helpers -----------------------------------------------------------

    def _start_watchdog(
        self, task: Task, state: _RunState, default_timeout: int, stall_timeout: float | None = None
    ) -> None:
        if stall_timeout is not None:
            state.stall_timeout = stall_timeout
        # ``default_timeout`` is already the effective (possibly escalated)
        # timeout resolved by the caller via _resolve_timeout — use it directly
        # so the watchdog matches the timeout the run is governed by.
        timeout = default_timeout
        deadline = time.monotonic() + timeout * 60
        thread = threading.Thread(
            target=self._watchdog_loop,
            args=(deadline, state),
            daemon=True,
            name=f"watchdog-{task.id}",
        )
        thread.start()
        stall = threading.Thread(
            target=self._stall_watchdog_loop,
            args=(state,),
            daemon=True,
            name=f"stall-{task.id}",
        )
        stall.start()
        progress = threading.Thread(
            target=self._progress_notify_loop,
            args=(task, state),
            daemon=True,
            name=f"progress-{task.id}",
        )
        progress.start()

    @staticmethod
    def _stall_timeout(session) -> float:
        raw = settings.get_setting(session, "stall_timeout_seconds") or STALL_TIMEOUT_SECONDS
        return raw if isinstance(raw, (int, float)) and raw >= 1 else STALL_TIMEOUT_SECONDS

    @staticmethod
    def _run_stalled(run: Run) -> bool:
        """Whether a failed run was killed by the stall watchdog (hung process).

        The stall diagnostic step carries a ``"stall": true`` sentinel written
        by ``_stream_and_finish``; the human text is also checked as a fallback
        for runs recorded before the sentinel existed. A fresh run is safer than
        resuming a wedged session that could re-hang.
        """
        if not run.steps_json:
            return False
        try:
            steps = json.loads(run.steps_json)
        except (ValueError, TypeError):
            return False
        return any(
            isinstance(s, dict)
            and s.get("type") == "error"
            and (
                s.get("stall") is True
                or str(s.get("text") or "").startswith("Agent produced no output")
            )
            for s in steps
        )

    def _cascade_unblock(self, session, task_id: int) -> None:
        """Flip every dependent whose deps are now all satisfied from
        ``blocked``→``queued`` and re-enqueue it (Phase 4 T4.1).

        Called on every terminal completion path. Safe to call when no
        dependents exist (no-op).
        """
        from jalebi.tasks import dependents_for, has_unmet_deps

        deps_unblocked: list[Task] = []
        for dep_id in dependents_for(session, task_id):
            dep = session.get(Task, dep_id)
            if dep is None:
                continue
            if dep.status != "blocked":
                continue
            if has_unmet_deps(session, dep.id):
                continue
            dep.status = "queued"
            dep.updated_at = now()
            deps_unblocked.append(dep)
        if deps_unblocked:
            session.commit()
            # Re-enqueue in original order; existing queue dispatch picks them up.
            for dep in deps_unblocked:
                self.enqueue(dep.id)

    @staticmethod
    def _recovery_policy(session) -> dict:
        """The retry_policy setting as a dict ({} when missing/malformed)."""
        policy = settings.get_setting(session, "retry_policy") or {}
        return policy if isinstance(policy, dict) else {}

    @staticmethod
    def _max_attempts(policy: dict) -> int:
        """Max auto-recovery attempts per task (default 3, minimum 1)."""
        raw = policy.get("max_attempts")
        return raw if isinstance(raw, int) and raw >= 1 else 3

    @staticmethod
    def _non_retryable_match(policy: dict, run: Run) -> str | None:
        """First configured non-retryable pattern matching the run's output.

        Case-insensitive substring match over the run's step texts. A wrong
        model name, revoked token, or missing session fails the same way on
        every attempt — retrying only burns time and spams notifications.
        """
        patterns = policy.get("non_retryable_patterns")
        if not isinstance(patterns, list) or not patterns:
            return None
        try:
            steps = json.loads(run.steps_json or "[]")
        except (ValueError, TypeError):
            return None
        haystack = " ".join(
            str(s.get("text") or "") for s in steps if isinstance(s, dict)
        ).lower()
        if not haystack.strip():
            return None
        for pattern in patterns:
            if isinstance(pattern, str) and pattern.strip() and pattern.lower() in haystack:
                return pattern
        return None

    def _recovery_decision(self, session, task: Task, run: Run) -> tuple[str, str | None]:
        """Decide what happens after a terminal run: ``(decision, reason)``.

        Decisions: ``"terminal_ok"`` (nothing failed), ``"off"`` (auto_retry
        disabled), ``"non_retryable"`` (failure matches a non-retryable
        pattern), ``"give_up_cap"`` (attempt cap reached), ``"recover"``.
        """
        if run.status not in ("failed", "timed_out"):
            return "terminal_ok", None
        policy = self._recovery_policy(session)
        if not policy.get("auto_retry"):
            return "off", None
        matched = self._non_retryable_match(policy, run)
        if matched is not None:
            return "non_retryable", matched
        if (task.retry_count or 0) + 1 > self._max_attempts(policy):
            return "give_up_cap", f"attempt cap ({self._max_attempts(policy)}) reached"
        return "recover", None

    def _maybe_recover(
        self, session, task: Task, run: Run, repo=None, state=None, masker=None
    ) -> None:
        """Auto-recover a failed/timed_out run that never delivered its output.

        Every task type has an expected deliverable; if the agent failed,
        timed out, or stalled before producing it, recover automatically:

        - **timeout / other failure** → resume the last session with a
          "continue" prompt (the provider stopped; the session is usually fine);
        - **stall** (process hung, no output) → fresh re-run instead of resuming
          a session that may be wedged and would just hang again.

        The per-run timeout is escalated on each attempt (``timeout_multiplier``,
        capped at ``max_timeout_minutes``) so sub-agent-heavy runs aren't cut
        short again. Recovery is **bounded by ``max_attempts``** (default 3):
        a deterministically failing task (wrong model, revoked token) stops
        after the cap instead of looping until the owner cancels it. Failures
        matching ``non_retryable_patterns`` fail immediately with no recovery.
        Only the first failure and the final give-up/success notify —
        intermediate attempts are timeline-only. ``task.retry_count`` counts
        recovery attempts for observability.
        """
        # Phase 4 T4.1 — when a task transitions to a satisfied terminal
        # state, unblock any dependents whose deps are now all met.
        if task.status in tasks.DEP_SATISFIED_STATUSES:
            self._cascade_unblock(session, task.id)
        decision, reason = self._recovery_decision(session, task, run)
        if decision in ("terminal_ok", "off"):
            return
        policy = self._recovery_policy(session)

        if decision in ("give_up_cap", "non_retryable"):
            # Final state: stay failed, explain why on the timeline, and send
            # the ONE give-up notification (suppressed in _stream_and_finish).
            if decision == "give_up_cap":
                total_attempts = (task.retry_count or 0) + 1
                text = (
                    f"Auto-recovery gave up after {total_attempts} attempt(s) "
                    f"({reason}). Fix the underlying issue and re-run manually."
                )
            else:
                text = (
                    f"Not auto-recovering: failure matches non-retryable "
                    f"pattern {reason!r}. Fix the underlying issue and re-run "
                    "manually."
                )
            steps = json.loads(run.steps_json or "[]")
            steps.append(
                {
                    "type": "message",
                    "phase": None,
                    "text": text,
                    "ts": clock.to_iso(now()),
                }
            )
            run.steps_json = json.dumps(steps[-MAX_STEPS:])
            session.commit()
            if (
                repo is not None
                and state is not None
                and self._notify_enabled(session, "notify_on_failed")
            ):
                if masker is None:
                    masker = self._build_masker(session)
                # The push quotes the give-up reason, not stale agent output.
                state.last_step_text = text
                self._notify(session, task, repo.full_name, state, masker=masker)
                logger.info("auto-recovery gave up for task %s (%s)", task.id, decision)
            return

        task.retry_count = (task.retry_count or 0) + 1
        # The escalated timeout is derived (see _resolve_timeout), so the base
        # task.timeout_minutes is never permanently mutated.
        escalated = self._resolve_timeout(session, task)
        task.status = "queued"
        task.updated_at = now()

        # Timestamp the failed run's timeline so the owner sees why a new run
        # suddenly appeared.
        steps = json.loads(run.steps_json or "[]")
        steps.append(
            {
                "type": "message",
                "phase": None,
                "text": (
                    f"Auto-recovering — re-running with a longer timeout "
                    f"({escalated}m, attempt {task.retry_count})."
                ),
                "ts": clock.to_iso(now()),
            }
        )
        run.steps_json = json.dumps(steps[-MAX_STEPS:])
        session.commit()

        if self._run_stalled(run):
            self.enqueue(task.id)
            logger.info(
                "auto-recovering task %s (attempt %s): fresh run", task.id, task.retry_count
            )
            return

        prev = tasks.latest_resumable_run(session, task.id)
        if prev is not None and prev.session_id:
            body = str(policy.get("continue_prompt") or "continue")
            self.enqueue_followup(task.id, body, auto=True)
            logger.info("auto-recovering task %s (attempt %s): resume", task.id, task.retry_count)
        else:
            self.enqueue(task.id)
            logger.info(
                "auto-recovering task %s (attempt %s): fresh run (no resumable session)",
                task.id,
                task.retry_count,
            )

    def _status_enabled(self, session, task: Task, repo: Repo) -> bool:
        """Per-repo opt-in (PRD F15): report statuses only for fix/review tasks."""
        return checkruns.status_enabled(session, task, repo)

    def _status_head_sha(
        self, session, task: Task, repo: Repo, git: GitWorkspace, token: str
    ) -> str | None:
        """The head SHA a status should attach to, or None.

        ``pr_review`` → the PR's head SHA (available at run start). ``issue_fix``
        → the pushed ``jalebi/<id>`` head, which exists only after the branch has
        been pushed at least once; None means "not pushed yet" (the status is
        then set at publish time).
        """
        if task.type == "pr_review":
            pr_number = self._task_pr_number(task)
            if pr_number is None:
                return None
            client = GitHubClient(token)
            try:
                pr = client.get_pr(repo.full_name, pr_number)
            except Exception:
                return None
            finally:
                client.close()
            return pr.get("head_sha")
        return git.current_remote_sha(repo.full_name, f"jalebi/{task.id}", token)

    def _set_status(
        self, session, task: Task, repo: Repo, run: Run | None, git: GitWorkspace, token: str,
        state: str, *, force_head: str | None = None,
    ) -> None:
        """Best-effort: set (or replace) the task's commit status at ``state``.

        Non-fatal — a status API failure or DB error never fails the task. Uses
        the current head SHA (or ``force_head`` when given, e.g. right after a
        publish pushed the branch). Posting the same ``(sha, context)`` again
        replaces GitHub's status, which is the "update, don't duplicate" contract.
        """
        try:
            if not self._status_enabled(session, task, repo):
                return
            head_sha = force_head or self._status_head_sha(session, task, repo, git, token)
            if not head_sha:
                return
            context = checkruns.status_context(task)
            client = GitHubClient(token)
            try:
                github_id = client.set_commit_status(
                    repo.full_name, head_sha, state, context,
                    description=f"Jalebi {task.type} for {repo.full_name}",
                )
            finally:
                client.close()
            checkruns.record_status(
                session,
                task_id=task.id,
                run_id=run.id if run is not None else None,
                repo_id=repo.id,
                head_sha=head_sha,
                context=context,
                state=state,
                github_check_id=github_id,
            )
        except Exception:  # noqa: BLE001 - statuses are best-effort, never fail the task
            logger.warning("could not set commit status for task %s", task.id)

    def _start_status(
        self, session, task: Task, repo: Repo, run: Run, git: GitWorkspace, token: str
    ) -> None:
        """Set the status to ``pending`` at run start (best-effort)."""
        self._set_status(session, task, repo, run, git, token, checkruns.STATE_PENDING)

    def _complete_status(
        self, session, task: Task, repo: Repo, run: Run, git: GitWorkspace, token: str
    ) -> None:
        """Set the final status state from ``task.status`` at run terminal (best-effort)."""
        state = checkruns.state_for_status(task.status)
        self._set_status(session, task, repo, run, git, token, state)

    def _publish_status(
        self, session, task: Task, repo: Repo, git: GitWorkspace, token: str
    ) -> None:
        """Set the final status on the just-pushed head after a publish (best-effort)."""
        try:
            if not self._status_enabled(session, task, repo):
                return
            head_sha = git.current_remote_sha(repo.full_name, f"jalebi/{task.id}", token)
            if not head_sha:
                return
            run = tasks.latest_run(session, task.id)
            self._set_status(
                session, task, repo, run, git, token,
                checkruns.state_for_status(task.status),
                force_head=head_sha,
            )
        except Exception:  # noqa: BLE001 - statuses are best-effort
            logger.warning("could not publish status for task %s", task.id)

    def _watchdog_loop(self, deadline: float, state: _RunState) -> None:
        while True:
            if state.handle.proc.poll() is not None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
        state.reason = "timeout"
        _kill_proc(state.handle.proc)

    def _stall_watchdog_loop(self, state: _RunState) -> None:
        """Kill the agent process if it emits no output for ``state.stall_timeout``.

        A hung agent run (e.g. a broken ``--session`` resume, or a tool such as
        a sub-agent that stops reporting to the parent stream) would otherwise
        keep a run "running" forever with an empty timeline until the much
        longer total timeout fires. The stall guard bounds it and the caller
        surfaces a clear "agent produced no output" diagnostic; the auto-recovery
        layer then resumes or re-runs the task.
        """
        while True:
            if state.handle.proc.poll() is not None:
                return
            if time.monotonic() - state.last_event >= state.stall_timeout:
                state.reason = "stalled"
                _kill_proc(state.handle.proc)
                return
            time.sleep(0.5)

    def _progress_notify_loop(self, task: Task, state: _RunState) -> None:
        """Push a periodic 'still running' notification while the agent is alive.

        Fires every ``notify_progress_interval_minutes`` (default 30), starting
        at the first interval mark, with elapsed time + the agent's latest
        message. The toggle and interval are re-read every loop so a live
        settings change applies on the next ping; best-effort (a
        disabled/unconfigured topic is a silent no-op).
        """
        session = Session()
        try:
            repo = session.get(Repo, task.repo_id)
            if repo is None:
                return
            # Include the task's env-var values so a value the agent echoed is
            # redacted in the progress ping too.
            task_env = envvars.values_for_names(session, repo.id, envvars.task_env_names(task))
            masker = self._build_masker(session, secret_values=list(task_env.values()))
            started = time.monotonic()
            while True:
                if state.handle.proc.poll() is not None:
                    return
                raw_interval = settings.get_setting(session, "notify_progress_interval_minutes")
                interval = (
                    raw_interval if isinstance(raw_interval, int) and raw_interval > 0 else 30
                )
                enabled = self._notify_enabled(session, "notify_on_progress")
                elapsed = time.monotonic() - started
                if enabled and elapsed >= interval * 60:
                    started = time.monotonic()  # next ping after another interval
                    elapsed_min = int(elapsed // 60)
                    state.last_step_text = state.last_step_text or "agent is still working"
                    try:
                        notify.send(
                            session,
                            title=f"Task #{task.id} still running — {repo.full_name}",
                            message=(
                                f"**{task.type}** task in `{repo.full_name}` has been "
                                f"running for **{elapsed_min}m**.\n\n"
                                f"> {state.last_step_text[:300]}"
                            ),
                            tags=notify.TAGS_CLOCK,
                            click=self._notify_click(task.id),
                            actions=[self._notify_open_action(task.id)],
                            masker=masker,
                        )
                    except Exception:
                        logger.warning(
                            "progress notification for task %s failed", task.id, exc_info=True
                        )
                time.sleep(min(5.0, max(1.0, interval * 60)))
        finally:
            session.close()

    def _step_from_event(self, event: AgentEvent, masker) -> dict[str, object]:
        text = event.text or (json.dumps(event.data) if event.data else None)
        if text is None:
            return {
                "type": event.type,
                "phase": event.phase,
                "text": None,
                "ts": clock.to_iso(now()),
            }
        # Cap BEFORE masking: the masker applies user-supplied regexes to the full
        # text, and an unbounded input on a pathological pattern could backtrack
        # catastrophically. Truncating first bounds the work.
        text = text[:MAX_STEP_TEXT]
        masked = masker(text)
        return {
            "type": event.type,
            "phase": event.phase,
            "text": masked,
            "ts": clock.to_iso(now()),
        }

    def _branch_ahead(self, task: Task, git: GitWorkspace) -> bool:
        worktree = GitWorkspace.worktree_path(self.config.data_dir, task.id)
        try:
            return git.commits_ahead(worktree, task.target_branch) > 0
        except Exception:
            return False

    @staticmethod
    def _should_auto_publish(session, task: Task) -> bool:
        """Whether a done run should auto-publish for this task.

        Per-task ``publish_mode`` wins: "auto" → publish, "manual" → don't.
        ``None`` (legacy/unset tasks) falls back to the global ``auto_publish``
        setting.
        """
        mode = task.publish_mode
        if mode == "auto":
            return True
        if mode == "manual":
            return False
        return bool(settings.get_setting(session, "auto_publish"))

    @staticmethod
    def _guard_publish_branch(git: GitWorkspace, task_id: int) -> None:
        """Translate a branch-mismatch from git into a ``PublishError``.

        Runs first in ``_publish`` (and via the manual publish path's
        pre-checks) so an agent that checked out/detached onto another ref
        never has its branch pushed.
        """
        try:
            git.assert_publish_branch(task_id)
        except GitWorkspaceError as exc:
            raise PublishError(str(exc)) from None

    def _stamp_git_sha(self, git: GitWorkspace, worktree: Path) -> str | None:
        """HEAD SHA with a ``-dirty`` suffix when the working tree is unclean.

        Best-effort: returns None when the worktree has no commits yet (a
        freshly-created worktree, or one whose ``HEAD`` is unborn) or when
        the underlying git helper isn't reachable. Errors are swallowed so a
        transient git issue never blocks run finalization.
        """
        try:
            sha = git.rev_parse_head(worktree)
        except (GitWorkspaceError, AttributeError):
            return None
        try:
            dirty = bool(git.working_tree_status(worktree))
        except (GitWorkspaceError, AttributeError):
            dirty = False
        return f"{sha}-dirty" if dirty else sha

    def _publish(
        self,
        task: Task,
        repo: Repo,
        token: str,
        git: GitWorkspace,
        masker=None,
        *,
        mode: str = "new_pr",
        target_branch: str | None = None,
        pr_number: int | None = None,
    ) -> int:
        # Refuse to push when the worktree HEAD is not on jalebi/<id> (T1.5).
        self._guard_publish_branch(git, task.id)
        if mode == "update_pr":
            return self._publish_update_pr(
                task, repo, token, git, pr_number=pr_number
            )
        if mode == "push_branch":
            if not target_branch:
                raise PublishError("push_branch mode requires `target_branch`")
            return self._publish_push_branch(task, repo, token, git, target_branch)
        if mode != "new_pr":
            raise PublishError(f"unknown publish mode: {mode!r}")
        return self._publish_new_pr(task, repo, token, git, masker=masker)

    def _publish_new_pr(
        self,
        task: Task,
        repo: Repo,
        token: str,
        git: GitWorkspace,
        masker=None,
    ) -> int:
        # Ensure the worktree exists (it may have been cleaned for old tasks);
        # _ensure_task_worktree reuses the existing jalebi/<taskId> branch if
        # present, and re-bases PR-head tasks on the current PR head.
        self._ensure_task_worktree(git, task, repo, token)
        # Sync the task branch with the PR base BEFORE pushing so the PR is up to
        # date with target's progress made during the run and merges cleanly. A
        # conflict aborts the merge and surfaces the files instead of pushing a
        # conflicted branch (main→dev divergence handling).
        worktree = GitWorkspace.worktree_path(self.config.data_dir, task.id)
        conflicts = git.merge_origin_into(
            worktree, repo.full_name, task.target_branch, token
        )
        if conflicts:
            raise PublishConflict(
                f"PR would conflict with `{task.target_branch}`: "
                + ", ".join(conflicts)
                + " — the merge was aborted. Send a follow-up asking the agent to "
                "merge origin/<target> and resolve the conflicts, then publish again."
            )
        git.push_branch(task.id, repo.full_name, token)
        if task.pr_number:
            # A PR already exists for jalebi/<taskId>; the push just updated it.
            # Only reuse it while it is still open — a closed/merged PR must not
            # swallow a fresh publish (the jalebi/<taskId> branch name can be
            # reused across sessions, leaving a stale closed PR behind).
            client = GitHubClient(token)
            try:
                existing = client.get_pr(repo.full_name, task.pr_number)
            finally:
                client.close()
            if existing.get("state") == "open":
                return task.pr_number
            task.pr_number = None
        client = GitHubClient(token)
        try:
            head = f"jalebi/{task.id}"
            existing = client.find_pr_by_head(repo.full_name, head)
            if existing:
                # An agent-created PR already exists for this head — reuse it
                # instead of opening a duplicate. find_pr_by_head only matches
                # open PRs, but verify defensively before reusing.
                existing_pr = client.get_pr(repo.full_name, existing)
                if existing_pr.get("state") != "open":
                    existing = None
            if existing:
                pr_number = existing
            else:
                title, body = self._pr_title_and_body(task, masker=masker)
                pr_number = client.create_pr(
                    repo.full_name,
                    title=title,
                    body=body,
                    head=head,
                    base=task.target_branch,
                )
                # Only a NEWLY created PR gets the issue link comments — reusing
                # an existing open PR (follow-up pushes) must stay silent.
                self._comment_on_issues(client, repo.full_name, task, pr_number)
            return pr_number
        finally:
            client.close()

    def _publish_update_pr(
        self,
        task: Task,
        repo: Repo,
        token: str,
        git: GitWorkspace,
        *,
        pr_number: int | None,
    ) -> int:
        """Push ``jalebi/<id>`` onto an existing PR's head branch.

        Validates the PR is open, fast-forwards (or merges) the agent's
        commits into the PR's head branch, and force-pushes with
        ``--force-with-lease``. Does NOT open a new PR, does NOT comment on
        linked issues (this is an update, not a new publication).
        """
        if pr_number is None:
            raise PublishError("update_pr mode requires `pr_number`")
        client = GitHubClient(token)
        try:
            pr = client.get_pr(repo.full_name, pr_number)
        finally:
            client.close()
        if pr.get("state") != "open":
            raise PublishError(
                f"PR #{pr_number} is {pr.get('state')}; cannot update a closed PR"
            )
        # get_pr normalizes "head" to the branch-ref string (not the raw GitHub
        # head object), so this is the ref name directly.
        head_branch = pr.get("head")
        if not head_branch:
            raise PublishError(
                f"PR #{pr_number} has no resolvable head branch"
            )
        head_repo = pr.get("head_repo") or repo.full_name
        is_fork = bool(pr.get("is_fork")) or (head_repo != repo.full_name)
        if is_fork:
            return self._publish_update_fork_pr(
                task, repo, token, git, pr_number=pr_number, pr=pr,
                head_branch=str(head_branch), fork_repo=str(head_repo),
            )
        # The worktree is already ensured by ``publish_task`` (commits_ahead
        # gate ran on it), so ``jalebi/<id>`` is on disk — no second
        # create_worktree call needed.
        old_sha = git.current_remote_sha(repo.full_name, head_branch, token)
        conflicts = git.fast_forward_into(
            task.id, repo.full_name, head_branch, token
        )
        if conflicts:
            raise PublishConflict(
                f"pushing to PR #{pr_number} ({head_branch}) would conflict: "
                + ", ".join(conflicts)
                + " — the merge was aborted. Send a follow-up asking the agent to "
                "resolve, then publish again."
            )
        try:
            git.push_existing_branch(repo.full_name, head_branch, token)
        except PushLeaseFailed:
            raise
        # The merge + push updated the local mirror's tracking ref (push
        # does an implicit fetch). Read it without an extra network round
        # trip so we log what actually went up, not what the remote looks
        # like now.
        new_sha = git.local_ref_sha(repo.full_name, head_branch)
        logger.info(
            "task %s updated PR #%s (%s): %s -> %s",
            task.id,
            pr_number,
            head_branch,
            (old_sha or "?")[:10],
            (new_sha or "?")[:10],
        )
        return pr_number

    def _publish_update_fork_pr(
        self,
        task: Task,
        repo: Repo,
        token: str,
        git: GitWorkspace,
        *,
        pr_number: int,
        pr: dict,
        head_branch: str,
        fork_repo: str,
    ) -> int:
        """Push ``jalebi/<id>`` onto a fork PR's head branch (Option 1).

        The merge runs on the mirror's local ``fork-pr-<N>`` branch (based on
        the current ``refs/pull/<N>/head``); the push goes directly to the fork
        URL with ``--force-with-lease`` against the PR's recorded head SHA, so
        a concurrently-moved fork branch is refused (412) instead of clobbered.
        The agent never touches the fork — all of this runs in the queue
        process. When the fork disallows maintainer edits, raises PublishError
        guiding the owner to ``new_pr`` (Option 2 fallback).
        """
        if pr.get("maintainer_can_modify") is False:
            raise PublishError(
                f"PR #{pr_number} is from fork {fork_repo} which does not allow "
                "maintainer edits — push to the fork is not permitted. Publish "
                "with `new_pr` instead to open a separate PR on "
                f"{repo.full_name}."
            )
        old_sha = pr.get("head_sha")
        if not isinstance(old_sha, str) or not old_sha:
            # No recorded head SHA (e.g. mocked PR payload) — push with a plain
            # lease instead of refusing the publish.
            old_sha = None
        conflicts = git.merge_task_into_fork_head(task.id, repo.full_name, pr_number, token)
        if conflicts:
            raise PublishConflict(
                f"pushing to PR #{pr_number} (fork {fork_repo}:{head_branch}) would conflict: "
                + ", ".join(conflicts)
                + " — the merge was aborted. Send a follow-up asking the agent to "
                "resolve, then publish again."
            )
        try:
            git.push_fork_head(
                repo.full_name, pr_number, head_branch, fork_repo,
                str(old_sha) if old_sha else None, token,
            )
        except PushLeaseFailed:
            raise
        new_sha = git.local_ref_sha(repo.full_name, GitWorkspace.fork_branch(pr_number))
        logger.info(
            "task %s updated fork PR #%s (%s:%s): %s -> %s",
            task.id,
            pr_number,
            fork_repo,
            head_branch,
            (str(old_sha) if old_sha else "?")[:10],
            (new_sha or "?")[:10],
        )
        return pr_number

    def _publish_push_branch(
        self,
        task: Task,
        repo: Repo,
        token: str,
        git: GitWorkspace,
        target_branch: str,
    ) -> int:
        """Push ``jalebi/<id>`` onto ``target_branch`` directly (no PR)."""
        # Same as _publish_update_pr: worktree was already ensured.
        old_sha = git.current_remote_sha(repo.full_name, target_branch, token)
        conflicts = git.fast_forward_into(
            task.id, repo.full_name, target_branch, token
        )
        if conflicts:
            raise PublishConflict(
                f"pushing to `{target_branch}` would conflict: "
                + ", ".join(conflicts)
                + " — the merge was aborted. Send a follow-up asking the agent to "
                "resolve, then publish again."
            )
        try:
            git.push_existing_branch(repo.full_name, target_branch, token)
        except PushLeaseFailed:
            raise
        new_sha = git.local_ref_sha(repo.full_name, target_branch)
        logger.info(
            "task %s pushed to branch %s: %s -> %s",
            task.id,
            target_branch,
            (old_sha or "?")[:10],
            (new_sha or "?")[:10],
        )
        return 0

    def _comment_on_issues(
        self, client: GitHubClient, full_name: str, task: Task, pr_number: int
    ) -> None:
        """Comment on each referenced issue that the task opened a PR for it."""
        if task.type != "issue_fix":
            return
        try:
            issue_numbers = json.loads(task.issues_json) if task.issues_json else []
        except (ValueError, TypeError):
            issue_numbers = []
        pr_url = f"https://github.com/{full_name}/pull/{pr_number}"
        body = messaging.issue_comment_for_pr(pr_number, pr_url)
        for number in issue_numbers:
            try:
                client.comment_on_issue(full_name, int(number), body)
            except Exception as exc:
                logger.warning("commenting on issue #%s failed: %s", number, exc)

    def _pr_title_and_body(self, task: Task, masker=None) -> tuple[str, str]:
        """PR title/body from the agent-written ``.jalebi/pr.md`` when present.

        Falls back to the prompt when the agent didn't write one. The Jalebi
        footer (brand line + ``Co-authored-by``) is always appended;
        ``Closes #N`` is added for referenced issues. The agent-written
        content is masked before it can be posted to GitHub.
        """
        title, body = "", ""
        pr_md = GitWorkspace.worktree_path(self.config.data_dir, task.id) / ".jalebi" / "pr.md"
        if pr_md.is_file():
            raw = pr_md.read_text() or ""
            lines = raw.splitlines()
            if lines and lines[0].startswith("# "):
                title = lines[0][2:].strip()[:messaging.PR_TITLE_MAX]
            body = "\n".join(lines[1:]).strip()
        if not title:
            first = task.prompt.strip().splitlines()[0] if task.prompt.strip() else ""
            title = messaging.pr_title(first)
        if not body:
            body = task.prompt
        if masker is not None:
            title = masker(title)
            body = masker(body)

        closes: list[int] = []
        if task.type == "issue_fix":
            try:
                numbers = json.loads(task.issues_json) if task.issues_json else []
            except (ValueError, TypeError):
                numbers = []
            closes = [int(n) for n in numbers if str(n).strip()]
            if closes:
                closes_str = " ".join(f"#{n}" for n in closes)
                if f"Closes {closes_str}" in body:
                    closes = []

        parts = [body, *messaging.pr_footer_parts(closes=closes)]
        return title, "\n\n".join(parts)
