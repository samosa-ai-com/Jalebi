"""Add proactive screening (PRD F10).

Two new tables:
- ``screenings`` — a configured screen (name, system prompt, cron cadence,
  repo scope, enabled, notify toggle).
- ``screening_runs`` — one execution per (screen × HEAD); stores the parsed
  findings JSON + the raw (masked) output, and the audited ``head_sha`` which
  doubles as the baseline-dedup watermark.

Revision ID: e6f7a8b9c0d1
Revises: c2d3e4f5a6b7
Create Date: 2026-08-09 17:15:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "screenings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repo_id",
            sa.Integer(),
            sa.ForeignKey("repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column(
            "cadence_cron",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'0 6 * * *'"),
        ),
        sa.Column("scope_branch", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("notify_ntfy", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_screenings_repo_id", "screenings", ["repo_id"])

    op.create_table(
        "screening_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "screening_id",
            sa.Integer(),
            sa.ForeignKey("screenings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("head_sha", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("findings_json", sa.Text(), nullable=True),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_screening_runs_screening_id", "screening_runs", ["screening_id"]
    )


def downgrade() -> None:
    op.drop_table("screening_runs")
    op.drop_table("screenings")
