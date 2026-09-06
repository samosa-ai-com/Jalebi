"""Webhook listener + deliveries routes (PRD F14).

``POST /webhook`` is the GitHub webhook endpoint: verify signature, dedup on
``X-GitHub-Delivery``, match trigger rules, dispatch, and record the delivery.
It is deliberately exempt from the UI Basic-auth gate (the webhook signature is
its auth — see ``app._basic_auth_gate``).
"""

import json
import logging

from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from jalebi import db, masking, nudger, secrets, settings, webhooks
from jalebi.config import Config
from jalebi.db import Repo
from jalebi.queue import TaskQueue

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


def _queue() -> TaskQueue:
    return current_app.config["JALEBI_QUEUE"]


def _config() -> Config:
    return current_app.config["JALEBI_CONFIG"]


def _masker(session):
    patterns = settings.get_setting(session, "secret_patterns") or []
    patterns = [str(p) for p in patterns] if isinstance(patterns, list) else []
    return masking.build_masker(secrets.all_token_values(_config()), patterns)


@bp.post("/webhook")
def webhook() -> ResponseReturnValue:
    """Receive a GitHub webhook delivery (signature-verified, deduped, dispatched)."""
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    event = request.headers.get("X-GitHub-Event", "")
    signature = request.headers.get("X-Hub-Signature-256")

    session = db.get_session()
    body = request.get_data()  # raw bytes for signature verification
    secret = str(settings.get_setting(session, "webhook_secret") or "")
    # A password-protected (possibly tunneled) install must not accept unsigned
    # deliveries: the webhook signature is the only auth GitHub can provide.
    if _config().password and not secret:
        return jsonify(
            {"error": "the UI is password-protected and no webhook_secret is set — "
             "set one in Settings before enabling webhook deliveries"}
        ), 403
    if not verify_or_401(session, secret, body, signature):
        return jsonify({"error": "invalid webhook signature"}), 403

    if not delivery_id:
        return jsonify({"error": "missing X-GitHub-Delivery header"}), 400
    if not event:
        return jsonify({"error": "missing X-GitHub-Event header"}), 400

    # Reserve the delivery row BEFORE any dispatch so concurrent re-deliveries of
    # the same X-GitHub-Delivery can never both run the rules (the UNIQUE
    # constraint makes the loser a no-op instead of a duplicate-task race).
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "delivery body is not valid JSON"}), 400

    action = payload.get("action") if isinstance(payload, dict) else None
    full_name = webhooks.repo_full_name_from_payload(payload)
    reserved = _reserve_delivery(
        session, delivery_id, event, action, full_name, payload
    )
    if reserved is False:
        return jsonify({"ok": True, "deduplicated": True})
    if reserved is None:
        return jsonify({"error": "delivery body is not valid JSON"}), 400

    repo = None
    if full_name:
        repo = session.execute(
            select(Repo).where(Repo.full_name == full_name, Repo.connected.is_(True))
        ).scalar_one_or_none()
    repo_id = repo.id if repo is not None else None

    # If the repo isn't connected (or the event is unrelated), record + ignore.
    if repo is None:
        _complete_delivery(
            session, reserved, repo_id=None, status="ignored",
            result={"reason": "repo not connected"},
        )
        return jsonify({"ok": True, "matched": False})

    # Phase 4 T4.2 — best-effort auto-nudge on CI/review signals. This is
    # independent of trigger rules (it keys off the task branch in the
    # payload), so it runs whether or not any rule matches. Never affects
    # the webhook response. Manual replays (replay()) intentionally skip
    # this — replay re-runs rules, not live signals.
    try:
        nudger.on_webhook(
            session, _queue(), event, payload if isinstance(payload, dict) else {},
            repo_id=repo.id,
        )
    except Exception:  # noqa: BLE001 - webhook must always answer 200
        logger.exception("auto-nudge hook failed for delivery %s", delivery_id)

    event_key = f"{event}.{action}" if action else event
    context = webhooks.event_context(payload)
    rules = webhooks.matching_rules(session, repo, event_key, context)

    if not rules:
        _complete_delivery(
            session, reserved, repo_id=repo_id, status="ignored",
            result={"reason": "no matching trigger rule"},
        )
        return jsonify({"ok": True, "matched": False})

    results = []
    masker = _masker(session)
    for rule in rules:
        try:
            summary = webhooks.dispatch_rule(
                session, _queue(), rule, repo, context, masker=masker
            )
        except Exception as exc:  # one bad rule must not fail the whole delivery
            summary = [{"type": "error", "error": str(exc)}]
        results.append({"rule_id": rule.id, "action": rule.action, "work": summary})

    # A delivery only counts as "matched" when at least one rule actually
    # produced non-error work (created/enqueued something). When every rule's
    # work is empty or composed only of error entries (e.g. triage_issue/
    # create_task rule fired but tasks.create_task raised on a disconnected
    # repo, oversized prompt, or missing custom_instructions), record the
    # delivery as "failed" so the Triggers page Delivery log doesn't lie about
    # success. The HTTP response stays 200 (GitHub already accepted the
    # delivery; we never want to make GitHub retry on app-side failures).
    any_work = _any_rule_produced_work(results)
    delivery_status = "matched" if any_work else "failed"
    _complete_delivery(
        session, reserved, repo_id=repo_id, status=delivery_status,
        result={"rules": results},
    )
    return jsonify({"ok": True, "matched": any_work, "results": results})


