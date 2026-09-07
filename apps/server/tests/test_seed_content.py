"""Validation for the real seed content (pure validation, no DB writes).

Guards the curated library: every seed skill passes the service validator,
every agent links only skills that exist in the seed set, and counts stay
pinned so an accidental edit to the seed modules fails loudly.
"""

from jalebi import catalog
from jalebi.seed_catalog import SEED_AGENTS, SEED_SKILLS

FRONTEND_AVATARS = {
    "shield",
    "magnifier",
    "flask",
    "wrench",
    "doc",
    "rocket",
    "compass",
    "bug",
    "bolt",
    "eye",
    "link",
    "spark",
}


def test_seed_skill_count_and_validity() -> None:
    assert len(SEED_SKILLS) == 31
    ids = [str(s["id"]) for s in SEED_SKILLS]
    assert len(set(ids)) == len(ids)
    for entry in SEED_SKILLS:
        assert isinstance(entry, dict)
        tags = entry.get("tags", [])
        assert isinstance(tags, list)
        catalog.validate_skill(
            name=str(entry.get("name", "")),
            description=str(entry.get("description", "") or ""),
            content=str(entry.get("content", "") or ""),
            tags=[str(t) for t in tags],
        )


def test_seed_agent_count_links_and_avatars() -> None:
    assert len(SEED_AGENTS) == 16
    skill_ids = {str(s["id"]) for s in SEED_SKILLS}
    ids = [str(a["id"]) for a in SEED_AGENTS]
    assert len(set(ids)) == len(ids)
    for entry in SEED_AGENTS:
        assert isinstance(entry, dict)
        links = entry.get("skill_ids", [])
        assert isinstance(links, list)
        dangling = [s for s in links if str(s) not in skill_ids]
        assert not dangling, f"{entry['id']} links unknown skills: {dangling}"
        avatar = entry.get("avatar")
        assert avatar in FRONTEND_AVATARS, f"{entry['id']}: unknown avatar {avatar!r}"
        assert entry.get("kind") in ("general", "reviewer")
        catalog.validate_definition(
            name=str(entry.get("name", "")),
            kind=str(entry.get("kind", "general")),
            cli=None,
            model=None,
            personality_md=str(entry.get("personality_md", "") or ""),
            skills=[],
            skill_ids=[str(s) for s in links],
            custom_instructions=str(entry.get("custom_instructions", "") or ""),
            enabled=bool(entry.get("enabled", True)),
            description=str(entry.get("description", "") or ""),
            avatar=str(avatar) if avatar else None,
        )
