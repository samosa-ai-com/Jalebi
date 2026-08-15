"""Minimal 5-field cron matcher for the screening scheduler (PRD F10).

Deliberately tiny — this is the "Python equivalent of node-cron" with no
dependency (PRD Goal #10). It only answers the question the scheduler asks:
"does this expression match this instant?" It does not compute next run times.

Fields (standard order): ``minute hour day-of-month month day-of-week``.
Supported per field: ``*``, a single number, a list ``1,5,9``, a range ``1-5``,
a step ``*/5`` or ``1-10/2``. Day-of-week uses 0-6 (0 = Sunday), as cron.
"""

from __future__ import annotations

_MINUTE = (0, 59)
_HOUR = (0, 23)
_DOM = (1, 31)
_MONTH = (1, 12)
_DOW = (0, 6)


class CronError(ValueError):
    """A cron expression is malformed."""


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    """Parse one cron field into the set of integers it matches.

    ``N/step`` (e.g. ``1/2``) matches ``N, N+step, …`` up to the field's max
    (Vixie semantics), not just the literal ``N``.
    """
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError("empty cron field")
        if part == "*":
            values.update(range(lo, hi + 1))
            continue
        step = 1
        has_step = "/" in part
        if has_step:
            base, _, step_s = part.partition("/")
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"invalid step in cron field: {part}")
            step = int(step_s)
            if base == "*":
                values.update(range(lo, hi + 1, step))
                continue
        else:
            base = part
        if "-" in base:
            a_s, _, b_s = base.partition("-")
            if not a_s.isdigit() or not b_s.isdigit():
                raise CronError(f"invalid range in cron field: {part}")
            a, b = int(a_s), int(b_s)
            if a < lo or b > hi or a > b:
                raise CronError(f"range out of bounds in cron field: {part}")
            values.update(range(a, b + 1, step))
        elif has_step and base.isdigit():
            v = int(base)
            if v < lo or v > hi:
                raise CronError(f"value out of bounds in cron field: {part}")
            values.update(range(v, hi + 1, step))
        else:
            if not base.isdigit():
                raise CronError(f"invalid token in cron field: {part}")
            v = int(base)
            if v < lo or v > hi:
                raise CronError(f"value out of bounds in cron field: {part}")
            values.add(v)
    return values


def cron_matches(expr: str, minute: int, hour: int, dom: int, month: int, dow: int) -> bool:
    """Return True if ``expr`` (5 cron fields) matches the given clock values.

    ``dow`` is 0-6 with 0 = Sunday (Python's ``datetime.weekday()`` uses
    Monday = 0, so callers pass ``(dt.weekday() + 1) % 7`` for cron semantics).

    Vixie day rule: when BOTH day-of-month and day-of-week are restricted
    (non-``*``), a job runs when EITHER matches (not both).
    """
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"cron expression must have 5 fields, got {len(fields)}: {expr}")
    matchers = [
        _parse_field(fields[0], *_MINUTE),
        _parse_field(fields[1], *_HOUR),
        _parse_field(fields[2], *_DOM),
        _parse_field(fields[3], *_MONTH),
        _parse_field(fields[4], *_DOW),
    ]
    minute_m, hour_m, dom_m, month_m, dow_m = matchers
    time_match = minute in minute_m and hour in hour_m and month in month_m
    dom_restricted = len(dom_m) < 31
    dow_restricted = len(dow_m) < 7
    if dom_restricted and dow_restricted:
        date_match = dom in dom_m or dow in dow_m
    else:
        date_match = dom in dom_m and dow in dow_m
    return time_match and date_match


def cron_matches_datetime(expr: str, dt) -> bool:
    """Convenience wrapper accepting a ``datetime`` (cron day-of-week semantics)."""
    from datetime import datetime  # noqa: PLC0415 - local import keeps signature light

    if not isinstance(dt, datetime):
        raise TypeError("cron_matches_datetime expects a datetime")
    return cron_matches(
        expr,
        minute=dt.minute,
        hour=dt.hour,
        dom=dt.day,
        month=dt.month,
        dow=(dt.weekday() + 1) % 7,  # Monday(0) → 1, Sunday(6) → 0
    )
