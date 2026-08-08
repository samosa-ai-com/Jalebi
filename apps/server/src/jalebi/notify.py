"""Push notifications via ntfy (best-effort, never raises into the caller).

The merged ``ntfy_topic`` setting is either a bare topic name (sent to the
default ``https://ntfy.sh`` server) or a full URL to a self-hosted ntfy
instance (``https://ntfy.example.com/room``).

Publishing uses ntfy's **JSON publishing** mode (docs.ntfy.sh/publish/): we POST
a JSON body to the **server root** with ``topic`` inside the body, which lets us
send Markdown-formatted messages, priorities, tags, a click action and action
buttons — instead of the raw-JSON-as-message you get from posting JSON to the
topic URL itself. Every send is best-effort: a failure is logged and returned to
the caller, never raised, so a dead ntfy server can never break the task queue.
"""

import logging
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from jalebi.settings import get_setting

logger = logging.getLogger(__name__)

DEFAULT_NTFY_URL = "https://ntfy.sh"
TIMEOUT_SECONDS = 10.0

# ntfy uses GitHub-style emoji shortcodes as tags (rendered as emoji badges).
TAGS_OK = "white_check_mark"
TAGS_FAIL = "x"
TAGS_CLOCK = "alarm_clock"
TAGS_APPROVE = "hand"

MASK = "***"


def resolve(topic: str | None) -> tuple[str, str] | None:
    """Split the merged ``ntfy_topic`` setting into ``(base_url, topic)``.

    A bare topic (``my-jalebi``) → ``("https://ntfy.sh", "my-jalebi")``; a full
    URL (``https://ntfy.example.com/room``) → the server root + the path as the
    topic. Empty/unset returns ``None``.
    """
    value = (topic or "").strip()
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        base = f"{parsed.scheme}://{parsed.netloc}"
        topic_name = parsed.path.strip("/")
        if not topic_name:
            return None
        return base, topic_name
    return DEFAULT_NTFY_URL, value


def send(
    session: Session,
    title: str,
    message: str,
    *,
    tags: str | list[str] | None = None,
    priority: int | None = None,
    click: str | None = None,
    actions: list[dict[str, object]] | None = None,
    masker: Callable[[str], str] | None = None,
) -> tuple[bool, str | None]:
    """Push an ntfy notification. Returns ``(ok, error)``; never raises.

    Sends a JSON body to the server root with ``topic`` inside it (JSON
    publishing mode). The title and message are masked before being sent (PATs
    + secret patterns), so a stray secret can never reach the push channel.
    """
    resolved = resolve(str(get_setting(session, "ntfy_topic") or ""))
    if resolved is None:
        return False, "ntfy_topic is not configured"
    base, topic = resolved
    if masker is not None:
        title = masker(title)
        message = masker(message)
    payload: dict[str, object] = {
        "topic": topic,
        "title": title,
        "message": message,
        "markdown": True,  # bold/links/lists render instead of raw text
    }
    if tags:
        payload["tags"] = tags if isinstance(tags, list) else [tags]
    if priority is not None:
        payload["priority"] = priority
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = actions
    try:
        resp = httpx.post(base, json=payload, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("ntfy notification failed: %s", exc)
        return False, f"ntfy request failed: {exc}"
    if resp.status_code not in (200, 201):
        logger.warning("ntfy notification rejected: HTTP %s", resp.status_code)
        return False, f"ntfy rejected the notification: HTTP {resp.status_code}"
    return True, None


def masked(value: str) -> str:
    """A short preview of a stored value for display (never the full value)."""
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + MASK
    return value[:4] + MASK + value[-2:]
