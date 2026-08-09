"""Drop event_deliveries.matched_rule_id (Step 45/M5).

The single matched_rule_id column could only record the first rule that
matched a delivery; when multiple rules matched, only one was persisted
while result.rules[] already carried the full set. Drop the column — the
JSON result.rules[] is the single source of truth.

Requires op.batch_alter_table: SQLite refuses a plain ALTER TABLE DROP
COLUMN on a column referenced by a FK constraint (the table rebuild
emits INSERT INTO <tmp> SELECT ..., which fails when the FK parent has
no matching row). batch_alter_table recreates the table cleanly. No
index on matched_rule_id exists, so only the column drop is needed.

A row's matched_rule_id value is intentionally NOT backfilled on
downgrade — the column carried a derived value already present in
result.rules[].rule_id, and the FK's ON DELETE SET NULL semantics
would have nulled it on rule deletion anyway.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("event_deliveries") as batch_op:
        batch_op.drop_column("matched_rule_id")


def downgrade() -> None:
    with op.batch_alter_table("event_deliveries") as batch_op:
        batch_op.add_column(
            sa.Column("matched_rule_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_event_deliveries_matched_rule_id_trigger_rules",
            "trigger_rules",
            ["matched_rule_id"],
            ["id"],
            ondelete="SET NULL",
        )
