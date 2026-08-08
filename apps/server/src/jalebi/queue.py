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

from jalebi import artifacts, masking, prompts, secrets, settings, tasks, worktree_bootstrap
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
MAX_DIFF_BYTES = 512 * 1024
KILL_GRACE_SECONDS = 5
MAX_AUTO_RETRIES = 1
DEFAULT_TIMEOUT_MINUTES = 30
STALL_TIMEOUT_SECONDS = 300  # no agent output for this long ⇒ the process is hung

GIT_USER_NAME = "Jalebi"
GIT_USER_EMAIL = "jalebi@localhost"


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
    def _agent_token_for(task: Task, token: str | None) -> str | None:
        """The token to expose to the agent subprocess.

        Every task type gets the **selected account's** token as
        ``JALEBI_GITHUB_TOKEN`` — freeform included — so the agent acts as the
        exact account the owner picked for the task. There is no default or
        fallback: if resolution produced ``None`` there is no token to expose.
        """
        return token

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
            masker = masking.build_masker(secrets.all_token_values(self.config) + [token], patterns)
            cli = str(task.cli or settings.get_setting(session, "agent_cli") or "opencode")
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
            wt = git.create_worktree(task.id, repo.full_name, task.source_branch, token)
            if run.seq == 1 and stale_branch and not worktree_existed:
                git.reset_branch_to_base(task.id, repo.full_name, task.source_branch)
            worktree_bootstrap.bootstrap_worktree(wt, prompts.build_agent_md(task, repo))

            adapter = get_adapter(cli)
            state.handle = adapter.start(
                str(wt),
                task.prompt,
                model=task.model,
                env=_build_agent_env(self._agent_token_for(task, token)),
            )
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
            git = GitWorkspace(self.config)
            git.ensure_mirror(repo.full_name, repo.clone_url, token)
            wt = git.create_review_worktree(task.id, repo.full_name, pr_number, token)
            worktree_bootstrap.bootstrap_worktree(wt, prompts.build_agent_md(task, repo))

            adapter = get_adapter(cli)
            state.handle = adapter.start(
                str(wt), task.prompt, model=task.model, env=_build_agent_env(token)
            )
            run.pid = getattr(state.handle.proc, "pid", None)
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

            if run.status == "done":
                review_text = self._read_review(wt)
                if not review_text:
                    review_text = self._last_message(session, task.id)
                if review_text:
                    review_text = masker(review_text)
                    try:
                        client = GitHubClient(token)
                        try:
                            client.post_pr_review(repo.full_name, pr_number, review_text)
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
            masker = masking.build_masker(secrets.all_token_values(self.config) + [token], patterns)
            cli = str(
                task.cli or prev.cli or settings.get_setting(session, "agent_cli") or "opencode"
            )
            timeout = self._resolve_timeout(session, task)
            effective_model = model or task.model or prev.model

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
                wt = git.create_worktree(task.id, repo.full_name, task.source_branch, token)
            worktree_bootstrap.bootstrap_worktree(wt, prompts.build_agent_md(task, repo))

            adapter = get_adapter(cli)
            state.handle = adapter.resume(
                str(wt),
                prev_session_id,
                prompts.build_followup_prompt(task, repo, body),
                model=effective_model,
                env=_build_agent_env(self._agent_token_for(task, token)),
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
            # Resolve the task's own account (not the primary) so a manual publish
            # matches the account the run used.
            token = secrets.resolve_token(self.config, task.pat_name)
            if token is None:
                raise RuntimeError("no GitHub token configured")
            raw_patterns = settings.get_setting(session, "secret_patterns") or []
            patterns = [str(p) for p in raw_patterns] if isinstance(raw_patterns, list) else []
            masker = masking.build_masker(secrets.all_token_values(self.config) + [token], patterns)
            git = GitWorkspace(self.config)
            pr_number = self._publish(task, repo, token, git, masker=masker)
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
        stall = threading.Thread(
            target=self._stall_watchdog_loop,
            args=(state,),
            daemon=True,
            name=f"stall-{task.id}",
        )
        stall.start()

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
    ) -> int:
        # Ensure the worktree exists (it may have been cleaned for old tasks);
        # create_worktree reuses the existing jalebi/<taskId> branch if present.
        git.create_worktree(task.id, repo.full_name, task.target_branch, token)
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
            self._comment_on_issues(client, repo.full_name, task, pr_number)
            return pr_number
        finally:
            client.close()

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
        for number in issue_numbers:
            try:
                client.comment_on_issue(
                    full_name,
                    int(number),
                    f"Jalebi opened a pull request for this issue — "
                    f"[task {task.id}](http://127.0.0.1:3456/tasks/{task.id}), "
                    f"PR #{pr_number}.",
                )
            except Exception as exc:
                logger.warning("commenting on issue #%s failed: %s", number, exc)

    def _pr_title_and_body(self, task: Task, masker=None) -> tuple[str, str]:
        """PR title/body from the agent-written ``.jalebi/pr.md`` when present.

        Falls back to the prompt when the agent didn't write one. The Jalebi
        footer is always appended; ``Closes #N`` is added for referenced issues.
        The agent-written content is masked before it can be posted to GitHub.
        """
        title, body = "", ""
        pr_md = GitWorkspace.worktree_path(self.config.data_dir, task.id) / ".jalebi" / "pr.md"
        if pr_md.is_file():
            raw = pr_md.read_text() or ""
            lines = raw.splitlines()
            if lines and lines[0].startswith("# "):
                title = lines[0][2:].strip()[:80]
            body = "\n".join(lines[1:]).strip()
        if not title:
            first = task.prompt.strip().splitlines()[0][:80] if task.prompt.strip() else ""
            title = f"[Jalebi] {first}".strip() or "Jalebi task"
        if not body:
            body = task.prompt
        if masker is not None:
            title = masker(title)
            body = masker(body)

        parts = [body]
        if task.type == "issue_fix":
            try:
                numbers = json.loads(task.issues_json) if task.issues_json else []
            except (ValueError, TypeError):
                numbers = []
            if numbers:
                closes = " ".join(f"#{int(n)}" for n in numbers)
                if f"Closes {closes}" not in body:
                    parts.append(f"Closes {closes}")
        parts.append(
            f"_Automated by Jalebi — [task {task.id}](http://127.0.0.1:3456/tasks/{task.id})_\n"
            "Co-authored-by: Jalebi <jalebi@localhost>"
        )
        return title, "\n\n".join(parts)
