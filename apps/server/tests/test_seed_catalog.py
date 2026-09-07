"""Tests for the versioned catalog seed mechanism (never overwrites)."""

import pytest

from jalebi import catalog, seed_catalog
from jalebi.settings import get_setting


@pytest.fixture(autouse=True)
def _seed_data(monkeypatch):
    monkeypatch.setattr(
        seed_catalog,
        "SEED_SKILLS",
        [
            {
                "id": "secure-coding",
                "name": "Secure Coding",
                "description": "Security checklist.",
                "content": "# v1\n",
                "tags": ["security"],
            }
        ],
    )
    monkeypatch.setattr(
        seed_catalog,
        "SEED_AGENTS",
        [
            {
                "id": "security-auditor",
                "name": "Security Auditor",
                "kind": "reviewer",
                "skill_ids": ["secure-coding"],
                "description": "Finds vulns.",
                "avatar": "shield",
                "enabled": True,
            }
        ],
    )


def test_seed_inserts_then_noops(session) -> None:
    assert seed_catalog.seed_catalog(session) == {"skills": 1, "agents": 1}
    assert get_setting(session, seed_catalog.SEED_VERSION_KEY) == 1
    agent = catalog.agent_by_slug(session, "security-auditor")
    assert agent is not None
    assert catalog.resolve_skills(session, agent) == [
        {"name": "Secure Coding", "content": "# v1\n"}
    ]
    # Second run is a no-op (version already applied).
    assert seed_catalog.seed_catalog(session) == {"skills": 0, "agents": 0}


def test_seed_never_overwrites_owner_content(session) -> None:
    catalog.create_skill(session, id="secure-coding", name="Mine", content="# mine\n")
    catalog.create_agent(session, id="security-auditor", name="Mine")
    assert seed_catalog.seed_catalog(session) == {"skills": 0, "agents": 0}
    skill = catalog.skill_by_id(session, "secure-coding")
    assert skill is not None and skill.content == "# mine\n"
    agent = catalog.agent_by_slug(session, "security-auditor")
    assert agent is not None and agent.name == "Mine"


def test_seed_deletion_stays_deleted_within_version(session) -> None:
    assert seed_catalog.seed_catalog(session) == {"skills": 1, "agents": 1}
    catalog.delete_agent(session, "security-auditor")
    catalog.delete_skill(session, "secure-coding")
    # Same version: nothing comes back.
    assert seed_catalog.seed_catalog(session) == {"skills": 0, "agents": 0}
    assert catalog.agent_by_slug(session, "security-auditor") is None


def test_version_bump_delivers_new_seeds(session, monkeypatch) -> None:
    assert seed_catalog.seed_catalog(session) == {"skills": 1, "agents": 1}
    monkeypatch.setattr(seed_catalog, "SEED_VERSION", 2)
    monkeypatch.setattr(
        seed_catalog,
        "SEED_SKILLS",
        [
            {
                "id": "secure-coding",
                "name": "Secure Coding",
                "content": "# v2\n",
            },
            {"id": "new-skill", "name": "New Skill", "content": "# new\n"},
        ],
    )
    assert seed_catalog.seed_catalog(session) == {"skills": 1, "agents": 0}
    # Existing slug untouched by the new version's copy.
    skill = catalog.skill_by_id(session, "secure-coding")
    assert skill is not None and skill.content == "# v1\n"
    assert catalog.skill_by_id(session, "new-skill") is not None
