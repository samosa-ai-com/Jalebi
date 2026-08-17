"""Proactive screening engine (PRD F10).

Screening runs scheduled, **read-only** code audits ("suggestions for
improvements") against a repo's HEAD and returns structured findings. It never
acts — it never opens issues/PRs or starts fix tasks; the owner converts a
finding into a ``screen_finding`` task explicitly.

Design (kept simple per PRD Goal #10):
- One daemon thread (``ScreeningScheduler``) wakes every 60s, matches each
  enabled screen's 5-field cron against the current UTC clock (``cron.py``),
  and runs due screens one at a time.
- **Baseline dedup:** a screen skips a tick when its last terminal run audited
  the same HEAD (the audited HEAD is the dedup watermark). "Run now" forces.
- Each run checks out a **detached, read-only worktree** at the branch HEAD,
  drives the opencode adapter with the screen's system prompt + a structured
  "return a JSON array" instruction, masks all output, and stores the parsed
  findings on the run row.
- Findings notify via ntfy when ``notify_ntfy`` is set.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi import clock, masking, notify, secrets, settings, worktree_bootstrap
from jalebi.adapters import available_adapters, get_adapter
from jalebi.config import Config
from jalebi.cron import CronError, cron_matches_datetime
from jalebi.db import Repo, Screening, ScreeningRun, now
from jalebi.events import TaskEvents
from jalebi.git_workspace import GitWorkspace
from jalebi.queue import _build_agent_env, _kill_proc  # pinned, gh-guarded agent env + kill

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
MAX_OUTPUT_CHARS = 50_000
MAX_FINDINGS = 200
MAX_STEP_TEXT = 2000

FINDING_KEYS = ("severity", "title", "file", "line", "detail", "recommendation")
SEVERITIES = ("critical", "high", "medium", "low")

# How long to wait before re-running a screen whose last run *failed* at the same
# HEAD. ``done`` is the baseline-dedup watermark; a failed run is retried (a
# transient flake must not permanently silence a screen), but never hot-looped.
FAILED_RETRY_COOLDOWN_SECONDS = 3600


class ScreeningError(Exception):
    """A screening operation failed for a domain reason."""


class _WatchState:
    """Mutable watchdog state shared between the run loop and its watchdog thread."""

    __slots__ = ("last_event", "reason", "stop")

    def __init__(self) -> None:
        self.last_event: float = time.monotonic()
        self.reason: str | None = None
        self.stop: bool = False


def parse_findings(text: str) -> list[dict[str, object]]:
    """Extract a JSON array of findings from the agent's final message.

    The screening prompt instructs the agent to end with a single JSON array.
    This is defensive: it strips a fenced block if present, then finds the first
    ``[`` ... matching ``]`` pair. Invalid/non-array output yields ``[]`` (the
    raw output is preserved on the run for inspection).
    """
    if not text:
        return []
    stripped = text.strip()
    # Drop a ```json ... ``` fence around the array if the agent wrapped it.
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        # strip a trailing "json" language tag
        if stripped.startswith("json"):
            stripped = stripped[len("json"):]
        stripped = stripped.strip()
    start = stripped.find("[")
    if start == -1:
        return []
    # Find the matching close bracket, skipping brackets inside JSON string
    # literals (a finding's detail/recommendation may contain `]` or `[`).
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return []
    try:
        raw = json.loads(stripped[start:end])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    findings: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        finding = _normalize_finding(item)
        if finding is not None:
            findings.append(finding)
        if len(findings) >= MAX_FINDINGS:
            break
    return findings


def _mask_findings(findings: list[dict[str, object]], masker) -> list[dict[str, object]]:
    """Redact secret-bearing string fields inside parsed findings (PRD F17)."""
    if not masker:
        return findings
    masked: list[dict[str, object]] = []
    for finding in findings:
        out = dict(finding)
        for key in ("title", "file", "detail", "recommendation"):
            value = out.get(key)
            if isinstance(value, str) and value:
                out[key] = masker(value)
        masked.append(out)
    return masked


def _normalize_finding(item: dict) -> dict[str, object] | None:
    """Coerce one raw finding into the PRD shape; ``None`` if unusable."""
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    severity = item.get("severity")
    if severity not in SEVERITIES:
        severity = "medium"
    finding: dict[str, object] = {
        "severity": severity,
        "title": title.strip(),
        "file": item.get("file") if isinstance(item.get("file"), str) else None,
        "line": item.get("line") if isinstance(item.get("line"), int) else None,
        "detail": item.get("detail") if isinstance(item.get("detail"), str) else None,
        "recommendation": (
            item.get("recommendation") if isinstance(item.get("recommendation"), str) else None
        ),
    }
    return finding


def build_screening_prompt(screen: Screening, repo: Repo, head_sha: str) -> str:
    """The read-only audit prompt: system prompt + structured-findings contract."""
    return (
        f"{screen.system_prompt}\n\n"
        f"Repository: `{repo.full_name}` — auditing HEAD `{head_sha[:12]}`.\n"
        "This is a READ-ONLY audit: do NOT modify any files, do NOT run git "
        "write commands, and do NOT create pull requests or issues.\n\n"
        "Return your findings as a single JSON array as your FINAL message. "
        "Each finding is an object with exactly these keys:\n"
        '- `"severity"`: "critical" | "high" | "medium" | "low"\n'
        '- `"title"`: short human-readable title (string)\n'
        '- `"file"`: file path (string, or null)\n'
        '- `"line"`: line number (integer, or null)\n'
        '- `"detail"`: explanation (string, or null)\n'
        '- `"recommendation"`: suggested fix (string, or null)\n'
        "If you find nothing worth reporting, return an empty array: `[]`. "
        "Output ONLY the JSON array — no prose around it."
    )


def screen_to_dict(screen: Screening) -> dict[str, object]:
    return {
        "id": screen.id,
        "repo_id": screen.repo_id,
        "name": screen.name,
        "system_prompt": screen.system_prompt,
        "cadence_cron": screen.cadence_cron,
        "scope_branch": screen.scope_branch,
        "cli": screen.cli,
        "model": screen.model,
        "enabled": screen.enabled,
        "notify_ntfy": screen.notify_ntfy,
        "created_at": clock.to_iso(screen.created_at),
        "updated_at": clock.to_iso(screen.updated_at),
    }


def run_to_dict(run: ScreeningRun) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    if run.findings_json:
        try:
            parsed = json.loads(run.findings_json)
            if isinstance(parsed, list):
                findings = [f for f in parsed if isinstance(f, dict)]
        except (json.JSONDecodeError, ValueError):
            findings = []
    output: dict[str, object] | None = None
    if run.output_json:
        try:
            parsed = json.loads(run.output_json)
            if isinstance(parsed, dict):
                output = parsed
        except (json.JSONDecodeError, ValueError):
            output = {"message": run.output_json}
    return {
        "id": run.id,
        "screening_id": run.screening_id,
        "head_sha": run.head_sha,
        "status": run.status,
        "started_at": clock.to_iso(run.started_at) if run.started_at else None,
        "finished_at": clock.to_iso(run.finished_at) if run.finished_at else None,
        "findings": findings,
        "output": output,
        "error": run.error,
    }


def list_screens(session: Session) -> list[Screening]:
    return list(session.execute(select(Screening).order_by(Screening.name)).scalars())


def get_screen(session: Session, screen_id: int) -> Screening | None:
    return session.get(Screening, screen_id)


def _normalize_cli(cli: str | None) -> str | None:
    """Normalize a screen's backend pin: empty/None → None (default adapter)."""
    cli = (cli or "").strip() or None
    if cli is not None and cli not in available_adapters():
        raise ScreeningError(f"unsupported agent cli: {cli}")
    return cli


