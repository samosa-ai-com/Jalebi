"""Add tasks.review_posted (durable review-delivery marker).

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-10 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the delivery marker; existing rows default to unposted.

    Historical reviews posted before this column existed read as unposted —
    the manual post endpoint stays available for them, and the assignment
    row (when present) still reports the truth.
    """
    op.add_column(
        "tasks",
        sa.Column("review_posted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Drop the added column."""
    op.drop_column("tasks", "review_posted")
