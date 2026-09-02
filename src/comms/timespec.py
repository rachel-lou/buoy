"""Parse human-typed time-range expressions into (since, until) epoch seconds.

Used by the phone-facing text command grammar, but kept independent of the
radio/store code so a scripted client can reuse the exact same parsing (e.g.
``--since 2h`` on the CLI test client) without importing radio internals.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

_RELATIVE_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_DATE_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

TIME_SPEC_HELP = "2h, 30m, 1d, YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD"
DURATION_HELP = "5s, 30m, 2h, or 1d"


class TimeSpecError(ValueError):
    """Raised when a time-range expression can't be parsed."""


def looks_like_time_spec(token: str) -> bool:
    """Cheap format check, used to tell a time spec apart from a sensor name."""
    token = token.strip()
    return bool(_RELATIVE_RE.match(token) or _DATE_RE.match(token) or _DATE_RANGE_RE.match(token))


def parse_duration(spec: str) -> float:
    """Parse a bare duration like ``"30m"``/``"2h"``/``"5s"``/``"1d"`` into seconds."""
    spec = spec.strip()
    m = _RELATIVE_RE.match(spec)
    if not m:
        raise TimeSpecError(f"unrecognized duration {spec!r}; expected {DURATION_HELP}")
    amount, unit = int(m.group(1)), m.group(2).lower()
    seconds = _UNIT_SECONDS[unit] * amount
    if seconds <= 0:
        raise TimeSpecError(f"invalid duration: {spec!r}")
    return float(seconds)


def parse_time_spec(spec: str, now: Optional[float] = None) -> Tuple[float, float]:
    """Parse a time-range expression into ``(since_ts, until_ts)`` UTC epoch seconds.

    Supported forms:
      * ``"2h"``, ``"30m"``, ``"1d"``, ``"45s"`` -- the last N units, ending now
      * ``"YYYY-MM-DD"``                          -- that whole UTC day
      * ``"YYYY-MM-DD..YYYY-MM-DD"``               -- inclusive UTC day range
    """
    if now is None:
        now = time.time()
    spec = spec.strip()

    if _RELATIVE_RE.match(spec):
        seconds = parse_duration(spec)
        return now - seconds, now

    m = _DATE_RANGE_RE.match(spec)
    if m:
        start = _day_start_utc(m.group(1))
        end = _day_start_utc(m.group(2)) + 86400
        if end <= start:
            raise TimeSpecError(f"date range end is before start: {spec!r}")
        return start, end

    m = _DATE_RE.match(spec)
    if m:
        start = _day_start_utc(spec)
        return start, start + 86400

    raise TimeSpecError(f"unrecognized time spec {spec!r}; expected {TIME_SPEC_HELP}")


def _day_start_utc(date_str: str) -> float:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TimeSpecError(f"invalid date {date_str!r}: {exc}") from exc
    return dt.timestamp()
