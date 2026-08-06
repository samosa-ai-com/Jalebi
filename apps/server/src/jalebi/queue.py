"""Task queue + worker pool + run lifecycle (PRD F3, F16)."""

import json
import logging
import os
import queue
import re
import signal
import threading
import time

from sqlalchemy import func, select

from jalebi import artifacts, masking, secrets, settings, tasks
from jalebi.adapters import get_adapter
from jalebi.adapters.types import AgentEvent
from jalebi.config import Config
from jalebi.db import Repo, Run, Session, Task, utcnow
from jalebi.events import TaskEvents
from jalebi.git_workspace import GitWorkspace
from jalebi.github import GitHubClient

logger = logging.getLogger(__name__)

MAX_STEPS = 500
MAX_STEP_TEXT = 2000
KILL_GRACE_SECONDS = 5
MAX_AUTO_RETRIES = 1
DEFAULT_TIMEOUT_MINUTES = 30


class _RunState:
    """Live run bookkeeping for timeout/cancel coordination."""

    def __init__(self, handle):
        self.handle = handle
        self.reason: str | None = None  # "timeout" | "cancelled"


def _kill_proc(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
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


def _kill_pid(pid: int) -> None:
    """Terminate a process by pid (orphaned agent after a crash), then SIGKILL."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    except OSError:
        return
    for _ in range(KILL_GRACE_SECONDS * 5):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


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

    def enqueue_followup(self, task_id: int, body: str) -> None:
        self._queue.put(("followup", task_id, body))

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
                        self._run_followup(item[1], item[2])
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
            return len(running_runs) + len(queued)
        finally:
            session.close()

    # -- run lifecycle -----------------------------------------------------

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
    ) -> None:
        """Stream a handle's events to the SSE bus, then finalize run + task."""
        steps: list[dict[str, object]] = []
        last_event_type: str | None = None
        for event in handle.events():
            if event.type in ("step", "message", "tool_call", "done", "error"):
                entry = self._step_from_event(event, masker)
                self.events.publish(task.id, entry)
                # Persist tool_call too so a reload doesn't lose console lines.
                steps.append(entry)
                if event.type in ("step", "message", "done", "error"):
                    last_event_type = event.type
            if event.type in ("done", "error"):
                break

        with self._running_lock:
            self._running.pop(task.id, None)

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

        if run.status == "done" and settings.get_setting(session, "auto_publish"):
            if self._branch_ahead(task, git):
                try:
                    task.pr_number = self._publish(task, repo, token, git)
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

        # Capture agent-produced (untracked) files from the worktree (PRD F18).
        worktree = GitWorkspace.worktree_path(self.config.data_dir, task.id)
        captured = artifacts.capture_run_artifacts(
            session, run, worktree, self.config.data_dir
        )
        run.artifacts_json = json.dumps(captured) if captured else None

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

            token = secrets.load_github_token(self.config)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            raw_patterns = settings.get_setting(session, "secret_patterns") or []
            patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
            masker = masking.build_masker(token, patterns)
            cli = str(task.cli or settings.get_setting(session, "agent_cli") or "opencode")
            timeout = self._resolve_timeout(session, task)

            # Register cancellation state BEFORE committing "running" so a cancel
            # racing the status flip is never lost.
            state = _RunState(None)
            with self._running_lock:
                self._running[task.id] = state

            run = self._prepare_run(session, task, cli)

            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            wt = git.create_worktree(task.id, repo.full_name, task.source_branch, token)

            adapter = get_adapter(cli)
            state.handle = adapter.start(str(wt), task.prompt, model=task.model)
            run.pid = getattr(state.handle.proc, "pid", None)
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
            session.rollback()
            if state is not None and state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            task = session.get(Task, task_id)
            if task is not None and task.status != "cancelled":
                task.status = "failed"
                task.updated_at = utcnow()
            if run is not None:
                run.status = "failed"
                run.finished_at = utcnow()
            session.commit()
        finally:
            session.close()
            self.events.close(task_id)

    def _run_followup(self, task_id: int, body: str) -> None:
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

            token = secrets.load_github_token(self.config)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            raw_patterns = settings.get_setting(session, "secret_patterns") or []
            patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
            masker = masking.build_masker(token, patterns)
            cli = str(
                task.cli or prev.cli or settings.get_setting(session, "agent_cli") or "opencode"
            )
            timeout = self._resolve_timeout(session, task)

            state = _RunState(None)
            with self._running_lock:
                self._running[task.id] = state

            run = self._prepare_run(session, task, cli)

            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            wt = git.create_worktree(task.id, repo.full_name, task.source_branch, token)

            adapter = get_adapter(cli)
            state.handle = adapter.resume(str(wt), prev_session_id, body)
            run.pid = getattr(state.handle.proc, "pid", None)
            session.commit()
            self._start_watchdog(task, state, timeout)

            if state.reason == "cancelled":
                _kill_proc(state.handle.proc)

            self._stream_and_finish(
                session, task, repo, run, git, token, masker, state.handle, state
            )
            tasks.add_followup(session, task.id, prev.id, body)
            session.commit()
        except Exception:
            logger.exception("follow-up for task %s failed", task_id)
            session.rollback()
            if state is not None and state.handle is not None:
                _kill_proc(state.handle.proc)
            with self._running_lock:
                self._running.pop(task_id, None)
            task = session.get(Task, task_id)
            if task is not None and task.status not in ("cancelled", "done"):
                task.status = "failed"
                task.updated_at = utcnow()
            if run is not None:
                run.status = "failed"
                run.finished_at = utcnow()
            session.commit()
        finally:
            session.close()
            self.events.close(task_id)

    def publish_task(self, task_id: int) -> int:
        """Manually publish a task's branch (push + open PR). Returns the PR number."""
        session = Session()
        try:
            task = session.get(Task, task_id)
            if task is None:
                raise KeyError(f"task {task_id} not found")
            repo = session.get(Repo, task.repo_id)
            if repo is None:
                raise KeyError(f"repo for task {task_id} not found")
            token = secrets.load_github_token(self.config)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            git = GitWorkspace(self.config)
            pr_number = self._publish(task, repo, token, git)
            task.pr_number = pr_number
            task.status = "done"
            task.updated_at = utcnow()
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

    def _step_from_event(self, event: AgentEvent, masker) -> dict[str, object]:
        text = event.text or (json.dumps(event.data) if event.data else None)
        masked = masker(text) if text else None
        return {
            "type": event.type,
            "phase": event.phase,
            "text": masked[:MAX_STEP_TEXT] if masked else None,
            "ts": utcnow().isoformat(),
        }

    def _branch_ahead(self, task: Task, git: GitWorkspace) -> bool:
        worktree = GitWorkspace.worktree_path(self.config.data_dir, task.id)
        try:
            return git.commits_ahead(worktree, task.target_branch) > 0
        except Exception:
            return False

    def _publish(self, task: Task, repo: Repo, token: str, git: GitWorkspace) -> int:
        git.push_branch(task.id, repo.full_name, token)
        if task.pr_number:
            # A PR already exists for jalebi/<taskId>; the push just updated it.
            return task.pr_number
        client = GitHubClient(token)
        try:
            return client.create_pr(
                repo.full_name,
                title=self._pr_title(task),
                body=self._pr_body(task),
                head=f"jalebi/{task.id}",
                base=task.target_branch,
            )
        finally:
            client.close()

    @staticmethod
    def _pr_title(task: Task) -> str:
        first = task.prompt.strip().splitlines()[0][:80] if task.prompt.strip() else "Jalebi task"
        return f"[Jalebi] {first}"

    @staticmethod
    def _pr_body(task: Task) -> str:
        parts = [task.prompt]
        if task.type == "issue_fix":
            numbers = re.findall(r"#(\d+)", task.prompt)
            if numbers:
                parts.append("Closes " + " ".join(f"#{n}" for n in numbers))
        parts.append(
            f"_Automated by Jalebi — [task {task.id}](http://127.0.0.1:3456/tasks/{task.id})_\n"
            "Co-authored-by: Jalebi <jalebi@localhost>"
        )
        return "\n\n".join(parts)
