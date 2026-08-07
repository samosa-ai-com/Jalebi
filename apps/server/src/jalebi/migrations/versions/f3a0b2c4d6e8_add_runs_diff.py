"""Add runs.diff_text (run-end diff snapshot for the diff viewer).

Revision ID: f3a0b2c4d6e8
Revises: e1f9c2d4a6b8
Create Date: 2026-08-07 00:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "f3a0b2c4d6e8"
down_revision: str | None = "e1f9c2d4a6b8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the run-end diff text (masked, capped) captured at run completion."""
    op.add_column("runs", sa.Column("diff_text", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the added column."""
    op.drop_column("runs", "diff_text")
