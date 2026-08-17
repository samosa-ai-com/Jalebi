"""Derived attention signals computed at read time (docs/21-phase4-plan.md T0/T2.2)."""

WAITING_INPUT_STATUSES = frozenset(
    {"done", "failed", "timed_out", "cancelled", "needs_approval"}
)

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
