"""Skill library routes: CRUD over `/api/skills` (PRD F6)."""

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import catalog, db

bp = Blueprint("skills", __name__, url_prefix="/api/skills")


def _tags_from_payload(payload: dict) -> list[str] | None:
    raw = payload.get("tags")
    if raw is None:
        return None
    if not isinstance(raw, list) or any(not isinstance(t, str) for t in raw):
        raise catalog.CatalogError("tags must be a list of strings")
    return raw


@bp.get("")
def list_skills() -> ResponseReturnValue:
    """List all library skills (search/filter/sort happen client-side)."""
    session = db.get_session()
    return jsonify([catalog.skill_to_dict(s) for s in catalog.list_skills(session)])


@bp.post("")
def create_skill() -> ResponseReturnValue:
    """Create a library skill: {id, name, description, content, tags}."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    session = db.get_session()
    try:
        skill = catalog.create_skill(
            session,
            id=payload.get("id", ""),
            name=payload.get("name", ""),
            description=payload.get("description", "") or "",
            content=payload.get("content", "") or "",
            tags=_tags_from_payload(payload) or [],
        )
    except catalog.CatalogError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(catalog.skill_to_dict(skill)), 201


@bp.get("/<slug>")
def get_skill(slug: str) -> ResponseReturnValue:
    session = db.get_session()
    skill = catalog.skill_by_id(session, slug)
    if skill is None:
        return jsonify({"error": "skill not found"}), 404
    return jsonify(catalog.skill_to_dict(skill))


@bp.put("/<slug>")
def update_skill(slug: str) -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    session = db.get_session()
    try:
        skill = catalog.update_skill(
            session,
            slug,
            name=payload.get("name"),
            description=payload.get("description"),
            content=payload.get("content"),
            tags=_tags_from_payload(payload),
        )
    except KeyError:
        return jsonify({"error": "skill not found"}), 404
    except catalog.CatalogError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(catalog.skill_to_dict(skill))


@bp.delete("/<slug>")
def delete_skill(slug: str) -> ResponseReturnValue:
    session = db.get_session()
    try:
        deleted = catalog.delete_skill(session, slug)
    except catalog.SkillInUse as exc:
        return jsonify({"error": str(exc), "agents": exc.agents}), 409
    if not deleted:
        return jsonify({"error": "skill not found"}), 404
    return jsonify({"deleted": slug})


@bp.get("/<slug>/usage")
def skill_usage(slug: str) -> ResponseReturnValue:
    """Which agents link a library skill (for the pre-delete warning)."""
    session = db.get_session()
    if catalog.skill_by_id(session, slug) is None:
        return jsonify({"error": "skill not found"}), 404
    return jsonify(catalog.skill_usage(session, slug))
