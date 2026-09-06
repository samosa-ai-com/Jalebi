"""Add runs.git_sha_start / git_sha_end for per-run SHA stamping (Phase 4 T1.6).

Revision ID: 7b4c5d6e7f80
Revises: a4b6c8d0e2f4
Create Date: 2026-08-17 03:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "7b4c5d6e7f80"
down_revision: str | None = "a4b6c8d0e2f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the run-level git SHA stamp columns (nullable; -dirty suffix applied by the app)."""
    op.add_column("runs", sa.Column("git_sha_start", sa.String(length=64), nullable=True))
    op.add_column("runs", sa.Column("git_sha_end", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop both added columns."""
    op.drop_column("runs", "git_sha_end")
    op.drop_column("runs", "git_sha_start")