def _codex_sandbox_usable() -> bool:
    """True if a codex screening can run safely on this host.

    Late-bound import: avoids a hard dependency from screening on the codex
    adapter module (a host without codex installed still loads screening). The
    underlying probe is cached per-process inside the adapter. A missing codex
    binary counts as "unusable" so a codex screening is refused with the same
    clear error regardless of the cause.
    """
    try:
        from jalebi.adapters.codex import _sandbox_usable
    except ImportError:
        return False
    try:
        return bool(_sandbox_usable())
    except RuntimeError:
        return False


def _normalize_model(model: str | None) -> str | None:
    """Normalize a screen's model pin: empty/None → None (adapter default)."""
    return (model or "").strip() or None


def create_screen(
    session: Session,
    *,
    repo_id: int,
    name: str,
    system_prompt: str,
    cadence_cron: str,
    scope_branch: str | None = None,
    cli: str | None = None,
    model: str | None = None,
    enabled: bool = True,
    notify_ntfy: bool = True,
) -> Screening:
    if not name or not str(name).strip():
        raise ScreeningError("name is required")
    if not system_prompt or not str(system_prompt).strip():
        raise ScreeningError("system_prompt is required")
    if not cadence_cron or not str(cadence_cron).strip():
        raise ScreeningError("cadence_cron is required")
    try:
        cron_matches_datetime(cadence_cron, now())
    except CronError as exc:
        raise ScreeningError(f"invalid cadence_cron: {exc}") from exc
    repo = session.get(Repo, repo_id)
    if repo is None:
        raise ScreeningError("repo not found")
    if not repo.connected:
        raise ScreeningError("repo is not connected")
    if scope_branch and not str(scope_branch).strip():
        scope_branch = None
    screen = Screening(
        repo_id=repo_id,
        name=str(name).strip(),
        system_prompt=str(system_prompt).strip(),
        cadence_cron=str(cadence_cron).strip(),
        scope_branch=str(scope_branch).strip() if scope_branch else None,
        cli=_normalize_cli(cli),
        model=_normalize_model(model),
        enabled=bool(enabled),
        notify_ntfy=bool(notify_ntfy),
    )
    session.add(screen)
    session.commit()
    session.refresh(screen)
    return screen


