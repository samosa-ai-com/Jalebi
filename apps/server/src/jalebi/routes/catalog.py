"""Catalog agent routes: CRUD over `/api/agents` (PRD F6)."""

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import catalog, db

bp = Blueprint("catalog", __name__, url_prefix="/api/agents")


def _skills_from_payload(payload: dict) -> list[dict[str, str]] | None:
    raw = payload.get("skills")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise catalog.CatalogError("skills must be a list of {name, content}")
    cleaned: list[dict[str, str]] = []
    for s in raw:
        if not isinstance(s, dict):
            raise catalog.CatalogError("each skill must be an object {name, content}")
        cleaned.append({"name": str(s.get("name", "")), "content": str(s.get("content", ""))})
    return cleaned


@bp.get("")
def list_agents() -> ResponseReturnValue:
    """List all catalog agents (optionally enabled-only for pickers)."""
    session = db.get_session()
    enabled_only = request.args.get("enabled") == "1"
    return jsonify([catalog.agent_to_dict(a) for a in catalog.list_agents(session, enabled_only)])


@bp.post("")
def create_agent() -> ResponseReturnValue:
    """Create a catalog agent: {id, name, kind, cli, model, personality_md, skills,
    custom_instructions, enabled}."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    if "enabled" in payload and not isinstance(payload["enabled"], bool):
        return jsonify({"error": "enabled must be a boolean"}), 400
    session = db.get_session()
    try:
        agent = catalog.create_agent(
            session,
            id=payload.get("id", ""),
            name=payload.get("name", ""),
            kind=payload.get("kind", "general"),
            cli=payload.get("cli"),
            model=payload.get("model"),
            personality_md=payload.get("personality_md", "") or "",
            skills=_skills_from_payload(payload) or [],
            custom_instructions=payload.get("custom_instructions", "") or "",
            enabled=payload.get("enabled", True),
        )
    except catalog.CatalogError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(catalog.agent_to_dict(agent)), 201


@bp.get("/<slug>")
def get_agent(slug: str) -> ResponseReturnValue:
    session = db.get_session()
    agent = catalog.agent_by_slug(session, slug)
    if agent is None:
        return jsonify({"error": "agent not found"}), 404
    return jsonify(catalog.agent_to_dict(agent))


@bp.put("/<slug>")
def update_agent(slug: str) -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    session = db.get_session()
    try:
        agent = catalog.update_agent(
            session,
            slug,
            name=payload.get("name"),
            kind=payload.get("kind"),
            cli=payload.get("cli"),
            model=payload.get("model"),
            personality_md=payload.get("personality_md"),
            skills=_skills_from_payload(payload),
            custom_instructions=payload.get("custom_instructions"),
            enabled=payload.get("enabled"),
        )
    except KeyError:
        return jsonify({"error": "agent not found"}), 404
    except catalog.CatalogError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(catalog.agent_to_dict(agent))


@bp.delete("/<slug>")
def delete_agent(slug: str) -> ResponseReturnValue:
    session = db.get_session()
    if not catalog.delete_agent(session, slug):
        return jsonify({"error": "agent not found"}), 404
    return jsonify({"deleted": slug})


@bp.get("/<slug>/usage")
def agent_usage(slug: str) -> ResponseReturnValue:
    """Where an agent is referenced (task history count + live trigger rules)."""
    session = db.get_session()
    if catalog.agent_by_slug(session, slug) is None:
        return jsonify({"error": "agent not found"}), 404
    return jsonify(catalog.agent_usage(session, slug))
