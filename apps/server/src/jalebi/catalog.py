"""Agent catalog service (PRD F6): agents + standalone skill library.

A catalog agent is deliberately simple — it is *not* a new agent type. It is a
personality (markdown merged into the worktree ``AGENTS.md``) + library skill
links (``skill_ids_json`` → ``catalog_skills``, resolved at run time) + legacy
inline skill extras (``skills_json``) + optional ``cli``/``model`` overrides +
custom instructions appended to the task prompt. The default agent of the CLI
reads ``AGENTS.md`` + skills automatically.

Skills live in the ``catalog_skills`` library table and are linked by agents
by slug (**reference semantics**: editing a library skill changes every agent
that links it). ``tasks.agent_id`` references ``catalog_agents.id`` by slug
but is FK-less — every write path validates the slug here.
"""

import json
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jalebi import clock
from jalebi.adapters import available_adapters
from jalebi.db import CatalogAgent, now

AGENT_KINDS = ("general", "reviewer")
ALLOWED_CLIS = tuple(available_adapters())
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,63}$")
AVATAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
MAX_INSTRUCTION_CHARS = 20_000  # custom_instructions ride the task prompt via argv
MAX_DESCRIPTION_CHARS = 2_000
MAX_SKILL_CHARS = 100_000
MAX_SKILL_TAGS = 12


class CatalogError(ValueError):
    """Raised for invalid catalog agent/skill definitions."""


