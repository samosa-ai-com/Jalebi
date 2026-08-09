"""Webhook listener service (PRD F14): signature verify, dedup, rule matching, dispatch.

Triggering is a first-class **webhook-pushed** mechanism (not polling). A
``POST /webhook`` delivery is:
1. verified against ``X-Hub-Signature-256`` (when a ``webhook_secret`` is set),
2. **idempotently deduped** on ``X-GitHub-Delivery`` (re-deliveries never
   double-run a task),
3. matched against the repo's enabled **trigger rules** (event + scope filters),
4. dispatched — ``start_review`` creates reviewer tasks, ``triage_issue`` an
   issue_fix task, ``create_task`` a freeform task, ``rerun_review`` re-enqueues
   the PR's reviewer tasks — and recorded in ``event_deliveries`` for the log +
   replay.

The webhook path does no slow work (no GitHub context fetches): validation +
dedup + rule matching + enqueue only, so webhook → task-start latency is
sub-second (PRD §13).
"""

import hashlib
import hmac
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import EventDelivery, Repo, ReviewAssignment, Task, TriggerRule, utcnow

RULE_ACTIONS = ("start_review", "triage_issue", "create_task", "rerun_review")

DELIVERY_STATUSES = ("received", "matched", "ignored", "failed")


class WebhookError(ValueError):
    """Raised for malformed deliveries or invalid rules."""


