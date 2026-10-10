"""Move the review-delivery marker from tasks to runs (per-deliverable posting).

Revision ID: f6a7b8c9d0e1
Revises: d6e7f8a9b0c1
Create Date: 2026-10-10 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Drop the task-wide marker (it blocked manual posting of follow-up
    deliverables) and track delivery per run instead."""
    op.drop_column("tasks", "review_posted")
    op.add_column(
        "runs",
        sa.Column("review_posted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Restore the task-wide marker."""
    op.drop_column("runs", "review_posted")
    op.add_column(
        "tasks",
        sa.Column("review_posted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
