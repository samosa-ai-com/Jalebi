"""Add runs.pid for orphaned-process recovery.

Revision ID: 0d42c1a9f0b1
Revises: 180905017971
Create Date: 2026-08-06 22:40:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0d42c1a9f0b1"
down_revision: str | None = "180905017971"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the runs.pid column."""
    op.add_column("runs", sa.Column("pid", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop the runs.pid column."""
    op.drop_column("runs", "pid")