def update_screen(
    session: Session,
    screen: Screening,
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    cadence_cron: str | None = None,
    scope_branch: str | None = None,
    cli: str | None = None,
    model: str | None = None,
    enabled: bool | None = None,
    notify_ntfy: bool | None = None,
) -> Screening:
    if name is not None:
        if not str(name).strip():
            raise ScreeningError("name is required")
        screen.name = str(name).strip()
    if system_prompt is not None:
        if not str(system_prompt).strip():
            raise ScreeningError("system_prompt is required")
        screen.system_prompt = str(system_prompt).strip()
    if cadence_cron is not None:
        try:
            cron_matches_datetime(cadence_cron, now())
        except CronError as exc:
            raise ScreeningError(f"invalid cadence_cron: {exc}") from exc
        screen.cadence_cron = str(cadence_cron).strip()
    if scope_branch is not None:
        screen.scope_branch = str(scope_branch).strip() or None
    if cli is not None:
        screen.cli = _normalize_cli(cli)
    if model is not None:
        screen.model = _normalize_model(model)
    if enabled is not None:
        screen.enabled = bool(enabled)
    if notify_ntfy is not None:
        screen.notify_ntfy = bool(notify_ntfy)
    screen.updated_at = now()
    session.commit()
    session.refresh(screen)
    return screen


