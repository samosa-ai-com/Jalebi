"""Derived attention signals computed at read time (docs/21-phase4-plan.md T0/T2.2)."""

import json
from typing import TYPE_CHECKING, TypedDict

WAITING_INPUT_STATUSES = frozenset(
    {"done", "failed", "timed_out", "cancelled", "needs_approval"}
)

# Non-terminal task statuses (everything except the terminal set + the rare
# `waiting_review` state). Used by ``attention_for`` to decide "working" vs
# the terminal branches.
_RUNNING_STATUSES = frozenset({"queued", "running", "waiting_review"})

if TYPE_CHECKING:
    from jalebi.db import Run, Task


class PRFacts(TypedDict):
    """Normalized PR state observed by the polling observer (T2.1).

    All fields are best-effort: ``None`` means GitHub didn't tell us (404,
    ETag-304 carrying no payload, or the poller is OFF). The poller stores
    one of these per ``(repo_id, pr_number)`` and is consumed read-only by
    ``attention_for``.
    """

    ci_state: str | None  # "success" | "failure" | "pending" | None
    review_decision: str | None  # "approved" | "changes_requested" | "review_required" | None
    mergeable: bool | None
    last_seen_at: str  # ISO timestamp of the last poll that touched this fact


def _steps_json(run) -> list:
    if run is None:
        return []
    try:
        return json.loads(run.steps_json or "[]")
    except (TypeError, ValueError):
        return []


def attention_for(task: "Task", run: "Run | None", pr_facts: "PRFacts | None") -> str:
    """One-word attention status consumed by the UI (T3) + route serializers.

    Decision tree (order matters):
    1. T0 waiting-for-input (terminal run whose last message asks for the
       user) → ``"needs_you"`` — beats every other branch.
    2. Non-terminal (queued / running / waiting_review) → ``"working"``,
       except a running task whose OPEN PR already has CI failure on its
       branch → ``"needs_you"``.
    3. Terminal, no PR facts → ``"done"`` if the task itself succeeded,
       otherwise ``"needs_you"``.
    4. Terminal with facts: ci_failure → ``"needs_you"``; changes_requested
       → ``"needs_you"``; mergeable=True → ``"ready_to_merge"``; else
       ``"in_review"``.
    """
    # 1. T0 waiting-for-input always wins (cosmetic but high-signal).
    if run is not None and run.status in WAITING_INPUT_STATUSES:
        steps = _steps_json(run)
        if is_waiting_message(last_message_text(steps)):
            return "needs_you"

    # 2. Non-terminal tasks.
    if task.status in _RUNNING_STATUSES:
        if (
            task.status == "running"
            and pr_facts is not None
            and pr_facts.get("ci_state") == "failure"
        ):
            return "needs_you"
        return "working"

    # 3. Terminal, no PR facts.
    if pr_facts is None:
        return "done" if task.status == "done" else "needs_you"
    # 4. Terminal with facts.
    if pr_facts.get("ci_state") == "failure":
        return "needs_you"
    if pr_facts.get("review_decision") == "changes_requested":
        return "needs_you"
    if pr_facts.get("mergeable") is True:
        return "ready_to_merge"
    return "in_review"

_PHRASES = (
    "waiting for",
    "awaiting",
    "please confirm",
    "let me know",
    "need your",
    "your approval",
    "should i",
    "do you want",
    "want me to",
    "approval",
)


def is_waiting_message(text: str) -> bool:
    if not text or not text.strip():
        return False
    lowered = text.lower()
    if lowered.rstrip().endswith("?"):
        return True
    return any(phrase in lowered for phrase in _PHRASES)


def last_message_text(steps: list[dict]) -> str:
    for step in reversed(steps or []):
        if step.get("type") == "message" and step.get("text"):
            return str(step["text"])
    return ""
