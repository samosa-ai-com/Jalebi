"""Tests for the standalone skill library service (skills section)."""

import pytest

from jalebi import catalog
from jalebi.catalog import CatalogError, SkillInUse


def _skill(session, slug: str = "secure-coding", **overrides):
    payload = {
        "id": slug,
        "name": "Secure Coding",
        "description": "Security checklist for any codebase.",
        "content": "# Secure coding\nNever use eval.",
        "tags": ["security", "review"],
    }
    payload.update(overrides)
    return catalog.create_skill(session, **payload)


def test_create_skill_roundtrip(session) -> None:
    skill = _skill(session)
    assert skill.id == "secure-coding"
    fetched = catalog.skill_by_id(session, "secure-coding")
    assert fetched is not None
    assert fetched.name == "Secure Coding"
    data = catalog.skill_to_dict(fetched)
    assert data["tags"] == ["security", "review"]
    assert data["content"] == "# Secure coding\nNever use eval."
    assert set(catalog.list_skills(session)) == {skill}


def test_skill_slug_validation(session) -> None:
    with pytest.raises(CatalogError, match="invalid agent id"):
        catalog.create_skill(session, id="Bad Slug!", name="X")
    with pytest.raises(CatalogError, match="already exists"):
        _skill(session)
        _skill(session)
    with pytest.raises(CatalogError, match="skill name is required"):
        catalog.create_skill(session, id="x", name="  ")
    with pytest.raises(CatalogError, match="too long"):
        catalog.create_skill(session, id="x", name="X", content="y" * 100_001)
    with pytest.raises(CatalogError, match="tags must be"):
        catalog.create_skill(session, id="x", name="X", tags="nope")  # type: ignore[arg-type]
    with pytest.raises(CatalogError, match="too many skill tags"):
        catalog.create_skill(session, id="x", name="X", tags=[f"t{i}" for i in range(13)])


def test_update_skill(session) -> None:
    _skill(session)
    row = catalog.update_skill(session, "secure-coding", description="New desc")
    assert row.description == "New desc"
    assert row.name == "Secure Coding"  # untouched
    with pytest.raises(KeyError):
        catalog.update_skill(session, "missing", name="X")


def test_library_edit_propagates_by_reference(session) -> None:
    """Reference semantics: editing a library skill changes linked agents."""
    _skill(session, content="# v1\n")
    catalog.create_agent(session, id="a", name="A", skill_ids=["secure-coding"])
    catalog.update_skill(session, "secure-coding", content="# v2\n")
    agent = catalog.agent_by_slug(session, "a")
    assert agent is not None
    assert catalog.resolve_skills(session, agent) == [
        {"name": "Secure Coding", "content": "# v2\n"}
    ]


def test_resolve_order_and_inline_merge(session) -> None:
    _skill(session, slug="b-skill", name="B")
    _skill(session, slug="a-skill", name="A")
    agent = catalog.create_agent(
        session,
        id="a",
        name="A",
        skill_ids=["b-skill", "a-skill"],
        skills=[{"name": "inline-extra", "content": "x"}],
    )
    # A link that outlives its skill (deleted out-of-band) is skipped, never
    # fatal — simulate by poking the column directly (the API refuses this).
    import json

    agent.skill_ids_json = json.dumps(["b-skill", "gone-skill", "a-skill"])
    session.commit()
    # Link order wins; unknown slugs skipped; inline extras appended.
    assert [s["name"] for s in catalog.resolve_skills(session, agent)] == [
        "B",
        "A",
        "inline-extra",
    ]


def test_resolve_inline_name_collision_prefers_library(session) -> None:
    _skill(session, name="Same", content="library")
    agent = catalog.create_agent(
        session,
        id="a",
        name="A",
        skill_ids=["secure-coding"],
        skills=[{"name": "Same", "content": "inline"}],
    )
    assert catalog.resolve_skills(session, agent) == [
        {"name": "Same", "content": "library"}
    ]


def test_agent_link_validation(session) -> None:
    with pytest.raises(CatalogError, match="unknown skill id"):
        catalog.create_agent(session, id="a", name="A", skill_ids=["nope"])
    _skill(session)
    agent = catalog.create_agent(session, id="a", name="A", skill_ids=["secure-coding"])
    assert catalog.agent_skill_ids(agent) == ["secure-coding"]
    with pytest.raises(CatalogError, match="unknown skill id"):
        catalog.update_agent(session, "a", skill_ids=["nope"])
    # Clearing links is allowed.
    agent = catalog.update_agent(session, "a", skill_ids=[])
    assert catalog.agent_skill_ids(agent) == []


def test_skill_usage_and_delete_guard(session) -> None:
    _skill(session)
    assert catalog.skill_usage(session, "secure-coding") == {"agents": []}
    catalog.create_agent(session, id="a", name="A", skill_ids=["secure-coding"])
    catalog.create_agent(session, id="b", name="B", skill_ids=["secure-coding"])
    usage = catalog.skill_usage(session, "secure-coding")
    assert sorted(a["id"] for a in usage["agents"]) == ["a", "b"]  # type: ignore[union-attr]
    with pytest.raises(SkillInUse) as exc:
        catalog.delete_skill(session, "secure-coding")
    assert {a["id"] for a in exc.value.agents} == {"a", "b"}
    # Still there after the refused delete.
    assert catalog.skill_by_id(session, "secure-coding") is not None
    # Unlink everywhere, then delete succeeds.
    catalog.update_agent(session, "a", skill_ids=[])
    catalog.delete_agent(session, "b")
    assert catalog.delete_skill(session, "secure-coding") is True
    assert catalog.delete_skill(session, "secure-coding") is False


def test_agent_description_avatar_validation(session) -> None:
    agent = catalog.create_agent(
        session, id="a", name="A", description="Finds vulns.", avatar="shield"
    )
    assert agent.description == "Finds vulns."
    assert agent.avatar == "shield"
    with pytest.raises(CatalogError, match="description too long"):
        catalog.create_agent(session, id="b", name="B", description="x" * 2001)
    with pytest.raises(CatalogError, match="avatar must be"):
        catalog.create_agent(session, id="b", name="B", avatar="not an id!")
    # Empty avatar clears back to auto.
    row = catalog.update_agent(session, "a", avatar="")
    assert row.avatar is None
    # agent_to_dict carries the new fields.
    data = catalog.agent_to_dict(row, catalog.resolve_skills(session, row))
    assert data["description"] == "Finds vulns."
    assert data["avatar"] is None
    assert data["skill_ids"] == []
