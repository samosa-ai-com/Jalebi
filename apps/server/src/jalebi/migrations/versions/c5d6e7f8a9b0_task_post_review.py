"""Add tasks.post_review ("post review on completion" flag for pr_review tasks).

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-10 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the per-task review auto-post flag; existing rows default to on
    (today's behavior: post the review when the run finishes with content)."""
    op.add_column(
        "tasks",
        sa.Column("post_review", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    """Drop the added column."""
    op.drop_column("tasks", "post_review")
