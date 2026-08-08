"""Add trigger rules + webhook delivery log (PRD F14).

Revision ID: a6b7c8d9e0f1
Revises: f0a1b2c3d4e5
Create Date: 2026-08-08 17:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create trigger_rules (per-repo event→action→agents) + event_deliveries."""
    op.create_table(
        "trigger_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_id", sa.Integer(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),  # e.g. "pull_request.opened"
        sa.Column("action", sa.Text(), nullable=False),  # start_review | triage_issue | create_task
        sa.Column("branch_filter", sa.Text(), nullable=True),
        sa.Column("label_filter", sa.Text(), nullable=True),  # JSON list
        sa.Column("author_filter", sa.Text(), nullable=True),
        sa.Column("agent_ids_json", sa.Text(), nullable=True),  # JSON list
        sa.Column("custom_instructions", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_trigger_rules_repo_id", "trigger_rules", ["repo_id"])

    op.create_table(
        "event_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("github_delivery_id", sa.Text(), nullable=False, unique=True),
        sa.Column("event", sa.Text(), nullable=False),  # e.g. "pull_request"
        sa.Column("action", sa.Text(), nullable=True),  # e.g. "opened"
        sa.Column("repo_id", sa.Integer(), sa.ForeignKey("repos.id"), nullable=True),
        sa.Column("repo_full_name", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column(
            "matched_rule_id", sa.Integer(), sa.ForeignKey("trigger_rules.id"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'received'")),
        sa.Column("result", sa.Text(), nullable=True),  # JSON summary
    )
    op.create_index("ix_event_deliveries_repo_id", "event_deliveries", ["repo_id"])


def downgrade() -> None:
    """Drop the webhook tables."""
    op.drop_index("ix_event_deliveries_repo_id", table_name="event_deliveries")
    op.drop_table("event_deliveries")
    op.drop_index("ix_trigger_rules_repo_id", table_name="trigger_rules")
    op.drop_table("trigger_rules")
