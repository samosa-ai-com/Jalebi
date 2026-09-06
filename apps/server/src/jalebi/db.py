"""SQLAlchemy engine, session factory, and Phase-0 models."""

from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.orm import (
    Session as OrmSession,
)

# Wall-clock "now" in the app's configured timezone (see jalebi.clock). Re-exported
# here so the rest of the codebase imports it from jalebi.db as it did `utcnow`.
from jalebi.clock import now

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(
        Text, nullable=False, default="main", server_default=sa.text("'main'")
    )
    clone_url: Mapped[str] = mapped_column(Text, nullable=False)
    pat_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    # the named PAT/account that owns this repo (None = primary/default)
    pat_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("1")
    )
    webhook_registered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("0")
    )
    poll_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("0")
    )
    check_runs_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("0")
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


TASK_TYPES = ("issue_fix", "pr_review", "freeform", "screen_finding", "triggered")
TASK_STATUSES = (
    "queued",
    "running",
    "waiting_review",
    "needs_approval",
    "done",
    "failed",
    "timed_out",
    "interrupted",
    "cancelled",
    # Phase 4 T4.1 — a task whose `depends_on` edges include an unmet dep
    # stays at "blocked" until cascade_unblock flips it back to "queued".
    "blocked",
)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "type IN ('issue_fix','pr_review','freeform','screen_finding','triggered')",
            name="ck_tasks_type",
        ),
        CheckConstraint(
            "status IN ('queued','running','waiting_review','needs_approval','done',"
            "'failed','timed_out','interrupted','cancelled','blocked')",
            name="ck_tasks_status",
        ),
        Index("ix_tasks_repo_id", "repo_id"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), nullable=False)
    source_branch: Mapped[str] = mapped_column(
        Text, nullable=False, default="main", server_default=sa.text("'main'")
    )
    target_branch: Mapped[str] = mapped_column(
        Text, nullable=False, default="main", server_default=sa.text("'main'")
    )
    # catalog agent slug (FK added in Phase 1)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    cli: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # which named PAT drives this task (None = the primary/default token)
    pat_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON lists of referenced issue/PR numbers (UI chips)
    issues_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON dict of fetched context used to build the worktree AGENTS.md
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON list of env-var names injected into the agent subprocess env
    env_vars_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default=sa.text("'queued'")
    )
    # Python default is 60 (the ORM always supplies it); the DB-level
    # server_default stays 30 because SQLite cannot alter a column default in
    # place and the batch rebuild would violate FK constraints — compare_metadata
    # ignores server_default, so this is a deliberate, harmless divergence.
    timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default=sa.text("30")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK in Phase 2
    # "auto" | "manual" | None (None → fall back to the global auto_publish setting).
    # issue_fix defaults to "auto"; freeform/screen_finding/triggered default to "manual".
    publish_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sa.text("1")
    )
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    cli: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    pat_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_sha_start: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_sha_end: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Followup(Base):
    __tablename__ = "followups"
    __table_args__ = (Index("ix_followups_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pat_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class CatalogAgent(Base):
    """A named, user-configured agent = personality + skills + optional overrides.

    ``kind`` is ``general`` or ``reviewer`` (reviewers get the reviewer workflow).
    The ``id`` is a user-chosen slug. ``personality_md`` is merged into the task
    worktree's ``AGENTS.md``; ``skills_json`` holds ``[{name, content}]`` markdown
    files materialized to ``.claude/skills/<name>/SKILL.md`` in the worktree so
    the CLI auto-discovers them; ``custom_instructions`` is appended to the task
    prompt when this agent is selected. ``cli``/``model`` override the task
    defaults. ``tasks.agent_id`` references this table by slug but is deliberately
    FK-less (a SQLite batch rebuild of the FK-referenced ``tasks`` parent is the
    Step-37 migration hazard) — validity is enforced in the service layer.
    """

    __tablename__ = "catalog_agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="general", server_default=sa.text("'general'")
    )
    cli: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality_md: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=sa.text("''")
    )
    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_instructions: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=sa.text("''")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class EnvVar(Base):
    """A named environment variable injected into task agent subprocesses.

    ``repo_id`` NULL means the variable applies to every repo; a non-NULL value
    scopes it to one repo. Values are secrets: they are never returned in full
    by the API, and they are added to the masker so they are redacted if the
    agent echoes them.
    """

    __tablename__ = "env_vars"
    __table_args__ = (
        Index("ix_env_vars_repo_id", "repo_id"),
        sa.UniqueConstraint("name", "repo_id", name="uq_env_vars_name_repo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[int | None] = mapped_column(ForeignKey("repos.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class ReviewAssignment(Base):
    """One reviewer (catalog agent of kind ``reviewer``) assigned to review a PR.

    Each reviewer runs as its OWN ``pr_review`` task (``task_id`` = that task) —
    reusing the existing review worktree + posting machinery, running in parallel
    under the queue's concurrency. The assignment is a lightweight registry
    (task ↔ agent ↔ PR ↔ repo ↔ status) so the PR card and the webhook flow can
    show which reviewers have posted.

    The ``UNIQUE(repo_id, pr_number, agent_id)`` constraint is the last line of
    defense against duplicate reviewer assignments under concurrent webhook
    deliveries or manual calls (the application-level ``assignments_for_pr``
    pre-filter is racy; two threads can both see ``{}`` before either inserts).
    On IntegrityError, ``reviews.assign_reviewers`` recovers the existing
    assignment's task rather than re-creating.
    """

    __tablename__ = "review_assignments"
    __table_args__ = (
        Index("ix_review_assignments_task_id", "task_id"),
        Index("ix_review_assignments_pr_number", "pr_number"),
        UniqueConstraint(
            "repo_id", "pr_number", "agent_id",
            name="uq_review_assignments_repo_pr_agent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default=sa.text("'queued'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class TriggerRule(Base):
    """A per-repo webhook trigger rule (PRD F14).

    ``event`` is the full event key, e.g. ``pull_request.opened`` (from the
    ``X-GitHub-Event`` header + the payload's ``action``). ``action`` is what to
    do: ``start_review`` (reviewer tasks per ``agent_ids_json``), ``triage_issue``
    (issue_fix task), ``create_task`` (freeform task with custom_instructions),
    or ``rerun_review`` (re-enqueue the PR's existing reviewer tasks). Optional
    scope filters narrow when a rule fires.
    """

    __tablename__ = "trigger_rules"
    __table_args__ = (Index("ix_trigger_rules_repo_id", "repo_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    branch_filter: Mapped[str | None] = mapped_column(Text, nullable=True)
    label_filter: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    author_filter: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class EventDelivery(Base):
    """One received webhook delivery (idempotency + replay log, PRD F14).

    ``github_delivery_id`` is UNIQUE (the ``X-GitHub-Delivery`` header), so a
    GitHub re-delivery is detected and skipped. ``payload_json`` is the raw body
    so a delivery can be replayed later; ``result`` records what the rule did.
    """

    __tablename__ = "event_deliveries"
    __table_args__ = (Index("ix_event_deliveries_repo_id", "repo_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_delivery_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    repo_id: Mapped[int | None] = mapped_column(
        ForeignKey("repos.id", ondelete="SET NULL"), nullable=True
    )
    repo_full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="received", server_default=sa.text("'received'")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON summary

class Screening(Base):
    """A proactive, scheduled code audit (PRD F10).

    Each screen audits a connected repo at HEAD with its own system prompt and
    cadence, producing structured findings. Screening is **notify-only** — it
    never creates tasks/PRs on its own; the owner converts findings into
    ``screen_finding`` tasks explicitly. ``scope_branch`` NULL means the repo's
    default branch. ``findings`` are stored on each run, not a separate table.
    """

    __tablename__ = "screenings"
    __table_args__ = (Index("ix_screenings_repo_id", "repo_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cadence_cron: Mapped[str] = mapped_column(
        Text, nullable=False, default="0 6 * * *", server_default=sa.text("'0 6 * * *'")
    )
    scope_branch: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional agent pins for the audit run (like catalog agents): ``cli`` is the
    # backend (default "opencode"); ``model`` overrides the CLI default. NULL =
    # no pin.
    cli: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("1")
    )
    notify_ntfy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class ScreeningRun(Base):
    """One execution of a screen against a specific HEAD.

    ``findings_json`` is the parsed JSON array; ``output_json`` is the raw final
    agent message (both masked). ``head_sha`` is the audited HEAD and doubles as
    the baseline-dedup watermark (skip a due screen if its last ``done`` run is
    at the same HEAD).
    """

    __tablename__ = "screening_runs"
    __table_args__ = (Index("ix_screening_runs_screening_id", "screening_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    screening_id: Mapped[int] = mapped_column(
        ForeignKey("screenings.id", ondelete="CASCADE"), nullable=False
    )
    head_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default=sa.text("'queued'")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CheckRun(Base):
    """Jalebi's registry of the commit statuses it set (PRD F15).

    GitHub's *check-runs* API is GitHub-App-only (PATs cannot write it), so merge
    gating uses **commit statuses** instead — this table mirrors each status
    Jalebi posted. ``github_check_id`` is the GitHub-side status id; ``head_sha``
    is the SHA the status is attached to; ``status``/``conclusion`` mirror the
    posted state (``conclusion`` holds ``pending``/``success``/``failure``/
    ``error``). A row is keyed by ``(task_id, head_sha, name/context)`` so a
    follow-up replaces the same GitHub status (matched by ``(sha, context)``) and
    a new pushed head gets a fresh row. ``tasks.check_run_id`` points at the
    latest row and is deliberately **not a real FK** (the SQLite batch-rebuild of
    ``tasks`` is the Step-37 migration hazard); validity is enforced in the
    service layer.
    """

    __tablename__ = "check_runs"
    __table_args__ = (
        Index("ix_check_runs_task_id", "task_id"),
        Index("ix_check_runs_head_sha", "head_sha"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), nullable=False)
    head_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default=sa.text("'queued'")
    )
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_check_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# ---- Phase 4 T4.1 — task dependency graph -------------------------------


class TaskDependency(Base):
    """DAG edge: ``task_id`` depends on ``depends_on_id`` (both Task rows).

    Cascade-deletes on either side (deleting a task also drops its edges).
    The self-ref CHECK constraint is enforced by the migration; the unique
    pair constraint prevents duplicate edges.
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint("task_id != depends_on_id", name="ck_dep_self_ref"),
        UniqueConstraint("task_id", "depends_on_id", name="uq_dep_pair"),
        Index("ix_dep_task_id", "task_id"),
        Index("ix_dep_depends_on_id", "depends_on_id"),
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now, server_default=sa.text("CURRENT_TIMESTAMP")
    )


# ---- Phase 4 T4.3 — durable SSE timeline ----------------------------------


class TaskEvent(Base):
    """A persisted step event (one row per ``events.publish``).

    The in-memory ring buffer still serves the live fanout (low latency);
    this table is the durable record so a tab reload after a restart
    backfills the whole timeline via ``TaskEvents.subscribe(after_seq=N)``.
    Per-(task, run) cap of 2000 rows is enforced in code.
    """

    __tablename__ = "task_events"
    __table_args__ = (
        Index("ix_task_events_task_seq", "task_id", "seq"),
        Index("ix_task_events_task_run", "task_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now, server_default=sa.text("CURRENT_TIMESTAMP")
    )


# ---- Phase 4 T4.2 — auto-nudge dedup --------------------------------------


class Nudge(Base):
    """A single delivered nudge, keyed by a stable signature.

    The unique pair (task_id, signature) is the dedup mechanism — the
    same ``signature`` never produces a second nudge. Signatures are built
    by ``nudger.py``: ``f"{task_id}:{kind}:{ref}"`` where ``ref`` is the
    underlying event id (review id, status context+sha, etc.).
    """

    __tablename__ = "nudges"
    __table_args__ = (
        UniqueConstraint("task_id", "signature", name="uq_nudge_sig"),
        Index("ix_nudges_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now, server_default=sa.text("CURRENT_TIMESTAMP")
    )


_engine: Engine | None = None
Session = sessionmaker(expire_on_commit=False)


def init_db(db_url: str) -> None:
    """Create the engine for ``db_url`` and wire the shared session factory to it."""
    global _engine
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    _engine = engine
    Session.configure(bind=engine)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialized; call init_db first")
    return _engine


def get_session() -> OrmSession:
    """Return the request-scoped SQLAlchemy session bound to the Flask app context."""
    from flask import g  # lazy import keeps db.py framework-agnostic

    session = getattr(g, "_db_session", None)
    if session is None:
        session = Session()
        g._db_session = session
    return session


def close_db() -> None:
    """Dispose the engine and detach the session factory (mainly for tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    Session.configure(bind=None)


def run_migrations(db_url: str) -> None:
    """Bring the database at ``db_url`` up to the latest Alembic revision."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_command.upgrade(cfg, "head")
