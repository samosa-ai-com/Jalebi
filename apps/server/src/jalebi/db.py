"""SQLAlchemy engine, session factory, and Phase-0 models."""

from datetime import UTC, datetime
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
    Text,
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

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite-friendly)."""
    return datetime.now(UTC).replace(tzinfo=None)


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
            "'failed','timed_out','interrupted','cancelled')",
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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


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


class Followup(Base):
    __tablename__ = "followups"
    __table_args__ = (Index("ix_followups_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pat_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ReviewAssignment(Base):
    """One reviewer (catalog agent of kind ``reviewer``) assigned to review a PR.

    Each reviewer runs as its OWN ``pr_review`` task (``task_id`` = that task) —
    reusing the existing review worktree + posting machinery, running in parallel
    under the queue's concurrency. The assignment is a lightweight registry
    (task ↔ agent ↔ PR ↔ repo ↔ status) so the PR card and the webhook flow can
    show which reviewers have posted.
    """

    __tablename__ = "review_assignments"
    __table_args__ = (
        Index("ix_review_assignments_task_id", "task_id"),
        Index("ix_review_assignments_pr_number", "pr_number"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


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
