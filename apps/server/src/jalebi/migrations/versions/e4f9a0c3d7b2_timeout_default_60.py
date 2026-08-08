"""Align tasks.timeout_minutes server default with the code default (60).

The code default moved 30 -> 60 (Step 34) but the DB-level server_default stayed
30, so a raw SQL insert that omits timeout_minutes would get 30 while ORM inserts
get 60. SQLite can't ALTER COLUMN DEFAULT directly, so rebuild the table.

Revision ID: e4f9a0c3d7b2
Revises: d9e8f7c6b5a4
Create Date: 2026-08-08 16:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "e4f9a0c3d7b2"
down_revision: str | None = "d9e8f7c6b5a4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Set the DB-level default to 60 to match the ORM/code default."""
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "timeout_minutes",
            existing_type=sa.Integer(),
            server_default=sa.text("60"),
        )


def downgrade() -> None:
    """Restore the old 30 default."""
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "timeout_minutes",
            existing_type=sa.Integer(),
            server_default=sa.text("30"),
        )
