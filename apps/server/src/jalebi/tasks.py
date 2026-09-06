"""Task service: create/list/detail over the `tasks` and `runs` tables."""

import json
import re
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from jalebi import attention, clock
from jalebi.catalog import agent_by_slug
from jalebi.db import (
    TASK_TYPES,
    Artifact,
    Followup,
    Repo,
    ReviewAssignment,
    Run,
    Task,
    TaskDependency,
    now,
)

MAX_PROMPT_CHARS = 32_000  # prompts travel via argv; bound them to stay clear of ARG_MAX

# A freeform task can be based on a PR head (same-repo or fork) instead of an
# origin branch: ``source_branch == "pr/<N>/head"`` means "start the worktree at
# the current head of PR #N". The branch never exists on ``origin`` for fork
# PRs, so the queue fetches ``refs/pull/<N>/head`` instead (see git_workspace).
PR_HEAD_SOURCE_RE = re.compile(r"^pr/(\d+)/head$")


def pr_head_source_number(source_branch: str | None) -> int | None:
    """PR number when ``source_branch`` is a ``pr/<N>/head`` sentinel, else None."""
    if not source_branch:
        return None
    m = PR_HEAD_SOURCE_RE.match(source_branch.strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def is_pr_head_source(source_branch: str | None) -> bool:
    """True when the task's worktree base is a PR head, not an origin branch."""
    return pr_head_source_number(source_branch) is not None


def effective_diff_base(task) -> str:
    """Branch for diff/conflict checks (``origin/<base>`` must exist).

    PR-head tasks are based on a PR head commit, but every read-only check runs
    against ``origin/<target>`` (the PR base) so fork sentinels never reach git.
    """
    if is_pr_head_source(task.source_branch):
        return task.target_branch or "main"
    if task.type == "issue_fix":
        return task.target_branch or task.source_branch or "main"
    return task.source_branch or "main"


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
        "git_sha_start": run.git_sha_start,
        "git_sha_end": run.git_sha_end,
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
    pr_facts: attention.PRFacts | None = None,
    deps: dict[str, object] | None = None,
) -> dict[str, object]:
    run_dict = run_to_dict(run, artifacts=artifacts) if run is not None else None
    deps = deps or {
        "depends_on": [],
        "blocked_by": [],
        "blocking": [],
        "blocked": False,
    }
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
        "attention": attention.attention_for(task, run, pr_facts),
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
        "depends_on": deps.get("depends_on", []),
        "blocked_by": deps.get("blocked_by", []),
        "blocking": deps.get("blocking", []),
        "blocked": deps.get("blocked", False),
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

    TaskDependency (Phase 4 T4.1) → Followup → ReviewAssignment →
    Artifact → Run → Task

    TaskDependency edges are dropped first (FK ON DELETE CASCADE on both
    sides will normally do this automatically; the explicit delete is
    belt-and-suspenders for the partial-cascade path that may skip
    cascading). Followups are deleted first because they reference both
    ``tasks.id`` and ``runs.id`` (nullable FK). Returns the ids of the
    deleted runs so callers can reuse them for disk cleanup (artifact
    store + worktree paths).

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
    # Phase 4 T4.1 — drop dep edges in both directions.
    session.execute(
        delete(TaskDependency).where(
            TaskDependency.task_id.in_(task_ids)
            | TaskDependency.depends_on_id.in_(task_ids)
        )
    )
    session.execute(delete(Followup).where(Followup.task_id.in_(task_ids)))
    session.execute(delete(ReviewAssignment).where(ReviewAssignment.task_id.in_(task_ids)))
    if run_ids:
        session.execute(delete(Artifact).where(Artifact.run_id.in_(run_ids)))
        session.execute(delete(Run).where(Run.id.in_(run_ids)))
    session.execute(delete(Task).where(Task.id.in_(task_ids)))
    return run_ids


# ---- Phase 4 T4.1 — task dependency helpers -----------------------------


# A dependency is "satisfied" when its task is in a terminal deliverable
# state. ``done`` and ``needs_approval`` mean "the work landed"; the other
# terminal states (failed / timed_out / cancelled / interrupted) leave
# dependents blocked (the work didn't actually land).
DEP_SATISFIED_STATUSES = frozenset({"done", "needs_approval"})


def dependencies_for(session: Session, task_id: int) -> list[int]:
    """IDs the task depends on."""
    return list(
        session.execute(
            select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)
        )
       .scalars()
    )


