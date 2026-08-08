"""Task queue + worker pool + run lifecycle (PRD F3, F16)."""

import json
import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path

from sqlalchemy import func, select

from jalebi import (
    artifacts,
    catalog,
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
from jalebi.db import CatalogAgent, Repo, Run, Session, Task, utcnow
from jalebi.events import TaskEvents
from jalebi.git_workspace import GitWorkspace, PushLeaseFailed
from jalebi.github import GitHubClient

logger = logging.getLogger(__name__)

MAX_STEPS = 500
MAX_STEP_TEXT = 2000
MAX_DIFF_BYTES = 512 * 1024
KILL_GRACE_SECONDS = 5
MAX_AUTO_RETRIES = 1
DEFAULT_TIMEOUT_MINUTES = 60
STALL_TIMEOUT_SECONDS = 300  # no agent output for this long ⇒ the process is hung

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
    def __init__(self, config: Config):
        self.config = config
        self.events = TaskEvents()
        # Items: ("task", task_id) | ("followup", task_id, body) | None (stop).
        self._queue: queue.Queue[tuple | None] = queue.Queue()
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
    ) -> None:
        self._queue.put(("followup", task_id, body, pat_name, model))

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
                if self._target_concurrency == 0:
                    self._queue.put(item)
                    time.sleep(0.5)
                    continue
                try:
                    if item[0] == "followup":
                        self._run_followup(item[1], item[2], item[3], item[4])
                    else:
                        self._run_task(item[1])
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
                run.finished_at = utcnow()
            task_ids = {r.task_id for r in running_runs}
            running_tasks = list(
                session.execute(select(Task).where(Task.status == "running")).scalars()
            )
            for task in running_tasks:
                task.status = "interrupted"
                task.updated_at = utcnow()
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
        return effective_cli, catalog.skills(agent)

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
            started_at=utcnow(),
            status="running",
        )
        session.add(run)
        task.status = "running"
        task.updated_at = utcnow()
        session.commit()
        session.refresh(run)
        # Each run gets a fresh seq + replay buffer so a stale subscriber's seq
        # watermark can't discard the new run's events (SSE backfill, F4).
        self.events.reset(task.id)
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
                self.events.publish(task.id, entry)
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
            # The agent process produced nothing for STALL_TIMEOUT_SECONDS; the
            # stall watchdog killed it. Surface a clear diagnostic instead of a
            # run that looks like it is still "running".
            steps.append(
                {
                    "type": "error",
                    "phase": None,
                    "text": (
                        f"Agent produced no output for {STALL_TIMEOUT_SECONDS}s — "
                        "the agent process hung and was terminated. Re-run the task "
                        "or check the agent/opencode configuration."
                    ),
                    "ts": utcnow().isoformat(),
                }
            )

        run.session_id = handle.session_id
        run.finished_at = utcnow()
        run.steps_json = json.dumps(steps[-MAX_STEPS:])

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
        task.updated_at = utcnow()

        if (
            publish
            and run.status == "done"
            and self._should_auto_publish(session, task)
        ):
            if self._branch_ahead(task, git):
                try:
                    task.pr_number = self._publish(task, repo, token, git, masker=masker)
                except Exception as exc:
                    task.status = "needs_approval"
                    logger.warning("auto-publish failed for task %s: %s", task.id, exc)
                    steps.append(
                        {
                            "type": "error",
                            "phase": None,
                            "text": f"publish failed: {exc}",
                            "ts": utcnow().isoformat(),
                        }
                    )
                    run.steps_json = json.dumps(steps[-MAX_STEPS:])

        # Push a terminal notification (done/failed/timed_out/cancelled or a
        # needs_approval publish failure), with the agent's final message. The
        # run-level masker already includes the env-var values + token, so a
        # value the agent echoed is redacted in the push too.
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
                    "ts": utcnow().isoformat(),
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
                        "ts": utcnow().isoformat(),
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
        if task.timeout_minutes is not None:
            return task.timeout_minutes
        raw = settings.get_setting(session, "default_timeout_minutes") or DEFAULT_TIMEOUT_MINUTES
        return raw if isinstance(raw, int) and raw > 0 else DEFAULT_TIMEOUT_MINUTES

    def _run_task(self, task_id: int) -> None:
        session = Session()
        run: Run | None = None
        state: _RunState | None = None
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
            cli = str(task.cli or settings.get_setting(session, "agent_cli") or "opencode")
            cli, agent_skills = self._agent_run_opts(session, task, cli)
            agent = self._catalog_agent(session, task)
            effective_model = task.model or (agent.model if agent is not None else None)
            effective_prompt = task.prompt
            if agent is not None and agent.custom_instructions:
                effective_prompt = f"{task.prompt}\n\n{agent.custom_instructions}"
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
                self._maybe_retry(session, task, run)
                return

            run = self._prepare_run(session, task, cli)
            run.pat_name = task.pat_name
            session.commit()

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
            wt = git.create_worktree(
                task.id, repo.full_name, self._worktree_base(task), token
            )
            if run.seq == 1 and stale_branch and not worktree_existed:
                git.reset_branch_to_base(
                    task.id, repo.full_name, self._worktree_base(task)
                )
            worktree_bootstrap.bootstrap_worktree(
                wt,
                prompts.build_agent_md(task, repo, agent=agent),
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
            session.commit()
            self._start_watchdog(task, state, timeout)

            if state.reason == "cancelled":
                _kill_proc(state.handle.proc)

            self._stream_and_finish(
                session, task, repo, run, git, token, masker, state.handle, state
            )
            session.commit()
            self._maybe_retry(session, task, run)
        except Exception:
            logger.exception("task %s run failed", task_id)
            run_id = run.id if run is not None else None
            session.rollback()
            if state is not None and state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            task = session.get(Task, task_id)
            if task is not None and task.status != "cancelled":
                task.status = "failed"
                task.updated_at = utcnow()
            if run_id is not None:
                # Re-fetch after rollback — the pre-rollback object may be stale.
                run = session.get(Run, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = utcnow()
            session.commit()
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
                prompts.build_agent_md(task, repo, agent=agent),
                cli=cli,
                skills=agent_skills,
            )

            adapter = get_adapter(cli)
            state.handle = adapter.start(
                str(wt), effective_prompt, model=effective_model, env=_build_agent_env(token)
            )
            run.pid = getattr(state.handle.proc, "pid", None)
            run.model = effective_model  # record the effective (possibly agent-pinned) model
            session.commit()
            self._start_watchdog(task, state, timeout)

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

            if run.status != "done":
                # Any non-done terminal end (failed/timed_out/cancelled) must
                # resolve the assignment — otherwise it would sit 'running' forever
                # while the task shows the real terminal status.
                reviews.set_assignment_status(session, task.id, "failed", run_id=run.id)
            elif run.status == "done":
                review_text = self._read_review(wt)
                if not review_text:
                    review_text = self._last_message(session, task.id)
                if not review_text:
                    # A done run with no review content has nothing to post.
                    reviews.set_assignment_status(session, task.id, "failed", run_id=run.id)
                else:
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
                                "ts": utcnow().isoformat(),
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
                                "ts": utcnow().isoformat(),
                            }
                        )
                        run.steps_json = json.dumps(steps[-MAX_STEPS:])
                        session.commit()
                        reviews.set_assignment_status(session, task.id, "failed", run_id=run.id)
        except Exception:
            logger.exception("pr_review task %s failed", task.id)
            run_id = run.id
            session.rollback()
            if state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task.id, None)
            run = session.get(Run, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = utcnow()
            if task.status not in ("cancelled",):
                task.status = "failed"
                task.updated_at = utcnow()
            session.commit()
            reviews.set_assignment_status(session, task.id, "failed", run_id=run_id)
        return run

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

    @staticmethod
    def _notify_click(task_id: int) -> str:
        """The URL to open when the notification is tapped (the Jalebi task page)."""
        return f"http://127.0.0.1:3456/tasks/{task_id}"

    @staticmethod
    def _notify_open_action(task_id: int) -> dict[str, object]:
        """A 'view' action button that opens the Jalebi task page."""
        return {
            "action": "view",
            "label": "Open task",
            "url": f"http://127.0.0.1:3456/tasks/{task_id}",
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

    def _run_followup(
        self,
        task_id: int,
        body: str,
        pat_name: str | None = None,
        model: str | None = None,
    ) -> None:
        """Resume a completed task's session in its own worktree (PRD F11)."""
        session = Session()
        run: Run | None = None
        state: _RunState | None = None
        try:
            task = session.get(Task, task_id)
            if task is None:
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
            cli = str(
                task.cli or prev.cli or settings.get_setting(session, "agent_cli") or "opencode"
            )
            timeout = self._resolve_timeout(session, task)
            effective_model = model or task.model or prev.model

            cli, agent_skills = self._agent_run_opts(session, task, cli)
            agent = self._catalog_agent(session, task)
            if agent is not None and agent.model:
                effective_model = effective_model or agent.model
            if agent is not None and agent.custom_instructions:
                body = f"{body}\n\n{agent.custom_instructions}"

            state = _RunState(None)
            with self._running_lock:
                self._running[task.id] = state

            run = self._prepare_run(session, task, cli)
            run.pat_name = pat_name or task.pat_name
            run.model = effective_model
            session.commit()

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
                wt = git.create_worktree(
                    task.id, repo.full_name, self._worktree_base(task), token
                )
            worktree_bootstrap.bootstrap_worktree(
                wt,
                prompts.build_agent_md(task, repo, agent=agent),
                cli=cli,
                skills=agent_skills,
            )

            adapter = get_adapter(cli)
            state.handle = adapter.resume(
                str(wt),
                prev_session_id,
                prompts.build_followup_prompt(task, repo, body),
                model=effective_model,
                env=self._agent_env(session, task, repo, token),
            )
            run.pid = getattr(state.handle.proc, "pid", None)
            session.commit()
            self._start_watchdog(task, state, timeout)

            if state.reason == "cancelled":
                _kill_proc(state.handle.proc)

            self._stream_and_finish(
                session, task, repo, run, git, token, masker, state.handle, state
            )
            tasks.add_followup(
                session,
                task.id,
                prev.id,
                body,
                pat_name=pat_name or task.pat_name,
                model=effective_model,
            )
            session.commit()
        except Exception:
            logger.exception("follow-up for task %s failed", task_id)
            run_id = run.id if run is not None else None
            session.rollback()
            if state is not None and state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            task = session.get(Task, task_id)
            if task is not None and task.status not in ("cancelled", "done"):
                task.status = "failed"
                task.updated_at = utcnow()
            if run_id is not None:
                run = session.get(Run, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = utcnow()
            session.commit()
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
            task.updated_at = utcnow()
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
                    {"type": "message", "phase": None, "text": text, "ts": utcnow().isoformat()}
                )
                run.steps_json = json.dumps(steps[-MAX_STEPS:])
            session.commit()
            return pr_number
        finally:
            session.close()

    # -- helpers -----------------------------------------------------------

    def _start_watchdog(self, task: Task, state: _RunState, default_timeout: int) -> None:
        timeout = task.timeout_minutes if task.timeout_minutes is not None else default_timeout
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

    def _maybe_retry(self, session, task: Task, run: Run) -> None:
        """Auto-retry a failed run once if ``retry_policy.auto_retry`` is set."""
        if run.status != "failed":
            return
        policy = settings.get_setting(session, "retry_policy") or {}
        auto = bool(policy.get("auto_retry")) if isinstance(policy, dict) else False
        if not auto:
            return
        if (task.retry_count or 0) >= MAX_AUTO_RETRIES:
            return
        task.retry_count = (task.retry_count or 0) + 1
        task.status = "queued"
        task.updated_at = utcnow()
        session.commit()
        self.enqueue(task.id)
        logger.info("auto-retrying task %s (attempt %s)", task.id, task.retry_count)

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
        """Kill the agent process if it emits no output for STALL_TIMEOUT_SECONDS.

        A hung ``opencode run`` (e.g. a broken ``--session`` resume) would
        otherwise keep a run "running" forever with an empty timeline until the
        much longer total timeout fires. The stall guard bounds it and the caller
        surfaces a clear "agent produced no output" diagnostic.
        """
        while True:
            if state.handle.proc.poll() is not None:
                return
            if time.monotonic() - state.last_event >= STALL_TIMEOUT_SECONDS:
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
                "ts": utcnow().isoformat(),
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
            "ts": utcnow().isoformat(),
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
        # create_worktree reuses the existing jalebi/<taskId> branch if present.
        git.create_worktree(task.id, repo.full_name, task.target_branch, token)
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
        head_branch = (pr.get("head") or {}).get("ref")
        if not head_branch:
            raise PublishError(
                f"PR #{pr_number} has no resolvable head branch"
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
