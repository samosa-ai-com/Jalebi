"""Tests for the catalog service (PRD F6): CRUD, validation, materialization."""

import pytest

from jalebi import catalog
from jalebi.catalog import CatalogError
from jalebi.db import Repo


def _make_repo(session, full_name: str = "owner/repo") -> Repo:
    repo = Repo(
        full_name=full_name,
        default_branch="main",
        clone_url=f"https://github.com/{full_name}.git",
        pat_name="test",
    )
    session.add(repo)
    session.commit()
    return repo


def test_create_agent_roundtrip(session) -> None:
    agent = catalog.create_agent(
        session,
        id="security-auditor",
        name="Security Auditor",
        kind="reviewer",
        cli="opencode",
        model="openai/gpt-5.1",
        personality_md="You are a senior application security engineer.",
        skills=[{"name": "secure-coding", "content": "# Secure coding\nNever use eval."}],
        custom_instructions="Check auth.",
        enabled=True,
    )
    assert agent.id == "security-auditor"
    fetched = catalog.agent_by_slug(session, "security-auditor")
    assert fetched is not None
    assert fetched.kind == "reviewer"
    assert catalog.skills(fetched) == [
        {"name": "secure-coding", "content": "# Secure coding\nNever use eval."}
    ]


def test_duplicate_slug_rejected(session) -> None:
    catalog.create_agent(session, id="auditor", name="A")
    with pytest.raises(CatalogError, match="already exists"):
        catalog.create_agent(session, id="auditor", name="B")


def test_invalid_definition_rejected(session) -> None:
    with pytest.raises(CatalogError, match="invalid agent id"):
        catalog.create_agent(session, id="Bad Slug!", name="X")
    with pytest.raises(CatalogError, match="kind must be"):
        catalog.create_agent(session, id="x", name="X", kind="robot")
    with pytest.raises(CatalogError, match="unsupported agent cli"):
        catalog.create_agent(session, id="x", name="X", cli="codex")
    with pytest.raises(CatalogError, match="name is required"):
        catalog.create_agent(session, id="x", name="  ")


def test_skills_validation(session) -> None:
    with pytest.raises(CatalogError, match="invalid skill name"):
        catalog.create_agent(
            session, id="x", name="X", skills=[{"name": "bad/name!", "content": "c"}]
        )
    with pytest.raises(CatalogError, match="duplicate skill name"):
        catalog.create_agent(
            session,
            id="x",
            name="X",
            skills=[
                {"name": "same", "content": "a"},
                {"name": "same", "content": "b"},
            ],
        )
    with pytest.raises(CatalogError, match="must be a JSON list"):
        catalog._load_skills("not-json")


def test_update_agent_partial(session) -> None:
    catalog.create_agent(session, id="auditor", name="A", kind="general")
    updated = catalog.update_agent(session, "auditor", kind="reviewer", enabled=False)
    assert updated.kind == "reviewer"
    assert updated.enabled is False
    assert updated.name == "A"  # untouched
    with pytest.raises(KeyError):
        catalog.update_agent(session, "missing", name="X")


def test_update_agent_empty_string_clears_pins(session) -> None:
    """An empty-string cli/model on update clears the pin (can be unpinned)."""
    catalog.create_agent(
        session, id="auditor", name="A", cli="opencode", model="openai/gpt-5.1"
    )
    updated = catalog.update_agent(session, "auditor", model="", cli="")
    assert updated.model is None
    assert updated.cli is None


def test_create_agent_empty_string_pins_mean_no_override(session) -> None:
    """Empty-string cli/model on create means 'no override' (the UI's default
    option sends "" — must not be rejected like an unsupported CLI)."""
    agent = catalog.create_agent(session, id="auditor", name="A", cli="", model="")
    assert agent.cli is None
    assert agent.model is None



def test_update_agent_rejects_invalid_skill_name(session) -> None:
    """PUT-path skills are validated the same as create — a poisoned name (e.g.
    path traversal) is refused instead of being stored."""
    catalog.create_agent(session, id="auditor", name="A")
    with pytest.raises(CatalogError, match="invalid skill name"):
        catalog.update_agent(
            session, "auditor", skills=[{"name": "../../escape", "content": "pwned"}]
        )
    # The stored row is unchanged.
    row = catalog.agent_by_slug(session, "auditor")
    assert row is not None
    assert catalog.skills(row) == []


def test_update_agent_rejects_oversized_instructions(session) -> None:
    """custom_instructions rides the task prompt through argv — must be bounded."""
    catalog.create_agent(session, id="auditor", name="A")
    with pytest.raises(CatalogError, match="custom_instructions too long"):
        catalog.update_agent(session, "auditor", custom_instructions="x" * 20_001)


def test_delete_agent(session) -> None:
    catalog.create_agent(session, id="auditor", name="A")
    assert catalog.delete_agent(session, "auditor") is True
    assert catalog.agent_by_slug(session, "auditor") is None
    assert catalog.delete_agent(session, "auditor") is False


def test_list_enabled_only(session) -> None:
    catalog.create_agent(session, id="a", name="A", enabled=True)
    catalog.create_agent(session, id="b", name="B", enabled=False)
    all_ids = {a.id for a in catalog.list_agents(session)}
    enabled_ids = {a.id for a in catalog.list_agents(session, enabled_only=True)}
    assert all_ids == {"a", "b"}
    assert enabled_ids == {"a"}


def test_agent_to_dict_roundtrip(session) -> None:
    agent = catalog.create_agent(
        session,
        id="auditor",
        name="A",
        skills=[{"name": "s", "content": "c"}],
    )
    data = catalog.agent_to_dict(agent)
    assert data["id"] == "auditor"
    assert data["skills"] == [{"name": "s", "content": "c"}]
    assert data["enabled"] is True


def test_create_task_with_agent(session) -> None:
    from jalebi import tasks

    repo = _make_repo(session)
    catalog.create_agent(session, id="auditor", name="A", enabled=True)
    task = tasks.create_task(
        session,
        type_="freeform",
        repo_id=repo.id,
        prompt="do it",
        agent_id="auditor",
    )
    assert task.agent_id == "auditor"


def test_create_task_agent_validation(session) -> None:
    from jalebi import tasks

    repo = _make_repo(session)
    catalog.create_agent(session, id="disabled", name="D", enabled=False)
    with pytest.raises(ValueError, match="catalog agent not found"):
        tasks.create_task(session, type_="freeform", repo_id=repo.id, prompt="p", agent_id="nope")
    with pytest.raises(ValueError, match="disabled"):
        tasks.create_task(
            session, type_="freeform", repo_id=repo.id, prompt="p", agent_id="disabled"
        )
