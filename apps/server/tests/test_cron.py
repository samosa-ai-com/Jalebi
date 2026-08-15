"""Cron matcher tests (PRD F10 scheduler)."""

import pytest

from jalebi.cron import CronError, cron_matches, cron_matches_datetime


def test_every_minute_matches_always():
    assert cron_matches("* * * * *", 0, 0, 1, 1, 0)
    assert cron_matches("* * * * *", 59, 23, 31, 12, 6)


def test_single_values():
    assert cron_matches("30 2 15 6 3", 30, 2, 15, 6, 3)
    assert not cron_matches("30 2 15 6 3", 31, 2, 15, 6, 3)
    assert not cron_matches("30 2 15 6 3", 30, 3, 15, 6, 3)


def test_lists_and_ranges():
    assert cron_matches("0,15,30,45 * * * *", 15, 0, 1, 1, 0)
    assert not cron_matches("0,15,30,45 * * * *", 20, 0, 1, 1, 0)
    assert cron_matches("0-30 * * * *", 22, 0, 1, 1, 0)
    assert not cron_matches("0-30 * * * *", 45, 0, 1, 1, 0)
    assert cron_matches("1-10/2 * * * *", 3, 0, 1, 1, 0)  # 1,3,5,7,9
    assert not cron_matches("1-10/2 * * * *", 4, 0, 1, 1, 0)


def test_steps():
    assert cron_matches("*/15 * * * *", 0, 0, 1, 1, 0)
    assert cron_matches("*/15 * * * *", 45, 0, 1, 1, 0)
    assert not cron_matches("*/15 * * * *", 20, 0, 1, 1, 0)


def test_n_step_matches_from_start():
    """``N/step`` matches N, N+step, … (Vixie), not just the literal N."""
    assert cron_matches("1/2 * * * *", 1, 0, 1, 1, 0)
    assert cron_matches("1/2 * * * *", 3, 0, 1, 1, 0)
    assert cron_matches("1/2 * * * *", 59, 0, 1, 1, 0)
    assert not cron_matches("1/2 * * * *", 2, 0, 1, 1, 0)
    assert cron_matches("30/15 * * * *", 30, 0, 1, 1, 0)
    assert cron_matches("30/15 * * * *", 45, 0, 1, 1, 0)


def test_restricted_dom_or_dow():
    """Vixie rule: when both day-of-month and day-of-week are restricted, a job
    runs when EITHER matches."""
    # 1st-of-month OR Monday.
    assert cron_matches("0 6 1 * 1", 0, 6, 1, 6, 5)  # 1st, not Monday
    assert cron_matches("0 6 1 * 1", 0, 6, 15, 6, 1)  # Monday, not 1st
    assert not cron_matches("0 6 1 * 1", 0, 6, 15, 6, 2)  # neither
    # A single restricted field stays plain AND with the wildcard field.
    assert cron_matches("0 6 1 * *", 0, 6, 1, 6, 2)  # 1st-of-month, any dow
    assert not cron_matches("0 6 1 * *", 0, 6, 2, 6, 2)


def test_day_of_week_monday_zero_sunday():
    # cron dow: 0=Sunday. Verify a Monday-only (dow=1) schedule.
    assert cron_matches("0 6 * * 1", 0, 6, 3, 6, 1)
    assert not cron_matches("0 6 * * 1", 0, 6, 3, 6, 2)


def test_invalid_expressions_raise():
    bad = ("", "1 2 3", "a * * * *", "* * * * * *", "61 * * * *", "1-5/0 * * * *", "1-2-3 * * * *")
    for expr in bad:
        with pytest.raises(CronError):
            cron_matches(expr, 0, 0, 1, 1, 0)


def test_datetime_wrapper_sunday_shift():
    from datetime import datetime

    # 2026-08-09 is a Sunday. Python weekday()=6 → cron dow should be 0.
    dt = datetime(2026, 8, 9, 6, 0)
    assert cron_matches_datetime("0 6 * * 0", dt)
    assert not cron_matches_datetime("0 6 * * 1", dt)
