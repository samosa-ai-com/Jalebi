"""Add env_vars table and tasks.env_vars_json.

Revision ID: d9e8f7c6b5a4
Revises: a7b3c5d9e2f4
Create Date: 2026-08-08 14:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "d9e8f7c6b5a4"
down_revision: str | None = "a7b3c5d9e2f4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the env-var store and the per-task selection column."""
    op.add_column("tasks", sa.Column("env_vars_json", sa.Text(), nullable=True))
    op.create_table(
        "env_vars",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("repo_id", sa.Integer(), sa.ForeignKey("repos.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", "repo_id", name="uq_env_vars_name_repo"),
    )
    op.create_index("ix_env_vars_repo_id", "env_vars", ["repo_id"])


def downgrade() -> None:
    """Drop the env-var store and the task column."""
    op.drop_index("ix_env_vars_repo_id", table_name="env_vars")
    op.drop_table("env_vars")
    op.drop_column("tasks", "env_vars_json")
