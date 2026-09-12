"""Enforce one unread notification of each kind per task.

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
"""

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # A pre-index race may have created duplicates. Keep the newest unread
    # notification in each group so upgrading an existing installation never
    # fails while adding the invariant.
    op.execute(
        """
        DELETE FROM notifications
        WHERE read_at IS NULL
          AND id NOT IN (
            SELECT newest_id
            FROM (
              SELECT MAX(id) AS newest_id
              FROM notifications
              WHERE read_at IS NULL
              GROUP BY task_id, kind
            )
          )
        """
    )
    op.create_index(
        "uq_notifications_unread_task_kind",
        "notifications",
        ["task_id", "kind"],
        unique=True,
        sqlite_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_unread_task_kind", table_name="notifications")
