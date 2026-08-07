"""Add task context columns + run/followup PAT model.

Revision ID: b3c1a5f2d9e4
Revises: 0d42c1a9f0b1
Create Date: 2026-08-06 23:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "b3c1a5f2d9e4"
down_revision: str | None = "0d42c1a9f0b1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add task context / PAT tracking columns."""
    op.add_column("tasks", sa.Column("pat_name", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("issues_json", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("prs_json", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("context_json", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("pat_name", sa.Text(), nullable=True))
    op.add_column("followups", sa.Column("pat_name", sa.Text(), nullable=True))
    op.add_column("followups", sa.Column("model", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the added columns."""
    op.drop_column("followups", "model")
    op.drop_column("followups", "pat_name")
    op.drop_column("runs", "pat_name")
    op.drop_column("tasks", "context_json")
    op.drop_column("tasks", "prs_json")
    op.drop_column("tasks", "issues_json")
    op.drop_column("tasks", "pat_name")
