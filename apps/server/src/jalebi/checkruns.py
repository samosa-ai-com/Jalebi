"""Commit-status registry + lifecycle for merge gating (PRD F15).

**Why commit statuses, not check runs:** GitHub's check-runs API is GitHub-App
only — PATs (classic and fine-grained) cannot write it. Jalebi is PAT-driven
(PRD §F1), so merge gating uses **commit statuses** (`POST /repos/{o}/{r}/statuses/{sha}`)
instead, which PATs CAN write ("Commit statuses read/write" is a required scope)
and which branch protection can require — the same merge-gating outcome.

A commit status is keyed by ``(sha, context)`` on GitHub: posting the same
context again *replaces* the previous status for that SHA. That is exactly the
"update the existing check, don't duplicate" contract M2 needs, so every call is
a single ``POST`` — there is no create-then-patch phase.

The ``check_runs`` table mirrors each status Jalebi set (task, run, head SHA,
context/name, state, conclusion-like state, GitHub status id) so the UI and
``tasks.check_run_id`` can point at the latest. ``tasks.check_run_id`` stays
FK-less by design (SQLite batch-rebuild hazard).

Non-fatal contract: every GitHub call here is best-effort — a status API failure
(GitHub down, missing scope, repo not reachable) is logged and never fails the
underlying task. Helpers swallow their own exceptions (including the DB record
step) so the queue never has to special-case them.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi import clock
from jalebi.db import CheckRun, Repo, Task

logger = logging.getLogger(__name__)

# GitHub commit-status states (the "conclusion" of a status).
STATE_PENDING = "pending"
STATE_SUCCESS = "success"
STATE_FAILURE = "failure"
STATE_ERROR = "error"

# Task types that report a status (per-repo opt-in via ``check_runs_enabled``).
STATUS_TASK_TYPES = ("issue_fix", "pr_review")

# Human-readable prefix shown in the GitHub "checks" section / PR status line.
CONTEXT_PREFIX = "Jalebi"


def status_enabled(session: Session, task: Task, repo: Repo) -> bool:
    """True if this task should report a status (type + per-repo opt-in)."""
    return bool(repo.check_runs_enabled) and task.type in STATUS_TASK_TYPES


def state_for_status(status: str) -> str:
    """Map a task terminal status to a GitHub commit-status state (PRD F15).

    ``done → success``; ``failed``/``timed_out → failure``; ``cancelled``/
    ``interrupted → error``; everything else (``needs_approval``, still-running,
    unknown) stays ``pending`` — GitHub treats pending as "not yet green", so it
    still blocks a merge under branch protection.
    """
    if status == "done":
        return STATE_SUCCESS
    if status in ("failed", "timed_out"):
        return STATE_FAILURE
    if status in ("cancelled", "interrupted"):
        return STATE_ERROR
    return STATE_PENDING


def status_context(task: Task) -> str:
    """The commit-status context (label). Deterministic per task type so the
    start/finish/terminal calls target the same ``(sha, context)`` key."""
    kind = "review" if task.type == "pr_review" else "fix"
    return f"{CONTEXT_PREFIX} / {kind}"


def latest_for_task(session: Session, task_id: int) -> CheckRun | None:
    return (
        session.execute(
            select(CheckRun)
            .where(CheckRun.task_id == task_id)
            .order_by(CheckRun.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def rows_for_head(session: Session, repo_id: int, head_sha: str, name: str) -> list[CheckRun]:
    """Every status row Jalebi has written for ``(repo, sha, context)``.

    Distinct from :func:`row_for_head`, which is scoped to one task: multiple
    tasks (e.g. several reviewers) can target the same head + context.
    """
    return list(
        session.execute(
            select(CheckRun).where(
                CheckRun.repo_id == repo_id,
                CheckRun.head_sha == head_sha,
                CheckRun.name == name,
            )
        )
        .scalars()
        .all()
    )


def _row_state(row: CheckRun) -> str:
    """A row's GitHub state: its conclusion when completed, else pending."""
    if row.status == "completed" and row.conclusion:
        return row.conclusion
    return STATE_PENDING


def aggregate_state(
    session: Session,
    repo_id: int,
    head_sha: str,
    name: str,
    *,
    task_id: int | None = None,
    state: str | None = None,
) -> str:
    """Worst-of state across all tasks sharing ``(repo, sha, context)``.

    A single ``Jalebi / review`` context can be shared by several tasks on one
    PR (parallel reviewers). Posting each task's own state would let a later
    success silently overwrite an earlier failure, so the posted state is the
    aggregate: any ``failure``/``error`` wins, else any non-``success`` keeps it
    ``pending``, else ``success``. With one task the result is that task's own
    state — identical to the pre-aggregation behaviour.

    ``task_id``/``state`` override that task's stored row (the queue calls this
    *before* recording the new state), so an in-flight task is counted with the
    state about to be posted rather than its previous one.
    """
    by_task: dict[int, str] = {row.task_id: _row_state(row) for row in rows_for_head(
        session, repo_id, head_sha, name
    )}
    if task_id is not None and state is not None:
        by_task[task_id] = state
    if not by_task:
        return STATE_PENDING
    values = set(by_task.values())
    if STATE_FAILURE in values:
        return STATE_FAILURE
    if STATE_ERROR in values:
        return STATE_ERROR
    if values == {STATE_SUCCESS}:
        return STATE_SUCCESS
    return STATE_PENDING

def row_for_head(session: Session, task_id: int, head_sha: str, context: str) -> CheckRun | None:
    """The existing status row for a task at a given head (registry key)."""
    return (
        session.execute(
            select(CheckRun)
            .where(
                CheckRun.task_id == task_id,
                CheckRun.head_sha == head_sha,
                CheckRun.name == context,
            )
            .order_by(CheckRun.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def record_status(
    session: Session,
    *,
    task_id: int,
    run_id: int | None,
    repo_id: int,
    head_sha: str,
    context: str,
    state: str,
    github_check_id: int | None,
) -> CheckRun:
    """Insert (or update the matching row for) a status and refresh the task pointer."""
    row = row_for_head(session, task_id, head_sha, context)
    if row is None:
        row = CheckRun(
            task_id=task_id,
            run_id=run_id,
            repo_id=repo_id,
            head_sha=head_sha,
            name=context,
            status="completed" if state != STATE_PENDING else "in_progress",
            conclusion=state if state != STATE_PENDING else None,
            github_check_id=github_check_id,
        )
        session.add(row)
    else:
        row.run_id = run_id or row.run_id
        row.status = "completed" if state != STATE_PENDING else "in_progress"
        row.conclusion = state if state != STATE_PENDING else None
        if github_check_id is not None:
            row.github_check_id = github_check_id
    session.commit()
    session.refresh(row)
    # Point the task at the latest status row (FK-less by design).
    task = session.get(Task, task_id)
    if task is not None:
        task.check_run_id = row.id
        session.commit()
    return row


def check_run_to_dict(check: CheckRun) -> dict[str, object]:
    return {
        "id": check.id,
        "task_id": check.task_id,
        "run_id": check.run_id,
        "repo_id": check.repo_id,
        "head_sha": check.head_sha,
        "name": check.name,
        "status": check.status,
        "conclusion": check.conclusion,
        "github_check_id": check.github_check_id,
        "created_at": clock.to_iso(check.created_at) if check.created_at else None,
    }
