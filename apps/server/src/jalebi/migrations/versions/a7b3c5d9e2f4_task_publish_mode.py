"""Add tasks.publish_mode (per-task auto/manual publish override).

Revision ID: a7b3c5d9e2f4
Revises: f3a0b2c4d6e8
Create Date: 2026-08-08 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "a7b3c5d9e2f4"
down_revision: str | None = "f3a0b2c4d6e8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the per-task publish mode; NULL falls back to the global setting."""
    op.add_column("tasks", sa.Column("publish_mode", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the added column."""
    op.drop_column("tasks", "publish_mode")