def _any_rule_produced_work(results: list[dict]) -> bool:
    """True iff at least one rule's ``work`` contains a non-error entry.

    A rule with an empty ``work`` list, or whose ``work`` is composed entirely
    of ``{"type": "error", ...}`` items, is treated as no-work — the
    dispatcher tried but produced nothing usable.
    """
    for r in results:
        for item in r.get("work") or []:
            if isinstance(item, dict) and item.get("type") != "error":
                return True
    return False


def _reserve_delivery(session, delivery_id, event, action, full_name, payload):
    """Insert the delivery row (status 'received') atomically; return the row.

    Returns ``False`` when the delivery was already reserved (re-delivery/race),
    ``None`` when the payload is not a dict, else the reserved EventDelivery row.
    """
    if not isinstance(payload, dict):
        return None
    row = db.EventDelivery(
        github_delivery_id=delivery_id,
        event=event,
        action=action,
        repo_id=None,  # resolved after reservation
        repo_full_name=full_name,
        payload_json=json.dumps(payload),
        received_at=db.now(),
        status="received",
    )
    session.add(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        return False  # UNIQUE(github_delivery_id) — a concurrent delivery won
    session.refresh(row)
    return row


def _complete_delivery(
    session, delivery: db.EventDelivery, *, repo_id, status, result
) -> None:
    """Update a reserved delivery with the outcome."""
    delivery.repo_id = repo_id
    delivery.status = status
    delivery.result = json.dumps(result) if result else None
    session.commit()


def verify_or_401(session, secret: str, body: bytes, signature: str | None) -> bool:
    return webhooks.verify_signature(secret, body, signature)


@bp.get("/api/webhook/status")
def webhook_status() -> ResponseReturnValue:
    """Webhook reachability/registration status for the Triggers page."""
    session = db.get_session()
    url = str(settings.get_setting(session, "webhook_url") or "")
    secret = str(settings.get_setting(session, "webhook_secret") or "")
    repos = list(
        session.execute(select(Repo).where(Repo.connected.is_(True))).scalars()
    )
    return jsonify(
        {
            "url": url,
            "secret_set": bool(secret),
            "reachable": bool(url),
            "repos": [
                {
                    "id": r.id,
                    "full_name": r.full_name,
                    "webhook_registered": r.webhook_registered,
                    "poll_fallback": r.poll_fallback,
                }
                for r in repos
            ],
        }
    )


@bp.get("/api/webhooks/deliveries")
def deliveries() -> ResponseReturnValue:
    session = db.get_session()
    return jsonify([webhooks.delivery_to_dict(d) for d in webhooks.list_deliveries(session)])


@bp.post("/api/webhooks/deliveries/<int:delivery_id>/replay")
def replay(delivery_id: int) -> ResponseReturnValue:
    """Re-run a stored delivery through the matcher (a manual re-delivery).

    Idempotent: a rule that already created work in the *original* delivery is
    not dispatched again (its stored work is returned as a no-op), so replaying
    never creates duplicate tasks/PRs — for `start_review` *and* the
    task-creating actions (`triage_issue`/`create_task`). A rule added *after*
    the delivery still fires.
    """
    session = db.get_session()
    delivery = session.get(db.EventDelivery, delivery_id)
    if delivery is None:
        return jsonify({"error": "delivery not found"}), 404
    payload = json.loads(delivery.payload_json)
    full_name = delivery.repo_full_name
    repo = None
    if full_name:
        repo = session.execute(
            select(Repo).where(Repo.full_name == full_name, Repo.connected.is_(True))
        ).scalar_one_or_none()
    if repo is None:
        return jsonify({"error": "repo no longer connected"}), 409

    # Rules already dispatched by this delivery in its original run. result
    # shape: {"rules": [{"rule_id", "action", "work": [...]}]}.
    already_dispatched: set[int] = set()
    prior_rules = []
    if delivery.result:
        try:
            prior_rules = json.loads(delivery.result).get("rules") or []
        except (ValueError, TypeError):
            prior_rules = []
    for entry in prior_rules:
        if isinstance(entry, dict) and isinstance(entry.get("rule_id"), int):
            # Treat the rule as already dispatched whenever its stored result
            # carries a ``work`` key — including empty lists and error-only
            # lists. The previous ``if entry.get("work"):`` predicate treated
            # ``work == []`` as not-yet-dispatched and re-ran dispatch_rule on
            # every replay (no-op rules like ``rerun_review`` with nothing to
            # re-enqueue would loop forever).
            if "work" in entry:
                already_dispatched.add(entry["rule_id"])

    event_key = f"{delivery.event}.{delivery.action}" if delivery.action else delivery.event
    context = webhooks.event_context(payload)
    rules = webhooks.matching_rules(session, repo, event_key, context)
    results = []
    masker = _masker(session)
    for rule in rules:
        if rule.id in already_dispatched:
            results.append(
                {
                    "rule_id": rule.id,
                    "action": rule.action,
                    "work": [],
                    "note": "already dispatched — skipped",
                }
            )
            continue
        try:
            summary = webhooks.dispatch_rule(
                session, _queue(), rule, repo, context, masker=masker
            )
        except Exception as exc:
            summary = [{"type": "error", "error": str(exc)}]
        results.append({"rule_id": rule.id, "action": rule.action, "work": summary})
    return jsonify({"matched": len(rules), "results": results})
