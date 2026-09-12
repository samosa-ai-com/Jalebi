"""Add tasks.address_reviews (creation-time "address PR review comments" flag).

Revision ID: f4a5b6c7d8e9
Revises: e7f8a9b0c1d2
Create Date: 2026-09-08 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the per-task address-reviews flag; existing rows default to off."""
    op.add_column(
        "tasks",
        sa.Column("address_reviews", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Drop the added column."""
    op.drop_column("tasks", "address_reviews")
