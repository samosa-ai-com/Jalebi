"""Key-value settings stored in the ``settings`` table, with code-side defaults."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from jalebi.db import Setting

DEFAULTS: dict[str, object] = {
    "concurrency": 4,
    "auto_publish": True,
    "ntfy_topic": "",
    "default_timeout_minutes": 30,
    "retry_policy": {"auto_retry": False},
    "secret_patterns": [],
    "artifact_ttl_days": 7,
}

SETTING_KEYS = tuple(DEFAULTS)


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