def dependents_for(session: Session, depends_on_id: int) -> list[int]:
    """IDs that depend on the given task (reverse direction)."""
    return list(
        session.execute(
            select(TaskDependency.task_id).where(TaskDependency.depends_on_id == depends_on_id)
        )
        .scalars()
    )


def has_unmet_deps(session: Session, task_id: int) -> bool:
    """True if any of ``task_id``'s deps is not in a satisfied terminal state."""
    dep_ids = dependencies_for(session, task_id)
    if not dep_ids:
        return False
    unmet = list(
        session.execute(
            select(Task.id).where(
                Task.id.in_(dep_ids),
                Task.status.notin_(DEP_SATISFIED_STATUSES),
            )
        ).scalars()
    )
    return bool(unmet)


def _dfs_creates_cycle(
    session: Session, start: int, target: int, visited: set[int]
) -> bool:
    """True if walking ``target -> deps`` reaches ``start`` (a cycle through start)."""
    if target in visited:
        return False
    visited.add(target)
    for nxt in dependencies_for(session, target):
        if nxt == start:
            return True
        if _dfs_creates_cycle(session, start, nxt, visited):
            return True
    return False


def add_dependency(
    session: Session, task_id: int, depends_on_id: int
) -> None:
    """Add an edge ``task_id -> depends_on_id``; raises ``ValueError`` on cycle/self-ref.

    Caller commits.
    """

    if task_id == depends_on_id:
        raise ValueError("a task cannot depend on itself")
    # Ensure both rows exist.
    if session.get(Task, task_id) is None:
        raise LookupError(f"task {task_id} not found")
    if session.get(Task, depends_on_id) is None:
        raise LookupError(f"task {depends_on_id} not found")
    if _dfs_creates_cycle(session, task_id, depends_on_id, set()):
        raise ValueError(f"adding dep {depends_on_id} would create a cycle")
    existing = session.get(
        TaskDependency, (task_id, depends_on_id)
    )
    if existing is not None:
        return  # idempotent
    session.add(TaskDependency(task_id=task_id, depends_on_id=depends_on_id))


def remove_dependency(
    session: Session, task_id: int, depends_on_id: int
) -> bool:
    """Remove an edge. Returns True if the edge existed."""

    edge = session.get(TaskDependency, (task_id, depends_on_id))
    if edge is None:
        return False
    session.delete(edge)
    return True


def dep_dict(session: Session, task_id: int) -> dict[str, object]:
    """The four task-dict dep fields for ``task_to_dict``.

``."""
    depends_on = dependencies_for(session, task_id)
    blocked_by = [
        d for d in depends_on
        if _status_for(session, d) not in DEP_SATISFIED_STATUSES
    ]
    blocking = dependents_for(session, task_id)
    blocked = task_id is not None and bool(blocked_by) and _status_for(
        session, task_id
    ) in {"queued", "running", "waiting_review"}
    return {
        "depends_on": depends_on,
        "blocked_by": blocked_by,
        "blocking": blocking,
        "blocked": blocked,
    }


def _status_for(session: Session, task_id: int) -> str | None:
    t = session.get(Task, task_id)
    return t.status if t is not None else None


def dismiss_task_attention(session: Session, task_id: int) -> Task | None:
    """Mark a task's attention as dismissed so it leaves the "Needs you" state."""
    from jalebi import clock

    task = session.get(Task, task_id)
    if task is None:
        return None
    ctx: dict = {}
    if task.context_json:
        try:
            parsed = json.loads(task.context_json)
            if isinstance(parsed, dict):
                ctx = parsed
        except (TypeError, ValueError):
            ctx = {}
    ctx["attention_dismissed"] = True
    ctx["attention_dismissed_at"] = clock.to_iso(now())
    task.context_json = json.dumps(ctx)
    session.commit()
    return task


def clear_attention_dismissal(session: Session, task: Task) -> bool:
    """Clear a prior attention dismissal when a new run starts.

    A dismissal acknowledges the *previous* run's state; a rerun or
    follow-up re-arms attention so this run's future ``needs_you`` is
    never hidden. Returns True when anything was cleared. The caller
    owns the commit (``queue._prepare_run`` persists it with the run).
    """
    if not task.context_json:
        return False
    try:
        ctx = json.loads(task.context_json)
    except (TypeError, ValueError):
        return False
    if not isinstance(ctx, dict) or not ctx.pop("attention_dismissed", None):
        return False
    ctx.pop("attention_dismissed_at", None)
    task.context_json = json.dumps(ctx)
    return True

