"""Add review assignments (PRD F7).

Revision ID: f0a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-08-08 16:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create review_assignments (one row per reviewer on a PR)."""
    op.create_table(
        "review_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("repo_id", sa.Integer(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_assignments_task_id", "review_assignments", ["task_id"])
    op.create_index("ix_review_assignments_pr_number", "review_assignments", ["pr_number"])


def downgrade() -> None:
    """Drop the assignments table."""
    op.drop_index("ix_review_assignments_pr_number", table_name="review_assignments")
    op.drop_index("ix_review_assignments_task_id", table_name="review_assignments")
    op.drop_table("review_assignments")
