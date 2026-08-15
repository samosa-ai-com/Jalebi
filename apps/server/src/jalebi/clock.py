"""App wall clock — the timezone Jalebi lives in.

Jalebi is a single-owner, localhost/LAN tool. It stores and matches wall-clock
times in a configurable timezone (default: the machine's local zone) so cron
cadences and displayed timestamps follow a clock the owner understands. The
earlier UTC-based design made the screening scheduler match cron against UTC —
a "1:30 AM" cadence never fired for a non-UTC owner — and every displayed
timestamp was shifted by the machine's UTC offset.

The zone is a module global (``set_zone``), set at startup after settings are
seeded and whenever the ``timezone`` setting is saved. Module-global so
SQLAlchemy column defaults (which have no session) can call ``now()``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_zone: str = "local"


def set_zone(name: str | None) -> None:
    """Set the app timezone: ``"local"`` (system zone) or an IANA name.

    An unknown IANA name raises ``ZoneInfoNotFoundError`` so the settings
    validator can reject it at save time.
    """
    global _zone
    cleaned = (name or "").strip() or "local"
    if cleaned != "local":
        ZoneInfo(cleaned)
    _zone = cleaned


def zone_name() -> str:
    return _zone


def _tz() -> tzinfo:
    if _zone == "local":
        return datetime.now().astimezone().tzinfo or UTC
    try:
        return ZoneInfo(_zone)
    except ZoneInfoNotFoundError:  # defensive: a stale zone must never crash
        return datetime.now().astimezone().tzinfo or UTC


def now() -> datetime:
    """Naive wall-clock ``now`` in the configured timezone (SQLite-friendly)."""
    return datetime.now(_tz()).replace(tzinfo=None)


def _offset_suffix() -> str:
    """The configured zone's current UTC offset as an ISO suffix (e.g. ``+05:30``)."""
    offset = datetime.now(_tz()).utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, mm = divmod(total // 60, 60)
    return f"{sign}{hh:02d}:{mm:02d}"


def to_iso(dt: datetime) -> str:
    """Serialize a naive wall-clock datetime with the zone's offset, so clients
    (the browser) parse it as a real instant and render it in their own local
    time.

    For a configured **named** zone the offset is resolved per-datetime via
    ``ZoneInfo``, so a stored value from across a DST boundary carries that
    date's correct offset (winter ``-05:00`` vs summer ``-04:00``, say). In
    ``"local"`` mode the system tzinfo is a fixed-offset view, so the current
    offset is used — a value stored in a different DST season of a DST system
    zone gets today's offset (a ±1h cosmetic skew for the machine's own zone;
    acceptable for a personal tool).
    """
    tz = _tz()
    if isinstance(tz, ZoneInfo):
        return dt.replace(tzinfo=tz).isoformat()
    return dt.isoformat() + _offset_suffix()
