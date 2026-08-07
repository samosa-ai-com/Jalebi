"""Add repos.pat_name (owning account for a connected repo).

Revision ID: e1f9c2d4a6b8
Revises: c7d2a1b3e5f6
Create Date: 2026-08-06 21:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "e1f9c2d4a6b8"
down_revision: str | None = "c7d2a1b3e5f6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the repos.pat_name column (None = primary account)."""
    op.add_column("repos", sa.Column("pat_name", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the repos.pat_name column."""
    op.drop_column("repos", "pat_name")
