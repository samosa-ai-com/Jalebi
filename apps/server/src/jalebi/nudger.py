"""Phase 4 T4.2 — auto-nudge on CI/review signal.

Default OFF. When ``auto_nudge`` is true and a webhook event for a tracked
PR signals failure (CI red) or changes-requested, the nudger enqueues a
follow-up with the agent so the owner doesn't have to babysit the
PR-feedback loop. Dedup is signature-keyed (one nudge per
``(task_id, kind, ref)``); the cap is MAX_NUDGES_PER_TASK.

The nudger is best-effort — a slow GitHub 5xx or a SQL hiccup must never
cascade into the webhook or poller response paths.
"""

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from jalebi import settings
from jalebi.db import Nudge, Run, Task

if TYPE_CHECKING:
    from jalebi.queue import TaskQueue

logger = logging.getLogger(__name__)

MAX_NUDGES_PER_TASK = 3


def _is_eligible(session: Session, task: Task, run: Run | None) -> bool:
    """A task is nudge-eligible when it has a resumable session and is
    not currently active (queued/running) and not in a never-to-resume
    terminal state."""
    if task.status in {"queued", "running"}:
        return False
    if task.status in {"done", "cancelled", "interrupted"}:
        return False
    if run is None or not run.session_id:
        return False
    return True


def _already_nudged(session: Session, task_id: int, signature: str) -> bool:
    n = (
        session.query(Nudge)
        .filter(Nudge.task_id == task_id, Nudge.signature == signature)
        .count()
    )
    return n > 0


def _at_cap(session: Session, task_id: int) -> bool:
    n = session.query(Nudge).filter(Nudge.task_id == task_id).count()
    return n >= MAX_NUDGES_PER_TASK


def _build_body(kind: str, ref: str) -> str:
    if kind == "ci_failure":
        return (
            f"CI failed on this PR (status context: {ref}). "
            "Investigate the failing check, push a fix, and re-run CI."
        )
    if kind == "review_changes":
        return (
            f"A reviewer requested changes on this PR (review id: {ref}). "
            "Read the review comments, address them, and push a fix."
        )
    return f"An external signal ({kind}: {ref}) needs your attention."


def _nudge(
    session: Session,
    queue: "TaskQueue",
    task: Task,
    run: Run,
    kind: str,
    ref: str,
) -> bool:
    """Record + enqueue a single nudge. Returns True if it landed."""
    signature = f"{task.id}:{kind}:{ref}"
    if _already_nudged(session, task.id, signature):
        return False
    if _at_cap(session, task.id):
        return False
    session.add(
        Nudge(
            task_id=task.id,
            signature=signature,
            kind=kind,
        )
    )
    session.commit()
    body = _build_body(kind, ref)
    try:
        queue.enqueue_followup(
            task.id,
            body,
            pat_name=task.pat_name,
            model=task.model,
            cli=task.cli,
        )
        return True
    except Exception:
        logger.exception("auto-nudge enqueue failed for task %s", task.id)
        return False


def on_webhook(
    session: Session,
    queue: "TaskQueue",
    event: str,
    payload: dict,
) -> None:
    """Hook called from the webhook route after delivery completion.

    Best-effort: only nudges when the setting is on and a matching tracked
    task/PR is found.
    """
    try:
        if not bool(settings.get_setting(session, "auto_nudge") or False):
            return
        if event != "status":
            return
        sha = payload.get("sha")
        branches = payload.get("branches") or []
        if not branches:
            return
        for branch in branches:
            # Real GitHub status payloads carry branches as objects
            # ({"name": "jalebi/12", ...}); accept bare strings too.
            if isinstance(branch, dict):
                branch = branch.get("name")
            if not isinstance(branch, str) or not branch.startswith("jalebi/"):
                continue
            try:
                task_id = int(branch[len("jalebi/"):])
            except ValueError:
                continue
            task = session.get(Task, task_id)
            if task is None:
                continue
            run = (
                session.query(Run)
                .filter(Run.task_id == task_id)
                .order_by(Run.id.desc())
                .first()
            )
            if run is None or not _is_eligible(session, task, run):
                continue
            state = (payload.get("state") or "").lower()
            if state not in {"failure", "error"}:
                continue
            _nudge(session, queue, task, run, kind="ci_failure", ref=f"{sha}:{state}")
    except Exception:
        logger.exception("nudger.on_webhook failed")


def on_poller_fact_change(
    session: Session,
    queue: "TaskQueue",
    task_id: int,
    pr_number: int,
    kind: str,
    ref: str,
) -> None:
    """Hook called from the poller on a fact transition.

    Only the poller can detect steady-state "still failing" — by
    definition a webhook fires only on a state CHANGE. We dedup on
    signature so a stuck failure never produces a second nudge.
    """
    try:
        if not bool(settings.get_setting(session, "auto_nudge") or False):
            return
        task = session.get(Task, task_id)
        if task is None:
            return
        run = (
            session.query(Run)
            .filter(Run.task_id == task_id)
            .order_by(Run.id.desc())
            .first()
        )
        if run is None or not _is_eligible(session, task, run):
            return
        _nudge(session, queue, task, run, kind=kind, ref=ref)
    except Exception:
        logger.exception("nudger.on_poller_fact_change failed for task %s", task_id)
