"""Projects V2 board sync: repo opt-in columns + project_items watermark.

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1

Schema: ``repos.project_sync_enabled`` (bool, default off) and
``repos.project_last_sync_at`` (nullable datetime), plus a new
``project_items`` table (repo FK CASCADE, UNIQUE content_node_id) recording
which issues/PRs were already added to the central board so re-syncs and
webhook re-deliveries never duplicate items (issue #23).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "repos",
        sa.Column(
            "project_sync_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("repos", sa.Column("project_last_sync_at", sa.DateTime(), nullable=True))
    op.create_table(
        "project_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content_node_id", sa.Text(), nullable=False),
        sa.Column("item_number", sa.Integer(), nullable=False),
        sa.Column("project_item_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "content_node_id", name="uq_project_items_content_node_id"
        ),
    )
    op.create_index("ix_project_items_repo_id", "project_items", ["repo_id"])


def downgrade() -> None:
    op.drop_index("ix_project_items_repo_id", table_name="project_items")
    op.drop_table("project_items")
    op.drop_column("repos", "project_last_sync_at")
    op.drop_column("repos", "project_sync_enabled")
