"""Task service: create/list/detail over the `tasks` and `runs` tables."""

import json
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import TASK_TYPES, Followup, Repo, Run, Task


def create_task(
    session: Session,
    *,
    type_: str,
    repo_id: int,
    prompt: str,
    source_branch: str = "main",
    target_branch: str = "main",
    model: str | None = None,
    cli: str | None = None,
    timeout_minutes: int = 30,
    masker: Callable[[str], str] | None = None,
) -> Task:
    """Validate and insert a new task, returning it (status = ``queued``)."""
    if type_ not in TASK_TYPES:
        raise ValueError(f"invalid task type: {type_}")
    repo = session.get(Repo, repo_id)
    if repo is None:
        raise ValueError(f"repo {repo_id} not found")
    if not prompt or not prompt.strip():
        raise ValueError("prompt must not be empty")

    masked_prompt = masker(prompt) if masker else prompt
    task = Task(
        type=type_,
        repo_id=repo_id,
        source_branch=source_branch,
        target_branch=target_branch,
        model=model,
        cli=cli,
        prompt=masked_prompt,
        status="queued",
        timeout_minutes=timeout_minutes,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def get_task(session: Session, task_id: int) -> Task | None:
    return session.get(Task, task_id)


def list_tasks(session: Session) -> list[Task]:
    return list(session.execute(select(Task).order_by(Task.id.desc())).scalars())


def latest_run(session: Session, task_id: int) -> Run | None:
    return session.execute(
        select(Run).where(Run.task_id == task_id).order_by(Run.id.desc())
    ).scalars().first()


def latest_resumable_run(session: Session, task_id: int) -> Run | None:
    """Latest run that carries a session id (a follow-up can resume it)."""
    return session.execute(
        select(Run).where(Run.task_id == task_id, Run.session_id.is_not(None)).order_by(
            Run.id.desc()
        )
    ).scalars().first()


def runs_for_task(session: Session, task_id: int) -> list[Run]:
    return list(
        session.execute(
            select(Run).where(Run.task_id == task_id).order_by(Run.id.asc())
        ).scalars()
    )


def add_followup(session: Session, task_id: int, run_id: int, body: str) -> Followup:
    """Persist a follow-up against ``run_id`` (the run it resumes)."""
    row = Followup(task_id=task_id, run_id=run_id, body=body)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_followups(session: Session, task_id: int) -> list[Followup]:
    return list(
        session.execute(
            select(Followup).where(Followup.task_id == task_id).order_by(Followup.id.asc())
        ).scalars()
    )


def run_to_dict(run: Run) -> dict[str, object]:
    return {
        "id": run.id,
        "seq": run.seq,
        "session_id": run.session_id,
        "cli": run.cli,
        "model": run.model,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": json.loads(run.steps_json) if run.steps_json else [],
    }


def task_to_dict(
    task: Task, run: Run | None = None, followups: list[Followup] | None = None
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": task.id,
        "type": task.type,
        "repo_id": task.repo_id,
        "source_branch": task.source_branch,
        "target_branch": task.target_branch,
        "model": task.model,
        "cli": task.cli,
        "prompt": task.prompt,
        "status": task.status,
        "timeout_minutes": task.timeout_minutes,
        "retry_count": task.retry_count,
        "pr_number": task.pr_number,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "run": run_to_dict(run) if run is not None else None,
        "followups": [
            {
                "id": f.id,
                "body": f.body,
                "created_at": f.created_at.isoformat(),
            }
            for f in (followups or [])
        ],
    }
    return data
