"""Task queue + worker pool + run lifecycle (PRD F3, F16)."""

import json
import logging
import queue
import re
import threading
import time

from sqlalchemy import select

from jalebi import masking, secrets, settings
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


class TaskQueue:
    def __init__(self, config: Config):
        self.config = config
        self.events = TaskEvents()
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._running: dict[int, _RunState] = {}
        self._running_lock = threading.Lock()
        self._workers: list[threading.Thread] = []

    # -- pool lifecycle ----------------------------------------------------

    def start(self, concurrency: int) -> None:
        """Spawn ``concurrency`` workers (0 = paused queue)."""
        if concurrency <= 0:
            logger.info("queue paused (concurrency=%s)", concurrency)
            return
        for _ in range(concurrency):
            thread = threading.Thread(
                target=self._worker_loop, daemon=True, name="jalebi-worker"
            )
            thread.start()
            self._workers.append(thread)

    def stop(self) -> None:
        for _ in self._workers:
            self._queue.put(None)
        for thread in self._workers:
            thread.join(timeout=5)

    def enqueue(self, task_id: int) -> None:
        self._queue.put(task_id)

    # -- worker loop -------------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if task_id is None:
                return
            try:
                self._run_task(task_id)
            except Exception:
                logger.exception("task %s crashed in worker", task_id)

    # -- cancellation ------------------------------------------------------

    def cancel(self, task_id: int) -> bool:
        """Kill a running task's process; returns True if it was running."""
        with self._running_lock:
            state = self._running.get(task_id)
        if state is None:
            return False
        state.reason = "cancelled"
        _kill_proc(state.handle.proc)
        return True

    # -- run lifecycle -----------------------------------------------------

    def _run_task(self, task_id: int) -> None:
        session = Session()
        run: Run | None = None
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

            seq = (
                len(session.execute(select(Run).where(Run.task_id == task.id)).scalars().all())
                + 1
            )
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

            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            wt = git.create_worktree(task.id, repo.full_name, task.source_branch, token)

            adapter = get_adapter(cli)
            handle = adapter.start(str(wt), task.prompt, model=task.model)
            state = _RunState(handle)
            with self._running_lock:
                self._running[task.id] = state
            self._start_watchdog(task, state)

            steps: list[dict[str, object]] = []
            last_event_type: str | None = None
            for event in handle.events():
                if event.type in ("step", "message", "tool_call", "done", "error"):
                    entry = self._step_from_event(event, masker)
                    self.events.publish(task.id, entry)
                    if event.type in ("step", "message", "done", "error"):
                        steps.append(entry)
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

            session.commit()
        except Exception:
            logger.exception("task %s run failed", task_id)
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

    def _start_watchdog(self, task: Task, state: _RunState) -> None:
        timeout = task.timeout_minutes if task.timeout_minutes is not None else 30
        deadline = time.monotonic() + timeout * 60
        thread = threading.Thread(
            target=self._watchdog_loop,
            args=(deadline, state),
            daemon=True,
            name=f"watchdog-{task.id}",
        )
        thread.start()

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
        parts.append("_Automated by Jalebi._")
        return "\n\n".join(parts)