def delete_screen(session: Session, screen_id: int) -> bool:
    screen = session.get(Screening, screen_id)
    if screen is None:
        return False
    # Deleting cascades runs — refuse while a run is in flight so an active agent
    # + worktree doesn't get orphaned (its final commit would silently hit a
    # cascaded-away row).
    running = (
        session.execute(
            select(ScreeningRun.id)
            .where(
                ScreeningRun.screening_id == screen_id,
                ScreeningRun.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    if running is not None:
        raise ScreeningError("screen is still running; wait for it to finish before deleting")
    session.delete(screen)
    session.commit()
    return True


def list_runs(session: Session, screening_id: int, limit: int = 50) -> list[ScreeningRun]:
    query = (
        select(ScreeningRun)
        .where(ScreeningRun.screening_id == screening_id)
        .order_by(ScreeningRun.id.desc())
        .limit(limit)
    )
    return list(session.execute(query).scalars())


def latest_run(session: Session, screening_id: int) -> ScreeningRun | None:
    return (
        session.execute(
            select(ScreeningRun)
            .where(ScreeningRun.screening_id == screening_id)
            .order_by(ScreeningRun.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


class ScreeningEngine:
    """Executes one screen pass (read-only audit) and records its run."""

    def __init__(self, config: Config):
        self.config = config
        self.events = TaskEvents()
        # Per-screen run locks: a manual "Run now" must never race a scheduler
        # tick on the same screen (which would create two run rows, two agents,
        # two worktrees on the same path namespace).
        self._locks: dict[int, threading.Lock] = {}

    def run_screen(
        self, session: Session, screen: Screening, *, force: bool = False
    ) -> ScreeningRun | None:
        """Run ``screen`` at the current branch HEAD; ``None`` if baseline-skipped.

        ``force=True`` ignores baseline dedup (manual "run now"). A screen that is
        already running (scheduler tick + manual run-now) is refused, never
        double-run. Failures are recorded on the run and re-raised as
        ``ScreeningError``.
        """
        lock = self._locks.setdefault(screen.id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ScreeningError(f"screen '{screen.name}' is already running")
        try:
            return self._run_screen_locked(session, screen, force=force)
        finally:
            lock.release()

    def _run_screen_locked(
        self, session: Session, screen: Screening, *, force: bool = False
    ) -> ScreeningRun | None:
        """The actual run — called with the per-screen lock held."""
        repo = session.get(Repo, screen.repo_id)
        if repo is None:
            raise ScreeningError("screen repo not found")
        if not repo.connected:
            raise ScreeningError(f"repo {repo.full_name} is not connected")

        token = secrets.resolve_token(self.config, repo.pat_name)
        if token is None:
            raise ScreeningError(f"no PAT account bound to {repo.full_name}")

        git = GitWorkspace(self.config)
        git.ensure_mirror(repo.full_name, repo.clone_url, token)
        branch = screen.scope_branch or repo.default_branch
        head_sha = git.current_remote_sha(repo.full_name, branch, token)
        if not head_sha:
            raise ScreeningError(f"branch {branch} has no remote HEAD")

        last = latest_run(session, screen.id)
        if not force and last is not None and last.head_sha == head_sha:
            # ``done`` is the baseline-dedup watermark: a successful audit of the
            # same HEAD needs no re-run. A *failed* run is NOT a valid audit — a
            # transient failure must not permanently silence the screen — so it is
            # retried, but only after a cooldown to avoid hot-looping a
            # persistent failure.
            if last.status == "done":
                return None
            if (
                last.status == "failed"
                and last.finished_at is not None
                and (now() - last.finished_at).total_seconds()
                < FAILED_RETRY_COOLDOWN_SECONDS
            ):
                return None

        run = ScreeningRun(
            screening_id=screen.id,
            head_sha=head_sha,
            status="running",
            started_at=now(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        self.events.reset(run.id)

        raw_patterns = settings.get_setting(session, "secret_patterns") or []
        patterns = (
            [str(p) for p in raw_patterns if isinstance(p, str)]
            if isinstance(raw_patterns, list)
            else []
        )
        masker = masking.build_masker(secrets.all_token_values(self.config), patterns)

        # Backend resolution (parity with the task queue, queue.py `_run_task`):
        # a screen's own cli pin wins; otherwise the global `default_backend`
        # setting; otherwise the opencode fallback. Derived only for an actual
        # run (below the baseline-dedup early return); screens have no run-to-run
        # resume, so it is re-derived every run (no continuity to preserve).
        effective_cli = str(
            screen.cli
            or settings.get_setting(session, "default_backend")
            or "opencode"
        )

        # Screening audits *untrusted* repository code (the highest prompt-injection
        # exposure in the system) — the agent gets no PAT. Codex is the only backend
        # whose only disk confinement is the OS sandbox; when bwrap user namespaces
        # are blocked on the host it falls back to ``danger-full-access`` and the
        # codex guard denies only ``gh``, leaving the agent free to read
        # ``~/.jalebi/secrets.json`` / ``~/.ssh`` / ``~/.aws``. Opencode and claude
        # each have a pattern-gate floor (external_directory: deny / PreToolUse hook)
        # that still confines a *benign-but-confused* agent to the worktree without
        # an OS sandbox — codex does not. Refuse a codex screening here so the
        # highest-risk path keeps at least the pattern-gate floor every other
        # backend has. (`_sandbox_usable` is cached per process; cheap.)
        if effective_cli == "codex" and not _codex_sandbox_usable():
            raise ScreeningError(
                "codex screening requires a working workspace-write sandbox "
                "(bwrap with user namespaces); this host has it disabled. "
                "Pick opencode or claude, or fix bwrap (see server log)."
            )

        wt = None
        try:
            wt_path = GitWorkspace.screening_worktree_path(self.config.data_dir, run.id)
            wt = git.create_detached_worktree(
                repo.full_name, branch, wt_path, token
            )
            worktree_bootstrap.write_guard(wt, effective_cli)
            prompt = build_screening_prompt(screen, repo, head_sha)
            adapter = get_adapter(effective_cli)
            # Screening audits *untrusted* repository code — the highest
            # prompt-injection-exposure agent in the system. It gets NO PAT: the
            # mirror/worktree are prepared by Jalebi above, and a prompt-injected
            # audit agent must never hold a privileged GitHub token (curl against
            # the REST API with it would otherwise be possible).
            effective_model = screen.model or None
            if not effective_model:
                default_backend = str(
                    settings.get_setting(session, "default_backend") or "opencode"
                )
                if effective_cli == default_backend:
                    default_model = settings.get_setting(session, "default_model")
                    effective_model = str(default_model) if default_model else None
            handle = adapter.start(
                str(wt), prompt, model=effective_model, env=_build_agent_env(None)
            )

            # Watchdog: a hung agent must never block the single scheduler thread
            # forever (leaving the worktree behind + a forever-``running`` run).
            # Absolute deadline + no-output stall → kill the process group and
            # mark the run failed; the worktree is still removed in ``finally``.
            raw_timeout = settings.get_setting(session, "default_timeout_minutes")
            timeout_minutes = int(raw_timeout) if isinstance(raw_timeout, int) else 60
            raw_stall = settings.get_setting(session, "stall_timeout_seconds")
            stall_seconds = int(raw_stall) if isinstance(raw_stall, int) else 600
            deadline = time.monotonic() + timeout_minutes * 60
            watch = _WatchState()
            watch_lock = threading.Lock()

            def _watchdog() -> None:
                while not watch.stop:
                    time.sleep(1)
                    if watch.stop:
                        break
                    with watch_lock:
                        elapsed = time.monotonic() - watch.last_event
                    if time.monotonic() >= deadline:
                        watch.reason = (
                            f"screening run exceeded {timeout_minutes} minute timeout"
                        )
                        break
                    if elapsed >= stall_seconds:
                        watch.reason = (
                            f"screening run produced no output for {int(stall_seconds)}s"
                        )
                        break
                if watch.reason is not None:
                    _kill_proc(handle.proc)

            watchdog = threading.Thread(
                target=_watchdog, daemon=True, name="jalebi-screen-watchdog"
            )
            watchdog.start()

            raw_messages: list[str] = []
            raw_chars = 0
            ended_with_error = False
            for event in handle.events():
                with watch_lock:
                    watch.last_event = time.monotonic()
                text = event.text or (json.dumps(event.data) if event.data else None)
                # Keep the raw text unmasked + untruncated for findings parsing;
                # only the persisted/streamed copies are capped + masked.
                entry = {
                    "type": event.type,
                    "phase": event.phase,
                    "text": masker(text[:MAX_STEP_TEXT]) if text else None,
                    "ts": clock.to_iso(now()),
                }
                self.events.publish(run.id, entry)
                if event.type == "message" and text:
                    # Bound the in-memory raw accumulation (the join below slices
                    # anyway) so a chatty agent can't balloon memory.
                    if raw_chars < MAX_OUTPUT_CHARS:
                        raw_messages.append(text)
                        raw_chars += len(text)
                if event.type == "error":
                    ended_with_error = True
                if event.type in ("done", "error"):
                    break
            watch.stop = True
            watchdog.join(timeout=2)

            output_text = "\n".join(raw_messages)[:MAX_OUTPUT_CHARS]
            run.output_json = json.dumps({"message": masker(output_text)})
            if ended_with_error or watch.reason is not None:
                run.status = "failed"
                run.error = str(watch.reason or "agent exited with an error")
            else:
                findings = _mask_findings(parse_findings(output_text), masker)
                run.findings_json = json.dumps(findings)
                run.status = "done"
            run.finished_at = now()
            session.commit()
            if run.status == "done":
                self._notify_findings(session, screen, repo, run, masker)
        except Exception as exc:  # noqa: BLE001 - run failures are recorded, not fatal
            logger.warning("screening run %s failed: %s", run.id, exc)
            try:
                session.rollback()
            except Exception:  # noqa: BLE001 - never mask the original error
                pass
            run.status = "failed"
            run.error = str(exc)[:2000]
            run.finished_at = now()
            try:
                session.commit()
            except Exception:  # noqa: BLE001 - best-effort; the run row is already recorded
                logger.exception("could not persist failed screening run %s", run.id)
            raise ScreeningError(str(exc)) from exc
        finally:
            if wt is not None:
                try:
                    git.remove_detached_worktree(wt, repo.full_name)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.debug("could not remove screening worktree %s", wt)
            self.events.close(run.id)
        return run

    def _notify_findings(
        self,
        session: Session,
        screen: Screening,
        repo: Repo,
        run: ScreeningRun,
        masker,
    ) -> None:
        """Push an ntfy summary when a run produced findings (best-effort)."""
        if not screen.notify_ntfy:
            return
        try:
            findings = parse_findings(run.findings_json or "")
        except Exception:  # noqa: BLE001 - never fail a run on notification
            return
        if not findings:
            return
        n = len(findings)
        lines = "\n".join(
            f"- **{f.get('severity', 'medium')}**: {f.get('title', '?')}"
            for f in findings[:10]
        )
        if n > 10:
            lines += f"\n- … and {n - 10} more"
        notify.send(
            session,
            title=f"Jalebi: {n} finding(s) — {screen.name} ({repo.full_name})",
            message=(
                f"**{screen.name}** audit of `{repo.full_name}` found "
                f"**{n}** finding(s):\n\n{lines}"
            ),
            tags="warning",
            click=f"http://127.0.0.1:{self.config.port}/screenings",
            masker=masker,
        )


class ScreeningScheduler:
    """Daemon thread that runs due screens on their cron cadence."""

    def __init__(self, config: Config):
        self.config = config
        self.engine = ScreeningEngine(config)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="jalebi-screening", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a tick must never kill the loop
                logger.exception("screening tick failed")

    def tick(self, now_dt: datetime | None = None) -> int:
        """Run every enabled, due screen once (baseline-deduped). Returns count run."""
        from jalebi import db  # local import avoids a cycle at module load

        session = db.Session()
        try:
            due = self._due_screens(session, now_dt or now())
            ran = 0
            for screen in due:
                try:
                    if self.engine.run_screen(session, screen) is not None:
                        ran += 1
                except ScreeningError as exc:
                    logger.warning("screen %s (%s): %s", screen.name, screen.id, exc)
            return ran
        finally:
            session.close()

    def _due_screens(self, session: Session, now: datetime) -> list[Screening]:
        due: list[Screening] = []
        for screen in list_screens(session):
            if not screen.enabled:
                continue
            try:
                if cron_matches_datetime(screen.cadence_cron, now):
                    due.append(screen)
            except CronError as exc:
                logger.warning("screen %s (%s) has a bad cron: %s", screen.name, screen.id, exc)
        return due
