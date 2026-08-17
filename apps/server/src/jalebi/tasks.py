"""Task service: create/list/detail over the `tasks` and `runs` tables."""

import json
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jalebi import attention, clock
from jalebi.catalog import agent_by_slug
from jalebi.db import TASK_TYPES, Artifact, Followup, Repo, ReviewAssignment, Run, Task

MAX_PROMPT_CHARS = 32_000  # prompts travel via argv; bound them to stay clear of ARG_MAX


def create_task(
    session: Session,
    *,
    type_: str,
    repo_id: int,
    prompt: str,
    source_branch: str = "main",
    target_branch: str = "main",
    agent_id: str | None = None,
    model: str | None = None,
    cli: str | None = None,
    pat_name: str | None = None,
    issues: list[int] | None = None,
    prs: list[int] | None = None,
    context: dict | None = None,
    env_vars: list[str] | None = None,
    timeout_minutes: int = 60,
    publish_mode: str | None = None,
    masker: Callable[[str], str] | None = None,
) -> Task:
    """Validate and insert a new task, returning it (status = ``queued``)."""
    if type_ not in TASK_TYPES:
        raise ValueError(f"invalid task type: {type_}")
    if publish_mode not in (None, "auto", "manual"):
        raise ValueError("publish_mode must be 'auto', 'manual', or None")
    repo = session.get(Repo, repo_id)
    if repo is None:
        raise ValueError(f"repo {repo_id} not found")
    if not repo.connected:
        raise ValueError(f"repo {repo.full_name} is disconnected")
    if not prompt or not prompt.strip():
        raise ValueError("prompt must not be empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"prompt too long ({len(prompt)} chars; max {MAX_PROMPT_CHARS})"
        )
    # The catalog agent is referenced by slug and validated here (FK-less by
    # design — see db.CatalogAgent). A deleted/disabled agent is refused at
    # creation; the queue re-validates at run time.
    if agent_id is not None:
        agent = agent_by_slug(session, agent_id)
        if agent is None:
            raise ValueError(f"catalog agent not found: {agent_id}")
        if not agent.enabled:
            raise ValueError(f"catalog agent is disabled: {agent_id}")
    # Every task runs as an explicit account: the selected one, else the account
    # bound to the repo at connect time. No default/fallback exists — a task
    # without an account is a config error.
    effective_pat = pat_name or repo.pat_name
    if not effective_pat:
        raise ValueError(f"repo {repo.full_name} has no bound account; select one")

    masked_prompt = masker(prompt) if masker else prompt
    task = Task(
        type=type_,
        repo_id=repo_id,
        source_branch=source_branch,
        target_branch=target_branch,
        agent_id=agent_id,
        model=model,
        cli=cli,
        pat_name=effective_pat,
        issues_json=json.dumps(issues) if issues else None,
        prs_json=json.dumps(prs) if prs else None,
        context_json=json.dumps(context) if context else None,
        env_vars_json=json.dumps(env_vars) if env_vars else None,
        prompt=masked_prompt,
        status="queued",
        timeout_minutes=timeout_minutes,
        publish_mode=publish_mode,
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


def add_followup(
    session: Session,
    task_id: int,
    run_id: int,
    body: str,
    pat_name: str | None = None,
    model: str | None = None,
) -> Followup:
    """Persist a follow-up against ``run_id`` (the run it resumes)."""
    row = Followup(task_id=task_id, run_id=run_id, body=body, pat_name=pat_name, model=model)
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


def list_artifacts(session: Session, run_id: int) -> list[Artifact]:
    return list(
        session.execute(
            select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.id.asc())
        ).scalars()
    )


def run_to_dict(run: Run, artifacts: list[Artifact] | None = None) -> dict[str, object]:
    steps = json.loads(run.steps_json) if run.steps_json else []
    waiting_input = (
        run.status in attention.WAITING_INPUT_STATUSES
        and attention.is_waiting_message(attention.last_message_text(steps))
    )
    return {
        "id": run.id,
        "seq": run.seq,
        "session_id": run.session_id,
        "cli": run.cli,
        "model": run.model,
        "pat_name": run.pat_name,
        "status": run.status,
        "started_at": clock.to_iso(run.started_at) if run.started_at else None,
        "finished_at": clock.to_iso(run.finished_at) if run.finished_at else None,
        "has_diff": bool(run.diff_text),
        "waiting_input": waiting_input,
        "steps": steps,
        "artifacts": [
            {
                "id": a.id,
                "path": a.path,
                "size": a.size,
                "created_at": clock.to_iso(a.created_at),
            }
            for a in (artifacts or [])
        ],
    }


def task_to_dict(
    task: Task,
    run: Run | None = None,
    followups: list[Followup] | None = None,
    artifacts: list[Artifact] | None = None,
    repo_full_name: str | None = None,
    reviewers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    run_dict = run_to_dict(run, artifacts=artifacts) if run is not None else None
    data: dict[str, object] = {
        "id": task.id,
        "type": task.type,
        "repo_id": task.repo_id,
        "repo_full_name": repo_full_name,
        "source_branch": task.source_branch,
        "target_branch": task.target_branch,
        "agent_id": task.agent_id,
        "model": task.model,
        "cli": task.cli,
        "pat_name": task.pat_name,
        "prompt": task.prompt,
        "status": task.status,
        "waiting_input": bool(run_dict and run_dict["waiting_input"]),
        "timeout_minutes": task.timeout_minutes,
        "retry_count": task.retry_count,
        "pr_number": task.pr_number,
        "publish_mode": task.publish_mode,
        "check_run_id": task.check_run_id,
        "issues": json.loads(task.issues_json) if task.issues_json else [],
        "prs": json.loads(task.prs_json) if task.prs_json else [],
        "env_vars": json.loads(task.env_vars_json) if task.env_vars_json else [],
        "created_at": clock.to_iso(task.created_at),
        "updated_at": clock.to_iso(task.updated_at),
        "run": run_dict,
        "followups": [
            {
                "id": f.id,
                "body": f.body,
                "pat_name": f.pat_name,
                "model": f.model,
                "created_at": clock.to_iso(f.created_at),
            }
            for f in (followups or [])
        ],
        "reviewers": reviewers or [],
    }
    return data


def delete_tasks_cascade(session: Session, task_ids: list[int]) -> list[int]:
    """Delete tasks and everything tied to them (orphan-safe cascade).

    Order is FK-dependency order — children before parents:

    Followup → ReviewAssignment → Artifact → Run → Task

    Followups are deleted first because they reference both ``tasks.id`` and
    ``runs.id`` (nullable FK). Returns the ids of the deleted runs so callers
    can reuse them for disk cleanup (artifact store + worktree paths).

    Does NOT commit: transaction control stays with the caller so it can
    wrap the cascade in its own transaction boundaries. ``_cleanup_partial``
    is the one caller that commits itself (to survive a rollbacked parent
    transaction on IntegrityError).
    """
    task_ids = list(task_ids)
    if not task_ids:
        return []
    run_ids = list(
        session.execute(select(Run.id).where(Run.task_id.in_(task_ids))).scalars()
    )
    session.execute(delete(Followup).where(Followup.task_id.in_(task_ids)))
    session.execute(delete(ReviewAssignment).where(ReviewAssignment.task_id.in_(task_ids)))
    if run_ids:
        session.execute(delete(Artifact).where(Artifact.run_id.in_(run_ids)))
        session.execute(delete(Run).where(Run.id.in_(run_ids)))
    session.execute(delete(Task).where(Task.id.in_(task_ids)))
    return run_ids
