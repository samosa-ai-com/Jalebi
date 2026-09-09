"""Add followups.cli (backend override recorded per follow-up row).

Revision ID: f5a6b7c8d9e0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-09 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Record the requested backend override on each follow-up row (nullable:
    NULL means the task backend was reused)."""
    op.add_column("followups", sa.Column("cli", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the added column."""
    op.drop_column("followups", "cli")
