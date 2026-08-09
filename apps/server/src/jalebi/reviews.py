"""Reviewer workflow service (PRD F7): assignments, review-task creation, status.

Each reviewer runs as its OWN ``pr_review`` task (decision: reuse the existing
review worktree + posting machinery; assignments are a lightweight registry). A
review assignment links the reviewer's task ↔ catalog agent ↔ PR ↔ repo and
tracks its status, so the PR card and the webhook flow can show which reviewers
have posted.
"""

import json

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jalebi import catalog
from jalebi.db import Repo, ReviewAssignment, Run, Task, utcnow
from jalebi.tasks import create_task

REVIEWER_STATUSES = ("queued", "running", "posted", "failed")

DEFAULT_REVIEW_PROMPT = (
    "Review this pull request thoroughly. Check correctness, security, "
    "performance, edge cases, and code quality. Validate by building and running "
    "relevant tests if feasible. Write your review to `.jalebi/review.md`: start "
    "with an overall verdict, then a prioritized list of findings (severity, "
    "file/line, issue, suggestion). Jalebi posts it as a comment on the PR."
)


class ReviewError(ValueError):
    """Raised for invalid review assignments."""


def assignment_by_task(session: Session, task_id: int) -> ReviewAssignment | None:
    """The assignment for a reviewer task (one per reviewer task)."""
    return session.execute(
        select(ReviewAssignment).where(ReviewAssignment.task_id == task_id)
    ).scalar_one_or_none()


def assignments_for_pr(session: Session, repo_id: int, pr_number: int) -> list[ReviewAssignment]:
    """All reviewer assignments for a PR (for the PR card / webhook flow)."""
    return list(
        session.execute(
            select(ReviewAssignment)
            .where(
                ReviewAssignment.repo_id == repo_id,
                ReviewAssignment.pr_number == pr_number,
            )
            .order_by(ReviewAssignment.id.asc())
        ).scalars()
    )


def assignments_for_task(session: Session, task_id: int) -> list[ReviewAssignment]:
    """Reviewer assignments whose reviewing task is ``task_id`` (a pr_review task)."""
    return list(
        session.execute(
            select(ReviewAssignment)
            .where(ReviewAssignment.task_id == task_id)
            .order_by(ReviewAssignment.id.asc())
        ).scalars()
    )


def set_assignment_status(
    session: Session, task_id: int, status: str, run_id: int | None = None
) -> ReviewAssignment | None:
    """Update a reviewer assignment's status (and optionally its run)."""
    if status not in REVIEWER_STATUSES:
        raise ReviewError(f"invalid assignment status: {status}")
    row = assignment_by_task(session, task_id)
    if row is None:
        return None
    row.status = status
    if run_id is not None:
        row.run_id = run_id
    session.commit()
    session.refresh(row)
    return row


def _validated_reviewers(session: Session, agent_ids: list[str]) -> list:
    """Resolve catalog agents, requiring kind==reviewer and enabled."""
    reviewers: list = []
    for agent_id in agent_ids:
        agent = catalog.agent_by_slug(session, agent_id)
        if agent is None:
            raise ReviewError(f"catalog agent not found: {agent_id}")
        if agent.kind != "reviewer":
            raise ReviewError(f"agent {agent_id} is kind '{agent.kind}', not 'reviewer'")
        if not agent.enabled:
            raise ReviewError(f"catalog agent is disabled: {agent_id}")
        reviewers.append(agent)
    return reviewers


def assign_reviewers(
    session: Session,
    repo: Repo,
    pr_number: int,
    agent_ids: list[str],
    *,
    queue=None,
    masker=None,
) -> list[Task]:
    """Assign reviewers to a PR: create one ``pr_review`` task per reviewer.

    Each reviewer task uses the catalog agent's pins/instructions, targets the
    PR, and never auto-publishes. Returns the combined list of tasks involved
    — both newly created and any existing tasks recovered when the
    ``UNIQUE(repo_id, pr_number, agent_id)`` constraint catches a concurrent
    race. Creates the assignment rows linking task ↔ agent ↔ PR ↔ repo.

    If ``queue`` is provided, only newly created tasks are enqueued onto it —
    recovered existing tasks are already in flight and would double-enqueue
    otherwise. Callers that don't need queue side-effects (tests) can pass
    ``queue=None`` and the returned list is just for inspection.
    """
    if not agent_ids:
        raise ReviewError("no reviewers selected")
    reviewers = _validated_reviewers(session, agent_ids)
    # Deduplicate: assigning the same reviewer twice would create two review
    # tasks for the same agent.
    seen: set[str] = set()
    reviewers = [a for a in reviewers if not (a.id in seen or seen.add(a.id))]

    # Per-agent atomic commits. ``create_task`` commits the Task row itself;
    # the follow-up ReviewAssignment is added and committed as a second
    # transaction. If a UNIQUE race fires on one agent's assignment, only
    # that agent's transaction is rolled back — other agents in the batch
    # are unaffected and the call returns the partial result instead of
    # silently dropping the whole batch (which is what an outer
    # all-or-nothing commit would do).
    new_tasks: list[Task]
    recovered_existing: list[Task]
    try:
        new_tasks, recovered_existing = _create_review_tasks(
            session, repo, pr_number, reviewers, masker
        )
    except ReviewError:
        raise

    if queue is not None:
        for task in new_tasks:
            queue.enqueue(task.id)

    result = new_tasks + recovered_existing
    for task in result:
        session.refresh(task)
    return result


