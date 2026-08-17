"""Agent catalog service (PRD F6): CRUD + personality/skills for worktree runs.

A catalog agent is deliberately simple — it is *not* a new agent type. It is a
personality (markdown merged into the worktree ``AGENTS.md``) + skill files
(materialized into the worktree's skill roots at run time, see
``worktree_bootstrap.write_agent_skills``) + optional ``cli``/``model``
overrides + custom instructions appended to the task prompt. The default agent
of the CLI reads ``AGENTS.md`` + skills automatically.

Skills are stored in the DB as ``[{name, content}]`` and materialized to disk at
run time into the task worktree's ``.claude/skills/``, ``.codex/skills/`` and
``.agents/skills/`` (the shared Agent Skills format), matching PRD F6.1.
``tasks.agent_id`` references ``catalog_agents.id`` by slug but is FK-less —
every write path validates the slug here.
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi import clock
from jalebi.adapters import available_adapters
from jalebi.db import CatalogAgent, now

AGENT_KINDS = ("general", "reviewer")
ALLOWED_CLIS = tuple(available_adapters())
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,63}$")
MAX_INSTRUCTION_CHARS = 20_000  # custom_instructions ride the task prompt via argv


class CatalogError(ValueError):
    """Raised for invalid catalog agent definitions."""


def _load_skills(skills_json: str | None) -> list[dict[str, str]]:
    if not skills_json:
        return []
    try:
        skills = json.loads(skills_json)
    except (ValueError, TypeError):
        raise CatalogError("skills_json must be a JSON list of {name, content}")
    if not isinstance(skills, list):
        raise CatalogError("skills_json must be a JSON list of {name, content}")
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            raise CatalogError("each skill must be an object {name, content}")
        name = str(skill.get("name", "")).strip()
        content = str(skill.get("content", ""))
        if not name or not SKILL_NAME_RE.match(name):
            raise CatalogError(
                f"invalid skill name: {name!r} (letters/digits/spaces/-/_, ≤64 chars)"
            )
        if name in seen:
            raise CatalogError(f"duplicate skill name: {name}")
        seen.add(name)
        cleaned.append({"name": name, "content": content})
    return cleaned


def _normalize_id(slug: str) -> str:
    slug = (slug or "").strip()
    if not SLUG_RE.match(slug):
        raise CatalogError(
            f"invalid agent id: {slug!r} (lowercase letters/digits with single hyphens)"
        )
    return slug


def validate_definition(
    *,
    name: str,
    kind: str,
    cli: str | None,
    model: str | None,
    personality_md: str,
    skills: list[dict[str, str]],
    custom_instructions: str,
    enabled: bool,
) -> None:
    """Validate a catalog agent definition (shared by create + update)."""
    if not name or not name.strip():
        raise CatalogError("name is required")
    if kind not in AGENT_KINDS:
        raise CatalogError(f"kind must be one of {AGENT_KINDS}")
    if cli is not None and not isinstance(cli, str):
        raise CatalogError("cli must be a string or null")
    if cli is not None and cli not in ALLOWED_CLIS:
        raise CatalogError(f"unsupported agent cli: {cli}")
    if model is not None and not isinstance(model, str):
        raise CatalogError("model must be a string or null")
    if model is not None and not model.strip():
        raise CatalogError("model must be a non-empty string or null")
    if len(custom_instructions) > MAX_INSTRUCTION_CHARS:
        raise CatalogError(
            f"custom_instructions too long ({len(custom_instructions)} chars; "
            f"max {MAX_INSTRUCTION_CHARS})"
        )
    for skill in skills:
        if "name" not in skill or "content" not in skill:
            raise CatalogError("each skill must have name and content")


def agent_by_slug(session: Session, slug: str) -> CatalogAgent | None:
    return session.get(CatalogAgent, slug)


def list_agents(session: Session, enabled_only: bool = False) -> list[CatalogAgent]:
    query = select(CatalogAgent).order_by(CatalogAgent.name)
    if enabled_only:
        query = query.where(CatalogAgent.enabled.is_(True))
    return list(session.execute(query).scalars())


def create_agent(
    session: Session,
    *,
    id: str,
    name: str,
    kind: str = "general",
    cli: str | None = None,
    model: str | None = None,
    personality_md: str = "",
    skills: list[dict[str, str]] | None = None,
    custom_instructions: str = "",
    enabled: bool = True,
) -> CatalogAgent:
    """Insert a new catalog agent (validates the definition + slug uniqueness)."""
    slug = _normalize_id(id)
    if agent_by_slug(session, slug) is not None:
        raise CatalogError(f"an agent with id {slug!r} already exists")
    skills = skills or []
    # Normalize + validate skills (name format, duplicates) — shared with update.
    skills = _load_skills(json.dumps(skills)) if skills else []
    # An empty string for cli/model means "no override" (the UI's default option
    # sends ""). Mirror update_agent's empty-clears semantics so create and update
    # treat "" identically instead of create rejecting it.
    cli = cli or None
    model = model or None
    validate_definition(
        name=name,
        kind=kind,
        cli=cli,
        model=model,
        personality_md=personality_md,
        skills=skills,
        custom_instructions=custom_instructions,
        enabled=enabled,
    )
    row = CatalogAgent(
        id=slug,
        name=name.strip(),
        kind=kind,
        cli=cli,
        model=model,
        personality_md=personality_md,
        skills_json=json.dumps(skills) if skills else None,
        custom_instructions=custom_instructions,
        enabled=enabled,
        created_at=now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_agent(
    session: Session,
    slug: str,
    *,
    name: str | None = None,
    kind: str | None = None,
    cli: str | None = None,
    model: str | None = None,
    personality_md: str | None = None,
    skills: list[dict[str, str]] | None = None,
    custom_instructions: str | None = None,
    enabled: bool | None = None,
) -> CatalogAgent:
    """Update fields of an existing catalog agent; ``None`` leaves a field unchanged.

    An empty string for ``cli``/``model`` means **clear the pin** (set back to
    ``None``), so a user can unpin a model or reset a CLI once set.
    """
    row = agent_by_slug(session, slug)
    if row is None:
        raise KeyError(slug)
    # Distinguish "not provided" (None → unchanged) from "explicitly cleared"
    # ("" → set back to None) by capturing the raw presence first.
    provided_cli = cli is not None
    provided_model = model is not None
    if provided_cli:
        cli = cli or None
    if provided_model:
        model = model or None
    # Normalize + validate skills (name format, duplicates) — shared with create,
    # so the PUT route cannot store an invalid/poisoned skill name.
    merged_skills = _load_skills(row.skills_json) if skills is None else _load_skills(
        json.dumps(skills)
    )
    validate_definition(
        name=name if name is not None else row.name,
        kind=kind if kind is not None else row.kind,
        cli=cli if provided_cli else row.cli,
        model=model if provided_model else row.model,
        personality_md=(
            personality_md if personality_md is not None else row.personality_md
        ),
        skills=merged_skills,
        custom_instructions=(
            custom_instructions if custom_instructions is not None else row.custom_instructions
        ),
        enabled=enabled if enabled is not None else row.enabled,
    )
    if name is not None:
        row.name = name.strip()
    if kind is not None:
        row.kind = kind
    if provided_cli:
        row.cli = cli
    if provided_model:
        row.model = model
    if personality_md is not None:
        row.personality_md = personality_md
    if skills is not None:
        row.skills_json = json.dumps(merged_skills) if merged_skills else None
    if custom_instructions is not None:
        row.custom_instructions = custom_instructions
    if enabled is not None:
        row.enabled = enabled
    session.commit()
    session.refresh(row)
    return row


def delete_agent(session: Session, slug: str) -> bool:
    """Remove a catalog agent. Tasks referencing it keep their history (agent_id
    is a plain slug, no FK — the run resolves it at start and falls back when gone)."""
    row = agent_by_slug(session, slug)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def skills(agent: CatalogAgent) -> list[dict[str, str]]:
    """The agent's skill list (decoded + validated)."""
    return _load_skills(agent.skills_json)


def agent_to_dict(agent: CatalogAgent) -> dict[str, object]:
    return {
        "id": agent.id,
        "name": agent.name,
        "kind": agent.kind,
        "cli": agent.cli,
        "model": agent.model,
        "personality_md": agent.personality_md,
        "skills": skills(agent),
        "custom_instructions": agent.custom_instructions,
        "enabled": agent.enabled,
        "created_at": clock.to_iso(agent.created_at),
    }
