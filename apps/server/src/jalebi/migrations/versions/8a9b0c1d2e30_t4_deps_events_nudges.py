"""Phase 4 T4 — task dependencies, durable SSE events, nudger dedup.

Three new tables (no FK hazard) + widening of ck_tasks_status to include
the "blocked" status Phase 4 T4.1 introduces.

Revision ID: 8a9b0c1d2e30
Revises: 7b4c5d6e7f80
Create Date: 2026-08-17 04:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "8a9b0c1d2e30"
down_revision: str | None = "7b4c5d6e7f80"
branch_labels: str | None = None
depends_on: str | None = None


_STATUS_VALUES = (
    "'queued','running','waiting_review','needs_approval','done','failed',"
    "'timed_out','interrupted','cancelled','blocked'"
)


def upgrade() -> None:
    # Widen ck_tasks_status to include "blocked". The referencing FKs (runs /
    # followups / review_assignments / check_runs) are dropped and re-added by
    # batch_alter_table on SQLite so the check constraint can be rebuilt.
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status", f"status IN ({_STATUS_VALUES})"
        )

    # task_dependencies — DAG edges (task_id depends_on depends_on_id).
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("task_id != depends_on_id", name="ck_dep_self_ref"),
        sa.UniqueConstraint("task_id", "depends_on_id", name="uq_dep_pair"),
    )
    op.create_index("ix_dep_task_id", "task_dependencies", ["task_id"], unique=False)
    op.create_index("ix_dep_depends_on_id", "task_dependencies", ["depends_on_id"], unique=False)

    # task_events — durable SSE timeline (Phase 4 T4.3).
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_task_events_task_seq", "task_events", ["task_id", "seq"], unique=False)
    op.create_index("ix_task_events_task_run", "task_events", ["task_id", "run_id"], unique=False)

    # nudges — auto-nudge dedup (Phase 4 T4.2).
    op.create_table(
        "nudges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("task_id", "signature", name="uq_nudge_sig"),
    )
    op.create_index("ix_nudges_task_id", "nudges", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_nudges_task_id", table_name="nudges")
    op.drop_table("nudges")
    op.drop_index("ix_task_events_task_run", table_name="task_events")
    op.drop_index("ix_task_events_task_seq", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_dep_depends_on_id", table_name="task_dependencies")
    op.drop_index("ix_dep_task_id", table_name="task_dependencies")
    op.drop_table("task_dependencies")
    # Restore the original check without "blocked".
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued','running','waiting_review','needs_approval','done',"
            "'failed','timed_out','interrupted','cancelled')",
        )
