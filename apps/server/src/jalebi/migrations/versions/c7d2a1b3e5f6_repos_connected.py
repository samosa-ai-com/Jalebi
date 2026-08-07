"""Add repos.connected for soft-disconnect.

Revision ID: c7d2a1b3e5f6
Revises: b3c1a5f2d9e4
Create Date: 2026-08-06 20:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2a1b3e5f6"
down_revision: str | None = "b3c1a5f2d9e4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the repos.connected flag (default connected)."""
    op.add_column(
        "repos",
        sa.Column("connected", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    """Drop the repos.connected column."""
    op.drop_column("repos", "connected")
