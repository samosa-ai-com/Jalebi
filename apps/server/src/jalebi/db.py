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
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default=sa.text("'queued'")
    )
    timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default=sa.text("30")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FK in Phase 2
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
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Followup(Base):
    __tablename__ = "followups"
    __table_args__ = (Index("ix_followups_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
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
