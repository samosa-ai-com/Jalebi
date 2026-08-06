"""Phase-0 tables: repos, tasks, runs, followups, artifacts, settings.

Revision ID: 180905017971
Revises:
Create Date: 2026-08-06 03:25:10.374712
"""

import sqlalchemy as sa
from alembic import op

revision: str = "180905017971"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "repos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column(
            "default_branch",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column("clone_url", sa.Text(), nullable=False),
        sa.Column("pat_scope", sa.Text(), nullable=True),
        sa.Column(
            "webhook_registered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("poll_fallback", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "check_runs_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("full_name"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_branch",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column(
            "target_branch",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("cli", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column(
            "timeout_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("check_run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_review','needs_approval','done',"
            "'failed','timed_out','interrupted','cancelled')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "type IN ('issue_fix','pr_review','freeform','screen_finding','triggered')",
            name="ck_tasks_type",
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_repo_id", "tasks", ["repo_id"], unique=False)
    op.create_index("ix_tasks_status", "tasks", ["status"], unique=False)
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("cli", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("steps_json", sa.Text(), nullable=True),
        sa.Column("artifacts_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_task_id", "runs", ["task_id"], unique=False)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"], unique=False)
    op.create_table(
        "followups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_followups_task_id", "followups", ["task_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_followups_task_id", table_name="followups")
    op.drop_table("followups")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_runs_task_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_repo_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("settings")
    op.drop_table("repos")