def _cleanup_partial(session: Session, tasks_created: list[Task]) -> None:
    """Remove any already-created reviewer tasks so a failed batch leaves no orphans.

    Used both by ``_create_review_tasks`` (per-iteration orphan cleanup on
    IntegrityError) and by callers that need a full cascade (e.g. the
    ``DELETE /api/tasks/<id>`` path). Delete the assignment row (if any) and
    the Task row, then commit so the cleanup itself survives a rollbacked
    parent transaction.
    """
    if not tasks_created:
        return
    task_ids = [t.id for t in tasks_created]
    session.execute(sa.delete(ReviewAssignment).where(ReviewAssignment.task_id.in_(task_ids)))
    session.execute(sa.delete(Task).where(Task.id.in_(task_ids)))
    session.commit()


def _recover_existing_task_for(
    session: Session, repo_id: int, pr_number: int, agent_id: str
) -> Task | None:
    """Find the existing pr_review Task for a (repo, pr, agent) — the winner
    of a UNIQUE-constraint race. Returns None when no assignment exists (which
    is itself an inconsistency; callers treat as a hard miss)."""
    for existing in assignments_for_pr(session, repo_id, pr_number):
        if existing.agent_id == agent_id:
            return session.get(Task, existing.task_id)
    return None


def _create_review_tasks(
    session: Session,
    repo: Repo,
    pr_number: int,
    reviewers: list,
    masker=None,
) -> tuple[list[Task], list[Task]]:
    """Create one pr_review task + assignment per reviewer (per-agent atomic).

    Returns ``(newly_created_tasks, recovered_existing_tasks)``:
    - ``newly_created_tasks``: tasks this call created (caller enqueues them).
    - ``recovered_existing_tasks``: tasks whose assignment was found in DB
      instead of created (the UNIQUE race-loser). The orphan Task this call
      created for them was cleaned up; no new assignment row is left behind.

    Each agent's pair is its own transaction (Task committed by
    ``create_task``; assignment flushed+committed by the follow-up
    ``session.commit()``). An IntegrityError on one agent's assignment rolls
    back ONLY that transaction — sibling agents in the same batch are
    unaffected, and the call still returns their work.
    """
    new_tasks: list[Task] = []
    recovered_existing: list[Task] = []
    for agent in reviewers:
        # The task prompt is the default review brief; the queue appends the
        # agent's custom_instructions once (same contract as every catalog
        # agent), so instructions never appear twice.
        task = create_task(
            session,
            type_="pr_review",
            repo_id=repo.id,
            prompt=DEFAULT_REVIEW_PROMPT,
            agent_id=agent.id,
            model=None,
            cli=None,
            pat_name=repo.pat_name,
            prs=[pr_number],
            context={"prs": [{"number": pr_number}]},
            publish_mode="manual",
            masker=masker,
        )
        try:
            session.add(
                ReviewAssignment(
                    task_id=task.id,
                    agent_id=agent.id,
                    pr_number=pr_number,
                    repo_id=repo.id,
                    status="queued",
                    created_at=utcnow(),
                )
            )
            session.commit()
            new_tasks.append(task)
        except IntegrityError:
            # UNIQUE race on this agent's assignment — another caller added
            # the same agent for this PR concurrently. ``create_task`` for
            # this iteration committed its own Task row in an earlier
            # transaction; the rollback here only discards the pending
            # ReviewAssignment insert. The Task row is now an orphan (no
            # assignment ever landed) — delete it via the same cascade
            # helper used for task-delete and full-batch cleanup.
            session.rollback()
            _cleanup_partial(session, [task])
            existing_task = _recover_existing_task_for(
                session, repo.id, pr_number, agent.id
            )
            if existing_task is not None:
                recovered_existing.append(existing_task)
        except Exception as exc:
            session.rollback()
            _cleanup_partial(session, [task])
            raise ReviewError(f"failed to create reviewer task: {exc}") from exc
    return new_tasks, recovered_existing


def assignment_to_dict(session: Session, assignment: ReviewAssignment) -> dict[str, object]:
    agent = catalog.agent_by_slug(session, assignment.agent_id)
    return {
        "id": assignment.id,
        "task_id": assignment.task_id,
        "agent_id": assignment.agent_id,
        "agent_name": agent.name if agent is not None else assignment.agent_id,
        "run_id": assignment.run_id,
        "pr_number": assignment.pr_number,
        "repo_id": assignment.repo_id,
        "status": assignment.status,
        "created_at": assignment.created_at.isoformat(),
    }


def latest_review_run(session: Session, task_id: int) -> Run | None:
    from jalebi import tasks as tasks_service

    return tasks_service.latest_run(session, task_id)


def reviews_json_for_task(session: Session, task_id: int) -> str | None:
    """JSON list of assignment dicts for a task's PR (for the task payload).

    The PR may be recorded in ``task.pr_number`` (e.g. after auto-publish) or
    ``task.prs_json`` — both are honored.
    """
    from jalebi.db import Task

    task = session.get(Task, task_id)
    if task is None:
        return None
    pr_number = task.pr_number
    if pr_number is None and task.prs_json:
        try:
            pr_number = int(json.loads(task.prs_json)[0])
        except (ValueError, TypeError, IndexError):
            pr_number = None
    if pr_number is None:
        return None
    assignments = assignments_for_pr(session, task.repo_id, pr_number)
    if not assignments:
        return None
    return json.dumps([assignment_to_dict(session, a) for a in assignments])
