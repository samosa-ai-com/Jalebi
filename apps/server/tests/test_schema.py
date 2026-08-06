import pytest
import sqlalchemy.exc
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from jalebi.db import Base

PHASE0_TABLES = {"repos", "tasks", "runs", "followups", "artifacts", "settings"}


def test_phase0_tables_exist(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert PHASE0_TABLES <= tables


def test_tasks_columns(engine: Engine) -> None:
    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert {
        "id",
        "type",
        "repo_id",
        "source_branch",
        "target_branch",
        "agent_id",
        "model",
        "cli",
        "prompt",
        "status",
        "timeout_minutes",
        "retry_count",
        "pr_number",
        "check_run_id",
        "created_at",
        "updated_at",
    } <= cols


def test_repos_full_name_unique(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO repos (full_name, clone_url) VALUES ('owner/repo', 'https://x')")
        )
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO repos (full_name, clone_url) VALUES ('owner/repo', 'https://y')")
            )


def test_task_status_check_enforced(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO repos (full_name, clone_url) VALUES ('owner/repo', 'https://x')")
        )
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tasks (type, repo_id, prompt, status)"
                    " VALUES ('freeform', 1, 'p', 'bogus-status')"
                )
            )


def test_task_repo_fk_enforced(engine: Engine) -> None:
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO tasks (type, repo_id, prompt) VALUES ('freeform', 999, 'p')")
            )


def test_migrations_match_models(engine: Engine) -> None:
    """Autogenerate against the migrated DB must produce no diffs (models == schema)."""
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, Base.metadata)
    assert diffs == []