# -- signature -------------------------------------------------------------


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Verify ``X-Hub-Signature-256`` over the raw body with ``secret``.

    When no secret is configured the check is disabled (the delivery is accepted
    — only safe on a localhost-only install, documented in the UI). Otherwise a
    missing/malformed signature is rejected.
    """
    if not secret:
        return True
    if not header:
        return False
    prefix = "sha256="
    if not header.startswith(prefix):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len(prefix):], expected)


# -- deliveries (idempotency + log) ---------------------------------------


def delivery_exists(session: Session, github_delivery_id: str) -> bool:
    return (
        session.execute(
            select(EventDelivery).where(
                EventDelivery.github_delivery_id == github_delivery_id
            )
        ).scalar_one_or_none()
        is not None
    )


def record_delivery(
    session: Session,
    *,
    github_delivery_id: str,
    event: str,
    action: str | None,
    repo_id: int | None,
    repo_full_name: str | None,
    payload_json: str,
    status: str,
    result: dict | None = None,
) -> EventDelivery:
    if status not in DELIVERY_STATUSES:
        raise WebhookError(f"invalid delivery status: {status}")
    row = EventDelivery(
        github_delivery_id=github_delivery_id,
        event=event,
        action=action,
        repo_id=repo_id,
        repo_full_name=repo_full_name,
        payload_json=payload_json,
        received_at=utcnow(),
        status=status,
        result=json.dumps(result) if result else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_deliveries(session: Session, limit: int = 100) -> list[EventDelivery]:
    return list(
        session.execute(
            select(EventDelivery).order_by(EventDelivery.id.desc()).limit(limit)
        ).scalars()
    )


def delivery_to_dict(delivery: EventDelivery) -> dict[str, object]:
    return {
        "id": delivery.id,
        "github_delivery_id": delivery.github_delivery_id,
        "event": delivery.event,
        "action": delivery.action,
        "repo_id": delivery.repo_id,
        "repo_full_name": delivery.repo_full_name,
        "received_at": delivery.received_at.isoformat(),
        "status": delivery.status,
        "result": json.loads(delivery.result) if delivery.result else None,
    }


# -- trigger rules ---------------------------------------------------------


def rule_by_id(session: Session, rule_id: int) -> TriggerRule | None:
    return session.get(TriggerRule, rule_id)


def list_rules(session: Session, repo_id: int | None = None) -> list[TriggerRule]:
    query = select(TriggerRule).order_by(TriggerRule.id.asc())
    if repo_id is not None:
        query = query.where(TriggerRule.repo_id == repo_id)
    return list(session.execute(query).scalars())


def create_rule(
    session: Session,
    *,
    repo_id: int,
    event: str,
    action: str,
    branch_filter: str | None = None,
    label_filter: list[str] | None = None,
    author_filter: str | None = None,
    agent_ids: list[str] | None = None,
    custom_instructions: str | None = None,
    enabled: bool = True,
) -> TriggerRule:
    repo = session.get(Repo, repo_id)
    if repo is None:
        raise WebhookError(f"repo {repo_id} not found")
    if not event or not event.strip():
        raise WebhookError("event is required")
    if action not in RULE_ACTIONS:
        raise WebhookError(f"invalid action: {action!r}")
    if action == "start_review" and not agent_ids:
        raise WebhookError("start_review requires agent_ids (catalog reviewers)")
    row = TriggerRule(
        repo_id=repo_id,
        event=event,
        action=action,
        branch_filter=branch_filter or None,
        label_filter=json.dumps(label_filter) if label_filter else None,
        author_filter=author_filter or None,
        agent_ids_json=json.dumps(agent_ids) if agent_ids else None,
        custom_instructions=custom_instructions or None,
        enabled=enabled,
        created_at=utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_rule(session: Session, rule_id: int, **fields) -> TriggerRule:
    row = rule_by_id(session, rule_id)
    if row is None:
        raise KeyError(rule_id)
    if "event" in fields and fields["event"] is not None:
        row.event = fields["event"]
    if "action" in fields and fields["action"] is not None:
        if fields["action"] not in RULE_ACTIONS:
            raise WebhookError(f"invalid action: {fields['action']!r}")
        row.action = fields["action"]
    if "branch_filter" in fields:
        row.branch_filter = fields["branch_filter"] or None
    if "label_filter" in fields:
        row.label_filter = (
            json.dumps(fields["label_filter"]) if fields["label_filter"] else None
        )
    if "author_filter" in fields:
        row.author_filter = fields["author_filter"] or None
    if "agent_ids" in fields:
        row.agent_ids_json = (
            json.dumps(fields["agent_ids"]) if fields["agent_ids"] else None
        )
    if "custom_instructions" in fields:
        row.custom_instructions = fields["custom_instructions"] or None
    if "enabled" in fields:
        row.enabled = bool(fields["enabled"])
    session.commit()
    session.refresh(row)
    return row


def delete_rule(session: Session, rule_id: int) -> bool:
    row = rule_by_id(session, rule_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def rule_to_dict(rule: TriggerRule) -> dict[str, object]:
    return {
        "id": rule.id,
        "repo_id": rule.repo_id,
        "event": rule.event,
        "action": rule.action,
        "branch_filter": rule.branch_filter,
        "label_filter": json.loads(rule.label_filter) if rule.label_filter else [],
        "author_filter": rule.author_filter,
        "agent_ids": json.loads(rule.agent_ids_json) if rule.agent_ids_json else [],
        "custom_instructions": rule.custom_instructions,
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
    }


# -- matching --------------------------------------------------------------


def event_context(payload: dict) -> dict:
    """Normalize a webhook payload into a small matchable context.

    Works for ``pull_request`` and ``issues`` events (the payload shapes Jalebi
    triggers on); unrelated events produce an empty context.
    """
    if not isinstance(payload, dict):
        return {}
    pr = payload.get("pull_request") or {}
    issue = payload.get("issue") or {}
    head = (pr.get("head") or {}).get("ref")
    base = (pr.get("base") or {}).get("ref")
    # A push event has no pull_request; the branch is in payload["ref"]
    # ("refs/heads/<branch>"), which we expose as the head ref for matching.
    push_ref = (payload.get("ref") or "").replace("refs/heads/", "")
    return {
        "pr_number": pr.get("number"),
        "issue_number": issue.get("number"),
        "title": pr.get("title") or issue.get("title"),
        "body": pr.get("body") or issue.get("body"),
        "base_ref": base or (push_ref if push_ref else None),
        "head_ref": head or (push_ref if push_ref else None),
        "author": (pr.get("user") or issue.get("user") or {}).get("login"),
        "labels": [
            label.get("name")
            for label in (pr.get("labels") or issue.get("labels") or [])
            if isinstance(label, dict)
        ],
    }


def repo_full_name_from_payload(payload: dict) -> str | None:
    repo = payload.get("repository") if isinstance(payload, dict) else None
    if not isinstance(repo, dict):
        return None
    return repo.get("full_name")


def matching_rules(
    session: Session,
    repo: Repo,
    event_key: str,
    context: dict,
) -> list[TriggerRule]:
    """Enabled rules for ``repo`` whose event + scope filters match."""
    rules = list_rules(session, repo.id)
    matched: list[TriggerRule] = []
    for rule in rules:
        if not rule.enabled or rule.event != event_key:
            continue
        if not _scope_matches(rule, context):
            continue
        matched.append(rule)
    return matched


def _scope_matches(rule: TriggerRule, context: dict) -> bool:
    if rule.branch_filter:
        head = context.get("head_ref")
        base = context.get("base_ref")
        # Match the filter against either ref (a rule like 'main' fires for PRs
        # into main regardless of the head branch).
        if rule.branch_filter not in (head, base):
            return False
    if rule.author_filter:
        author = context.get("author")
        if not author or author != rule.author_filter:
            return False
    if rule.label_filter:
        required = json.loads(rule.label_filter) if rule.label_filter else []
        labels = set(context.get("labels") or [])
        if not required or not all(label in labels for label in required):
            return False
    return True


# -- dispatch --------------------------------------------------------------


def dispatch_rule(
    session: Session,
    queue,
    rule: TriggerRule,
    repo: Repo,
    context: dict,
    masker=None,
) -> list[dict]:
    """Act on a matched rule; returns a summary list of created/re-enqueued work.

    ``queue`` is the TaskQueue (for enqueueing). ``masker`` masks the task
    prompt at creation. No GitHub API calls happen here (webhook path stays
    fast); a rule may create tasks that the queue then runs like manual ones.
    """
    if rule.action == "start_review":
        return _dispatch_start_review(session, queue, rule, repo, context, masker)
    if rule.action == "triage_issue":
        return _dispatch_triage_issue(session, queue, rule, repo, context, masker)
    if rule.action == "create_task":
        return _dispatch_create_task(session, queue, rule, repo, context, masker)
    if rule.action == "rerun_review":
        return _dispatch_rerun_review(session, queue, repo, context)
    return []


def _dispatch_start_review(session, queue, rule, repo, context, masker) -> list[dict]:
    pr_number = context.get("pr_number")
    if not pr_number:
        return []
    from jalebi import reviews

    agent_ids = json.loads(rule.agent_ids_json) if rule.agent_ids_json else []
    # Never create a second reviewer task for an agent already assigned to this
    # PR (e.g. on replay of a delivery, or a re-triggered rule). Existing
    # assignments are left as-is.
    existing = {
        a.agent_id
        for a in reviews.assignments_for_pr(session, repo.id, int(pr_number))
    }
    fresh = [str(a) for a in agent_ids if str(a) not in existing]
    if not fresh:
        return []
    created = reviews.assign_reviewers(
        session, repo, int(pr_number), fresh, queue=queue, masker=masker
    )
    summary = []
    for task in created:
        summary.append({"type": "review", "task_id": task.id, "agent_id": task.agent_id})
    return summary


def _dispatch_triage_issue(session, queue, rule, repo, context, masker) -> list[dict]:
    issue_number = context.get("issue_number")
    if not issue_number:
        return []
    from jalebi import tasks

    prompt = (rule.custom_instructions or "").strip() or f"Fix issue #{issue_number}."
    agent_ids = json.loads(rule.agent_ids_json) if rule.agent_ids_json else []
    # The issue body/title are UNTRUSTED webhook payload and may contain secrets
    # (a PAT pasted in an issue). Mask them before they reach the task context
    # (which flows into the worktree AGENTS.md).
    title_raw = str(context.get("title") or "")
    body_raw = str(context.get("body") or "")
    masked_title = masker(title_raw) if masker else title_raw
    masked_body = masker(body_raw) if masker else body_raw
    try:
        task = tasks.create_task(
            session,
            type_="issue_fix",
            repo_id=repo.id,
            prompt=prompt,
            agent_id=str(agent_ids[0]) if agent_ids else None,
            pat_name=repo.pat_name,
            issues=[int(issue_number)],
            context={
                "issues": [
                    {
                        "number": int(issue_number),
                        "title": masked_title,
                        "body": masked_body,
                        "html_url": "",
                    }
                ]
            },
            publish_mode="auto",
            masker=masker,
        )
    except Exception as exc:
        return [{"type": "error", "error": str(exc)}]
    queue.enqueue(task.id)
    return [{"type": "issue_fix", "task_id": task.id}]


def _dispatch_create_task(session, queue, rule, repo, context, masker) -> list[dict]:
    from jalebi import tasks

    prompt = (rule.custom_instructions or "").strip()
    if not prompt:
        return [{"type": "error", "error": "create_task requires custom_instructions"}]
    agent_ids = json.loads(rule.agent_ids_json) if rule.agent_ids_json else []
    try:
        task = tasks.create_task(
            session,
            type_="freeform",
            repo_id=repo.id,
            prompt=prompt,
            agent_id=str(agent_ids[0]) if agent_ids else None,
            pat_name=repo.pat_name,
            publish_mode="manual",
            masker=masker,
        )
    except Exception as exc:
        return [{"type": "error", "error": str(exc)}]
    queue.enqueue(task.id)
    return [{"type": "freeform", "task_id": task.id}]


def _dispatch_rerun_review(session, queue, repo, context) -> list[dict]:
    pr_number = context.get("pr_number")
    if not pr_number:
        return []
    # Re-enqueue the PR's existing reviewer tasks (a fresh review pass per task).
    assignments = list(
        session.execute(
            select(ReviewAssignment).where(
                ReviewAssignment.repo_id == repo.id,
                ReviewAssignment.pr_number == int(pr_number),
            )
        ).scalars()
    )
    summary = []
    for assignment in assignments:
        task = session.get(Task, assignment.task_id)
        # Re-enqueue only terminal/done statuses. ``needs_approval`` is excluded
        # by virtue of not being in this inclusion list — a reviewer task that
        # is awaiting manual publish (a fresh review pass would spawn a new
        # agent run while the publish hangs waiting for the owner). The test
        # ``test_dispatch_rerun_review_skips_needs_approval`` in
        # ``test_webhooks.py`` pins this contract; do NOT add
        # ``needs_approval`` to the list below.
        if task is None or task.status not in (
            "done",
            "failed",
            "timed_out",
            "interrupted",
            "cancelled",
        ):
            continue
        task.status = "queued"
        task.updated_at = utcnow()
        queue.enqueue(task.id)
        summary.append({"type": "rerun_review", "task_id": task.id})
    session.commit()
    return summary
