"""Key-value settings stored in the ``settings`` table, with code-side defaults.

Every configuration key is materialized as a row in the ``settings`` table
(``seed_defaults`` runs at startup), so settings are **persistent** — they
survive restarts and are never held in memory. ``get_setting`` falls back to the
code default only when a key has no stored row (e.g. a brand-new key added by a
code update before the next restart).
"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import Setting

DEFAULTS: dict[str, object] = {
    # Phase 4 T4.2 — auto-nudge when CI/review state changes (default OFF).
    "auto_nudge": False,
    # Phase 4 T6 — IDE connector (open a task's worktree in the configured
    # IDE). Empty = feature off. Validated: bare executable name or absolute
    # path, no whitespace/shell metacharacters, must resolve via shutil.which
    # (or exist on disk if absolute). ``ide_name`` is a display label only.
    "ide_command": "",
    "ide_name": "",
    "concurrency": 4,
    "auto_publish": True,
    # Merged ntfy endpoint: a bare topic ("my-jalebi") or a full URL
    # ("https://ntfy.example.com/room"). ntfy_url was folded into this.
    "ntfy_topic": "",
    "default_timeout_minutes": 60,
    # Auto-recovery on failed/timed_out/stalled runs. Unbounded by design: each
    # run is bounded by its own (escalating) timeout and terminal/progress
    # notifications keep the owner informed. Stall runs restart fresh (a wedged
    # session re-hangs); timeouts/other failures resume the session with
    # ``continue_prompt``.
    "retry_policy": {
        "auto_retry": True,
        "continue_prompt": "continue",
        "timeout_multiplier": 2,
        "max_timeout_minutes": 180,
    },
    # No-output threshold before a run is declared stalled (and auto-recovered).
    "stall_timeout_seconds": 600,
    "secret_patterns": [],
    "artifact_ttl_days": 7,
    # Default backend + model used when an operation doesn't pick its own.
    # Every task/follow-up/screen/agent form has its own Backend + Model selects
    # (which is the primary path) — these are only the fallbacks (triggered
    # tasks, legacy rows, unpinned screens). The default model is applied only
    # when the resolved backend == default_backend (a single model can't be
    # valid for every backend); other backends use the CLI's own default when
    # unpinned. ``default_model`` is required (validator rejects empty) so a
    # default is always configured.
    "default_backend": "opencode",
    "default_model": "",
    # Owner override of each adapter's curated model list: {cli: [model names]}.
    # Consumed by GET /api/models; lets the owner pin the task-form model dropdown
    # (e.g. for a custom provider) without touching adapter code. {} = adapters'
    # own defaults.
    "adapter_model_lists": {},
    "notify_on_done": True,
    "notify_on_failed": True,
    "notify_on_progress": True,
    "notify_on_needs_approval": True,
    "notify_progress_interval_minutes": 30,
    # Public base URL GitHub can reach to deliver webhooks (e.g. a tunnel such
    # as cloudflared/ngrok). Empty = not exposed → webhooks can't be delivered.
    "webhook_url": "",
    # Optional HMAC secret verified against X-Hub-Signature-256 on POST /webhook.
    # Empty = signature check disabled (only safe on a localhost-only install).
    "webhook_secret": "",
    # The timezone the app's wall clock lives in: "local" (the machine's system
    # zone) or an IANA name (e.g. "Asia/Kolkata"). Drives the screening cron
    # scheduler (cadence = wall clock in this zone) and every stored/displayed
    # timestamp.
    "timezone": "local",
}

SETTING_KEYS = tuple(DEFAULTS)

# Sentinel returned by GET /api/settings for write-only secrets (webhook_secret):
# the value is never exposed, only a placeholder that the UI treats as
# "unchanged" on save (an empty string clears it).
SECRET_MASK = "••••••••"


def seed_defaults(session: Session) -> int:
    """Persist every default that has no stored row yet. Returns the count added.

    Runs at startup so the ``settings`` table always contains every
    configuration key — settings are explicit, inspectable, and survive
    restarts. Existing (user-modified) values are never overwritten, and a key
    added by a later code update gets seeded on the next startup.
    """
    existing = {
        row.key
        for row in session.execute(select(Setting)).scalars()
    }
    added = 0
    for key, value in DEFAULTS.items():
        if key not in existing:
            session.add(Setting(key=key, value=json.dumps(value)))
            added += 1
    if added:
        session.commit()
    return added


def get_setting(session: Session, key: str) -> object:
    """Return the stored value for ``key``, falling back to the code default."""
    row = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None:
        return DEFAULTS.get(key)
    return json.loads(row.value)


def set_setting(session: Session, key: str, value: object) -> None:
    """Upsert ``key`` with a JSON-encoded ``value``."""
    encoded = json.dumps(value)
    row = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None:
        session.add(Setting(key=key, value=encoded))
    else:
        row.value = encoded
    session.commit()