class SkillInUse(CatalogError):
    """Raised when deleting a library skill that agents still link."""

    def __init__(self, slug: str, agents: list[dict[str, str]]) -> None:
        self.slug = slug
        self.agents = agents
        names = ", ".join(a["id"] for a in agents)
        super().__init__(
            f"skill {slug!r} is still linked by {len(agents)} agent(s): {names}"
        )


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
    skill_ids: list[str],
    custom_instructions: str,
    enabled: bool,
    description: str = "",
    avatar: str | None = None,
) -> None:
    """Validate a catalog agent definition (shared by create + update)."""
    if not isinstance(name, str) or not name.strip():
        raise CatalogError("name is required")
    if not isinstance(kind, str) or kind not in AGENT_KINDS:
        raise CatalogError(f"kind must be one of {AGENT_KINDS}")
    if cli is not None and not isinstance(cli, str):
        raise CatalogError("cli must be a string or null")
    if cli is not None and cli not in ALLOWED_CLIS:
        raise CatalogError(f"unsupported agent cli: {cli}")
    if model is not None and not isinstance(model, str):
        raise CatalogError("model must be a string or null")
    if model is not None and not model.strip():
        raise CatalogError("model must be a non-empty string or null")
    if not isinstance(personality_md, str):
        raise CatalogError("personality_md must be a string")
    if not isinstance(custom_instructions, str):
        raise CatalogError("custom_instructions must be a string")
    if len(custom_instructions) > MAX_INSTRUCTION_CHARS:
        raise CatalogError(
            f"custom_instructions too long ({len(custom_instructions)} chars; "
            f"max {MAX_INSTRUCTION_CHARS})"
        )
    if not isinstance(enabled, bool):
        # bool("false") is True — loose coercion would silently enable what the
        # owner meant to disable. Callers pass real booleans or omit the field.
        raise CatalogError("enabled must be a boolean")
    if not isinstance(description, str):
        raise CatalogError("description must be a string")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise CatalogError(
            f"description too long ({len(description)} chars; "
            f"max {MAX_DESCRIPTION_CHARS})"
        )
    if avatar is not None:
        if not isinstance(avatar, str) or not AVATAR_RE.match(avatar.strip()):
            raise CatalogError(
                "avatar must be an avatar id (letters/digits/_/-, ≤64 chars) or null"
            )
    if not isinstance(skill_ids, list) or any(
        not isinstance(s, str) for s in skill_ids
    ):
        raise CatalogError("skill_ids must be a list of skill slugs")
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
    skill_ids: list[str] | None = None,
    custom_instructions: str = "",
    enabled: bool = True,
    description: str = "",
    avatar: str | None = None,
) -> CatalogAgent:
    """Insert a new catalog agent (validates the definition + slug uniqueness)."""
    slug = _normalize_id(id)
    if agent_by_slug(session, slug) is not None:
        raise CatalogError(f"an agent with id {slug!r} already exists")
    skills = skills or []
    # Normalize + validate skills (name format, duplicates) — shared with update.
    skills = _load_skills(json.dumps(skills)) if skills else []
    links = _normalize_skill_ids(skill_ids or [])
    _require_skills_exist(session, links)
    # An empty string for cli/model means "no override" (the UI's default option
    # sends ""). Mirror update_agent's empty-clears semantics so create and update
    # treat "" identically instead of create rejecting it.
    cli = cli or None
    model = model or None
    avatar = avatar.strip() if isinstance(avatar, str) and avatar.strip() else None
    validate_definition(
        name=name,
        kind=kind,
        cli=cli,
        model=model,
        personality_md=personality_md,
        skills=skills,
        skill_ids=links,
        custom_instructions=custom_instructions,
        enabled=enabled,
        description=description,
        avatar=avatar,
    )
    row = CatalogAgent(
        id=slug,
        name=name.strip(),
        kind=kind,
        cli=cli,
        model=model,
        personality_md=personality_md,
        skills_json=json.dumps(skills) if skills else None,
        skill_ids_json=json.dumps(links) if links else None,
        custom_instructions=custom_instructions,
        enabled=enabled,
        description=description.strip() if isinstance(description, str) else "",
        avatar=avatar,
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
    skill_ids: list[str] | None = None,
    custom_instructions: str | None = None,
    enabled: bool | None = None,
    description: str | None = None,
    avatar: str | None = None,
) -> CatalogAgent:
    """Update fields of an existing catalog agent; ``None`` leaves a field unchanged.

    An empty string for ``cli``/``model`` means **clear the pin** (set back to
    ``None``), so a user can unpin a model or reset a CLI once set. An empty
    string for ``avatar`` likewise clears it back to auto-assign.
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
    if provided_cli and not provided_model:
        # A backend switch orphans the old model pin (it belonged to the old
        # backend) — drop it unless a new pin arrives in the same call.
        model = None
        provided_model = True
    # Normalize + validate skills (name format, duplicates) — shared with create,
    # so the PUT route cannot store an invalid/poisoned skill name.
    merged_skills = _load_skills(row.skills_json) if skills is None else _load_skills(
        json.dumps(skills)
    )
    if skill_ids is not None:
        links = _normalize_skill_ids(skill_ids)
        _require_skills_exist(session, links)
    else:
        links = _load_skill_ids(row.skill_ids_json)
    provided_avatar = avatar is not None
    if provided_avatar:
        avatar = avatar.strip() if avatar.strip() else None
    validate_definition(
        name=name if name is not None else row.name,
        kind=kind if kind is not None else row.kind,
        cli=cli if provided_cli else row.cli,
        model=model if provided_model else row.model,
        personality_md=(
            personality_md if personality_md is not None else row.personality_md
        ),
        skills=merged_skills,
        skill_ids=links,
        custom_instructions=(
            custom_instructions if custom_instructions is not None else row.custom_instructions
        ),
        enabled=enabled if enabled is not None else row.enabled,
        description=description if description is not None else (row.description or ""),
        avatar=avatar if provided_avatar else row.avatar,
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
    if skill_ids is not None:
        row.skill_ids_json = json.dumps(links) if links else None
    if description is not None:
        row.description = description.strip()
    if provided_avatar:
        row.avatar = avatar
    session.commit()
    session.refresh(row)
    return row


def _load_skill_ids(skill_ids_json: str | None) -> list[str]:
    """Decode an agent's ordered library-skill link list (tolerates junk)."""
    if not skill_ids_json:
        return []
    try:
        links = json.loads(skill_ids_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(links, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for slug in links:
        if isinstance(slug, str) and slug and slug not in seen:
            seen.add(slug)
            cleaned.append(slug)
    return cleaned


def _normalize_skill_ids(skill_ids: list[str]) -> list[str]:
    """Strip + dedupe a caller-supplied link list (order-preserving)."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for slug in skill_ids:
        slug = slug.strip()
        if slug and slug not in seen:
            seen.add(slug)
            cleaned.append(slug)
    return cleaned


def _require_skills_exist(session: Session, links: list[str]) -> None:
    """Refuse links to unknown library skills (fail fast at write time)."""
    for slug in links:
        if skill_by_id(session, slug) is None:
            raise CatalogError(f"unknown skill id: {slug!r}")


def agent_skill_ids(agent: CatalogAgent) -> list[str]:
    """The agent's linked library skill slugs, in order."""
    return _load_skill_ids(agent.skill_ids_json)


def resolve_skills(
    session: Session, agent: CatalogAgent
) -> list[dict[str, str]]:
    """The agent's effective skill list for a run: library skills in link
    order, then legacy inline extras (deduplicated by name, library wins).
    Unknown link slugs are skipped — a deleted-then-recreated skill never
    breaks a run.
    """
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for slug in _load_skill_ids(agent.skill_ids_json):
        skill = skill_by_id(session, slug)
        if skill is None:
            continue
        resolved.append({"name": skill.name, "content": skill.content})
        seen.add(skill.name)
    for inline in _load_skills(agent.skills_json):
        if inline["name"] not in seen:
            seen.add(inline["name"])
            resolved.append(inline)
    return resolved


def agent_usage(session: Session, slug: str) -> dict[str, object]:
    """Where a catalog agent is referenced: historical task count + live trigger
    rules. Deleting an agent orphans task history (by design) but breaks live
    rules at dispatch — the UI shows this before delete.
    """
    from jalebi.db import Repo, Task, TriggerRule

    task_count = session.execute(
        select(func.count()).select_from(Task).where(Task.agent_id == slug)
    ).scalar_one()
    rules = []
    for rule in session.execute(select(TriggerRule)).scalars():
        try:
            agent_ids = json.loads(rule.agent_ids_json) if rule.agent_ids_json else []
        except (ValueError, TypeError):
            agent_ids = []
        if slug in agent_ids:
            repo = session.get(Repo, rule.repo_id)
            rules.append(
                {
                    "id": rule.id,
                    "event": rule.event,
                    "action": rule.action,
                    "repo_id": rule.repo_id,
                    "repo_full_name": repo.full_name if repo is not None else None,
                }
            )
    return {"task_count": task_count, "trigger_rules": rules}


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
    """The agent's legacy inline skill extras (decoded + validated).

    Prefer :func:`resolve_skills` (needs a session) for the effective run-time
    list, which merges library links first.
    """
    return _load_skills(agent.skills_json)


def agent_to_dict(
    agent: CatalogAgent, skills: list[dict[str, str]] | None = None
) -> dict[str, object]:
    return {
        "id": agent.id,
        "name": agent.name,
        "kind": agent.kind,
        "cli": agent.cli,
        "model": agent.model,
        "personality_md": agent.personality_md,
        "skills": skills if skills is not None else _load_skills(agent.skills_json),
        "skill_ids": _load_skill_ids(agent.skill_ids_json),
        "custom_instructions": agent.custom_instructions,
        "enabled": agent.enabled,
        "description": agent.description or "",
        "avatar": agent.avatar,
        "created_at": clock.to_iso(agent.created_at),
    }


# ---------------------------------------------------------------------------
# Skill library
# ---------------------------------------------------------------------------


def validate_skill(
    *,
    name: str,
    description: str,
    content: str,
    tags: list[str],
) -> None:
    """Validate a library skill definition (shared by create + update)."""
    if not isinstance(name, str) or not name.strip():
        raise CatalogError("skill name is required")
    if len(name.strip()) > 200:
        raise CatalogError("skill name too long (max 200 chars)")
    if not isinstance(description, str):
        raise CatalogError("skill description must be a string")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise CatalogError(
            f"skill description too long ({len(description)} chars; "
            f"max {MAX_DESCRIPTION_CHARS})"
        )
    if not isinstance(content, str):
        raise CatalogError("skill content must be a string")
    if len(content) > MAX_SKILL_CHARS:
        raise CatalogError(
            f"skill content too long ({len(content)} chars; max {MAX_SKILL_CHARS})"
        )
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise CatalogError("skill tags must be a list of strings")
    cleaned = [t.strip() for t in tags if t.strip()]
    if len(cleaned) > MAX_SKILL_TAGS:
        raise CatalogError(f"too many skill tags (max {MAX_SKILL_TAGS})")
    for tag in cleaned:
        if len(tag) > 32:
            raise CatalogError(f"skill tag too long: {tag!r} (max 32 chars)")


def _normalize_tags(tags: list[str]) -> list[str]:
    """Strip + dedupe tags (order-preserving)."""
    if not isinstance(tags, list):
        raise CatalogError("skill tags must be a list of strings")
    if any(not isinstance(t, str) for t in tags):
        raise CatalogError("skill tags must be a list of strings")
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            cleaned.append(tag)
    return cleaned


def skill_by_id(session: Session, slug: str):
    """Fetch one library skill by slug (None when missing)."""
    from jalebi.db import CatalogSkill

    return session.get(CatalogSkill, slug)


def list_skills(session: Session):
    """All library skills, ordered by name."""
    from jalebi.db import CatalogSkill

    query = select(CatalogSkill).order_by(CatalogSkill.name)
    return list(session.execute(query).scalars())


def create_skill(
    session: Session,
    *,
    id: str,
    name: str,
    description: str = "",
    content: str = "",
    tags: list[str] | None = None,
):
    """Insert a new library skill (validates the definition + slug uniqueness)."""
    from jalebi.db import CatalogSkill

    slug = _normalize_id(id)
    if skill_by_id(session, slug) is not None:
        raise CatalogError(f"a skill with id {slug!r} already exists")
    tags = _normalize_tags(tags or [])
    validate_skill(
        name=name, description=description, content=content, tags=tags
    )
    row = CatalogSkill(
        id=slug,
        name=name.strip(),
        description=description.strip(),
        content=content,
        tags_json=json.dumps(tags) if tags else None,
        created_at=now(),
        updated_at=now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_skill(
    session: Session,
    slug: str,
    *,
    name: str | None = None,
    description: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
):
    """Update fields of a library skill; ``None`` leaves a field unchanged.

    Reference semantics: linked agents resolve the new content at run time —
    no fan-out needed.
    """
    row = skill_by_id(session, slug)
    if row is None:
        raise KeyError(slug)
    tags = _normalize_tags(tags) if tags is not None else _load_tags(row.tags_json)
    validate_skill(
        name=name if name is not None else row.name,
        description=description if description is not None else row.description,
        content=content if content is not None else row.content,
        tags=tags,
    )
    if name is not None:
        row.name = name.strip()
    if description is not None:
        row.description = description.strip()
    if content is not None:
        row.content = content
    row.tags_json = json.dumps(tags) if tags else None
    row.updated_at = now()
    session.commit()
    session.refresh(row)
    return row


def _load_tags(tags_json: str | None) -> list[str]:
    if not tags_json:
        return []
    try:
        tags = json.loads(tags_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, str)]


def skill_usage(session: Session, slug: str) -> dict[str, object]:
    """Which agents link a library skill (for the pre-delete warning)."""
    agents = [
        {"id": a.id, "name": a.name}
        for a in list_agents(session)
        if slug in _load_skill_ids(a.skill_ids_json)
    ]
    return {"agents": agents}


def delete_skill(session: Session, slug: str) -> bool:
    """Remove a library skill. Refused (SkillInUse) while agents link it —
    unlink first, so no agent silently loses a skill mid-flight."""
    row = skill_by_id(session, slug)
    if row is None:
        return False
    agents = skill_usage(session, slug)["agents"]
    if agents:
        raise SkillInUse(slug, agents)  # type: ignore[arg-type]
    session.delete(row)
    session.commit()
    return True


def skill_to_dict(skill) -> dict[str, object]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description or "",
        "content": skill.content or "",
        "tags": _load_tags(skill.tags_json),
        "created_at": clock.to_iso(skill.created_at),
        "updated_at": clock.to_iso(skill.updated_at),
    }
