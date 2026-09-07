"""Versioned seed content for the skill library + agent catalog.

Seeds are Jalebi-curated starter content (generalized, reusable skills and
agents). Rules:

- **Insert-missing only** — a slug that already exists is never overwritten,
  so owner edits always win.
- **Version-gated** — ``seed_catalog`` runs once per ``SEED_VERSION`` (tracked
  in the ``catalog_seed_version`` setting). Bumping the version delivers new
  seeds; deleting a seed never brings it back within the same version.
- **User deletions are permanent** within a version (nothing re-inserts them).

Seed bodies live here (not in migrations) so they stay reviewable as code.
"""

from jalebi import catalog
from jalebi.seed_data_agents import SEED_AGENTS
from jalebi.seed_data_skills import SEED_SKILLS
from jalebi.settings import get_setting, set_setting

SEED_VERSION = 1
SEED_VERSION_KEY = "catalog_seed_version"


def seed_catalog(session) -> dict[str, int]:
    """Insert missing seed skills/agents for the current SEED_VERSION.

    Returns ``{"skills": n, "agents": n}`` inserted. Safe to call on every
    startup (no-op past the first run per version).
    """
    raw = get_setting(session, SEED_VERSION_KEY)
    raw = get_setting(session, SEED_VERSION_KEY)
    current = 0
    if isinstance(raw, bool):
        current = int(raw)
    elif isinstance(raw, (int, float)):
        current = int(raw)
    elif isinstance(raw, str):
        try:
            current = int(raw)
        except ValueError:
            current = 0
    if current >= SEED_VERSION:
        return {"skills": 0, "agents": 0}
    inserted_skills = 0
    for entry in SEED_SKILLS:
        assert isinstance(entry, dict)
        if catalog.skill_by_id(session, str(entry["id"])) is not None:
            continue
        tags = entry.get("tags", [])
        tags = [str(t) for t in tags] if isinstance(tags, list) else []
        catalog.create_skill(
            session,
            id=str(entry["id"]),
            name=str(entry.get("name", "")),
            description=str(entry.get("description", "") or ""),
            content=str(entry.get("content", "") or ""),
            tags=tags,
        )
        inserted_skills += 1
    inserted_agents = 0
    for entry in SEED_AGENTS:
        assert isinstance(entry, dict)
        if catalog.agent_by_slug(session, str(entry["id"])) is not None:
            continue
        skill_ids = entry.get("skill_ids", [])
        skill_ids = [str(s) for s in skill_ids] if isinstance(skill_ids, list) else []
        catalog.create_agent(
            session,
            id=str(entry["id"]),
            name=str(entry.get("name", "")),
            kind=str(entry.get("kind", "general") or "general"),
            cli=entry.get("cli"),  # type: ignore[arg-type]
            model=entry.get("model"),  # type: ignore[arg-type]
            personality_md=str(entry.get("personality_md", "") or ""),
            skill_ids=skill_ids,
            custom_instructions=str(entry.get("custom_instructions", "") or ""),
            enabled=bool(entry.get("enabled", True)),
            description=str(entry.get("description", "") or ""),
            avatar=entry.get("avatar"),  # type: ignore[arg-type]
        )
        inserted_agents += 1
    set_setting(session, SEED_VERSION_KEY, SEED_VERSION)
    return {"skills": inserted_skills, "agents": inserted_agents}
