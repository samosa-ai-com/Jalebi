"""Add optional per-screen agent pins: ``cli`` (backend) + ``model``.

Revises f1b2c3d4e5f6 (check_runs).

Revision ID: a4b6c8d0e2f4
Revises: f1b2c3d4e5f6
Create Date: 2026-08-15 02:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "a4b6c8d0e2f4"
down_revision: str | None = "f1b2c3d4e5f6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("screenings", sa.Column("cli", sa.Text(), nullable=True))
    op.add_column("screenings", sa.Column("model", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("screenings", "model")
    op.drop_column("screenings", "cli")
