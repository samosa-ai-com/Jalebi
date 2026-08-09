"""UNIQUE constraint on review_assignments (Step 45/H2).

Two concurrent webhook deliveries (or two concurrent manual calls) on the
same PR with the same reviewer agent could both pass the application-level
``existing = assignments_for_pr(...)`` pre-filter (each sees ``{}``), then
both create duplicate pr_review tasks. The UNIQUE(repo_id, pr_number,
agent_id) constraint is the last line of defense — on IntegrityError the
dispatcher recovers the existing assignment's task instead of re-creating.

``review_assignments`` has no FK-referenced children (run_id is nullable and
runs.task_id → tasks.id, not the other direction), so the batch table
rebuild is safe.
"""

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_assignments") as batch_op:
        batch_op.create_unique_constraint(
            "uq_review_assignments_repo_pr_agent",
            ["repo_id", "pr_number", "agent_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("review_assignments") as batch_op:
        batch_op.drop_constraint("uq_review_assignments_repo_pr_agent", type_="unique")
