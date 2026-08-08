"""Trigger-rule routes: CRUD over `/api/triggers` (PRD F14)."""

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from jalebi import db, webhooks

bp = Blueprint("triggers", __name__, url_prefix="/api/triggers")


def _payload_fields(payload: dict) -> dict:
    """Extract the rule fields the routes accept (agent_ids → JSON list)."""
    return {
        "repo_id": payload.get("repo_id"),
        "event": payload.get("event"),
        "action": payload.get("action"),
        "branch_filter": payload.get("branch_filter"),
        "label_filter": payload.get("label_filter"),
        "author_filter": payload.get("author_filter"),
        "agent_ids": payload.get("agent_ids"),
        "custom_instructions": payload.get("custom_instructions"),
        "enabled": payload.get("enabled"),
    }


@bp.get("")
def list_rules() -> ResponseReturnValue:
    session = db.get_session()
    repo_id = request.args.get("repo_id", type=int)
    return jsonify([webhooks.rule_to_dict(r) for r in webhooks.list_rules(session, repo_id)])


@bp.post("")
def create_rule() -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    session = db.get_session()
    try:
        rule = webhooks.create_rule(
            session,
            repo_id=payload["repo_id"],
            event=payload.get("event", ""),
            action=payload.get("action", ""),
            branch_filter=payload.get("branch_filter"),
            label_filter=payload.get("label_filter"),
            author_filter=payload.get("author_filter"),
            agent_ids=payload.get("agent_ids"),
            custom_instructions=payload.get("custom_instructions"),
            enabled=bool(payload.get("enabled", True)),
        )
    except (webhooks.WebhookError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(webhooks.rule_to_dict(rule)), 201


@bp.put("/<int:rule_id>")
def update_rule(rule_id: int) -> ResponseReturnValue:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    session = db.get_session()
    fields = _payload_fields(payload)
    fields.pop("repo_id", None)  # repo binding is immutable via this route
    fields = {k: v for k, v in fields.items() if v is not None or k == "enabled"}
    try:
        rule = webhooks.update_rule(session, rule_id, **fields)
    except KeyError:
        return jsonify({"error": "rule not found"}), 404
    except webhooks.WebhookError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(webhooks.rule_to_dict(rule))


@bp.delete("/<int:rule_id>")
def delete_rule(rule_id: int) -> ResponseReturnValue:
    session = db.get_session()
    if not webhooks.delete_rule(session, rule_id):
        return jsonify({"error": "rule not found"}), 404
    return jsonify({"deleted": rule_id})
