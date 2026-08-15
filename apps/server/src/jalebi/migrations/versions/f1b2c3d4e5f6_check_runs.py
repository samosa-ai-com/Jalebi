"""Add commit-status registry for merge gating (PRD F15).

One table, ``check_runs`` — Jalebi's registry of the GitHub commit statuses it
posted (commit statuses, not GitHub check runs — the check-runs API is
GitHub-App-only and PATs cannot write it). ``tasks.check_run_id`` is updated by
the service layer to point at the latest row but is deliberately FK-less (a
SQLite batch rebuild of the FK parent ``tasks`` is the Step-37 migration hazard).

Revision ID: f1b2c3d4e5f6
Revises: e6f7a8b9c0d1
Create Date: 2026-08-09 17:40:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f1b2c3d4e5f6"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "check_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("repo_id", sa.Integer(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("github_check_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_check_runs_task_id", "check_runs", ["task_id"])
    op.create_index("ix_check_runs_head_sha", "check_runs", ["head_sha"])


def downgrade() -> None:
    op.drop_table("check_runs")
