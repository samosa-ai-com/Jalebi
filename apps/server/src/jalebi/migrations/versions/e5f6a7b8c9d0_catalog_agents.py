"""Add the agent catalog (PRD F6).

Revision ID: e5f6a7b8c9d0
Revises: d9e8f7c6b5a4
Create Date: 2026-08-08 15:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d9e8f7c6b5a4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create catalog_agents (personality + skills + cli/model overrides)."""
    op.create_table(
        "catalog_agents",
        sa.Column("id", sa.Text(), primary_key=True),  # slug, e.g. "security-auditor"
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "kind", sa.Text(), nullable=False, server_default=sa.text("'general'")
        ),
        sa.Column("cli", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("personality_md", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("skills_json", sa.Text(), nullable=True),
        sa.Column("custom_instructions", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    """Drop the catalog."""
    op.drop_table("catalog_agents")
