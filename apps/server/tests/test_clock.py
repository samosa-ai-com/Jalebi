"""Clock tests: the app wall clock in the configured timezone (PRD F10, Step 53+).

The screening scheduler matches cron against this wall clock and every stored/
serialized timestamp follows it, so the zone is behaviorally important.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

import pytest

from jalebi import clock


@pytest.fixture(autouse=True)
def _restore_zone(monkeypatch):
    """These tests mutate the module-global zone — always restore it."""
    yield
    clock.set_zone("local")


def test_default_zone_is_system_local() -> None:
    assert clock.zone_name() == "local"
    now = clock.now()
    assert now.tzinfo is None  # SQLite-friendly naive
    assert abs((now - datetime.now()).total_seconds()) < 5


def test_set_zone_iana_changes_wall_clock() -> None:
    clock.set_zone("Asia/Kolkata")
    assert clock.zone_name() == "Asia/Kolkata"
    # IST is fixed UTC+5:30 — the naive local value must match UTC shifted.
    local = clock.now()
    utc = datetime.now(UTC).replace(tzinfo=None)
    shifted = local - timedelta(hours=5, minutes=30)
    assert abs((shifted - utc).total_seconds()) < 5


def test_set_zone_rejects_unknown_name() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        clock.set_zone("Mars/Olympus")


def test_to_iso_appends_configured_zone_offset() -> None:
    clock.set_zone("Asia/Kolkata")
    iso = clock.to_iso(clock.now())
    assert iso.endswith("+05:30")


def test_to_iso_parses_back_to_an_instant() -> None:
    clock.set_zone("Asia/Kolkata")
    iso = clock.to_iso(clock.now())
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None  # carries an offset → a real instant


def test_to_iso_is_dst_aware_for_named_zones() -> None:
    """A named zone resolves the offset per-datetime, so a winter wall-clock
    value serializes with the winter offset, not today's."""
    clock.set_zone("America/New_York")
    winter = datetime(2026, 1, 15, 10, 0)
    summer = datetime(2026, 7, 15, 10, 0)
    assert clock.to_iso(winter).endswith("-05:00")  # EST
    assert clock.to_iso(summer).endswith("-04:00")  # EDT
