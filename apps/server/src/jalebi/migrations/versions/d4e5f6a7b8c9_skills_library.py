"""Standalone skill library + agent links + agent description/avatar.

Revision ID: d4e5f6a7b8c9
Revises: 8a9b0c1d2e30

Schema:
- New ``catalog_skills`` table (id slug PK, name, description, content,
  tags_json, created_at, updated_at).
- ``catalog_agents`` gains ``skill_ids_json`` (ordered JSON list of linked
  library skill slugs, FK-less by design), ``description`` ('' default) and
  ``avatar`` (nullable avatar id).

Data migration: every inline skill in ``catalog_agents.skills_json`` becomes
a library row (deduplicated by slug; same name + same content shares one row,
same name + different content gets a ``-2`` suffix), each agent's
``skill_ids_json`` preserves its original skill order, and ``skills_json`` is
cleared (run-time resolution merges library links first, inline extras after,
so nothing is lost even if a row is missed).

Downgrade restores ``skills_json`` from the link order (content comes from the
library rows) before dropping the table/columns, so no skill content is lost
in either direction.
"""

import json
import re
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "8a9b0c1d2e30"
branch_labels: str | None = None
depends_on: str | None = None

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _slugify(name: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug or not _SLUG_RE.match(slug):
        return fallback
    return slug


def upgrade() -> None:
    op.create_table(
        "catalog_skills",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "description", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("tags_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("catalog_agents") as batch_op:
        batch_op.add_column(sa.Column("skill_ids_json", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "description",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(sa.Column("avatar", sa.Text(), nullable=True))

    # Migrate inline skills into the library.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, skills_json FROM catalog_agents")
    ).fetchall()
    by_slug: dict[str, dict] = {}  # slug -> {name, content}
    now = datetime.now().replace(microsecond=0)
    for agent_id, skills_json in rows:
        if not skills_json:
            continue
        try:
            inline = json.loads(skills_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(inline, list):
            continue
        links: list[str] = []
        for i, skill in enumerate(inline):
            if not isinstance(skill, dict):
                continue
            name = str(skill.get("name", "")).strip()
            content = str(skill.get("content", ""))
            if not name:
                continue
            slug = _slugify(name, f"skill-{agent_id}-{i}")
            base, n = slug, 2
            while slug in by_slug and by_slug[slug]["content"] != content:
                slug = f"{base}-{n}"
                n += 1
            if slug not in by_slug:
                by_slug[slug] = {"name": name, "content": content}
            if slug not in links:
                links.append(slug)
        bind.execute(
            sa.text(
                "UPDATE catalog_agents SET skill_ids_json = :links, "
                "skills_json = NULL WHERE id = :id"
            ),
            {"links": json.dumps(links) if links else None, "id": agent_id},
        )
    for slug, skill in by_slug.items():
        bind.execute(
            sa.text(
                "INSERT INTO catalog_skills (id, name, description, content, "
                "tags_json, created_at, updated_at) VALUES (:id, :name, '', "
                ":content, NULL, :now, :now)"
            ),
            {"id": slug, "name": skill["name"], "content": skill["content"], "now": now},
        )


def downgrade() -> None:
    # Restore inline skills from the link order first (content from library).
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, skill_ids_json FROM catalog_agents")
    ).fetchall()
    for agent_id, skill_ids_json in rows:
        try:
            links = json.loads(skill_ids_json) if skill_ids_json else []
        except (ValueError, TypeError):
            links = []
        if not isinstance(links, list) or not links:
            continue
        inline = []
        for slug in links:
            row = bind.execute(
                sa.text(
                    "SELECT name, content FROM catalog_skills WHERE id = :id"
                ),
                {"id": slug},
            ).fetchone()
            if row is not None:
                inline.append({"name": row[0], "content": row[1]})
        bind.execute(
            sa.text("UPDATE catalog_agents SET skills_json = :s WHERE id = :id"),
            {"s": json.dumps(inline) if inline else None, "id": agent_id},
        )
    op.drop_table("catalog_skills")
    with op.batch_alter_table("catalog_agents") as batch_op:
        batch_op.drop_column("skill_ids_json")
        batch_op.drop_column("description")
        batch_op.drop_column("avatar")
