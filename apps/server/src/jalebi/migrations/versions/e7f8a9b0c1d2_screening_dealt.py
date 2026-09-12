"""Owner-handled screening findings (dealt state for rerun context).

Revision ID: e7f8a9b0c1d2
Revises: d4e5f6a7b8c9

Schema: new ``screening_dealt`` table (id PK, screening_id FK with
CASCADE, canonical ``fingerprint`` NOT NULL string, auxiliary nullable
title/file/line for inspection, created_at) with
``UNIQUE(screening_id, fingerprint)``. The fingerprint is stored — never
recomputed from nullable columns — because SQLite treats NULLs as
distinct in UNIQUE constraints, which would allow unbounded duplicates
for file/line-less findings.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "screening_dealt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("screening_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("file", sa.Text(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["screening_id"], ["screenings.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "screening_id", "fingerprint", name="uq_screening_dealt_fp"
        ),
    )
    op.create_index(
        "ix_screening_dealt_screening_id", "screening_dealt", ["screening_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_screening_dealt_screening_id", table_name="screening_dealt")
    op.drop_table("screening_dealt")
