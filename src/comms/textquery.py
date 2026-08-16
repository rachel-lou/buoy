"""Phone-friendly text command interface for querying stored readings.

A phone with no WiFi still has the Meshtastic app, which lets a person type a
plain-text message straight to the buoy's node. That message shows up on the
buoy's UART as a line that isn't one of our checksummed JSON packets (see
``Radio.register_text_handler``); this module turns it into a store query and
sends back a short plain-text reply, chunked to fit LoRa-sized messages.

Command grammar (case-insensitive, leading verb word optional):

    [GET] <sensor|ALL> <timespec> [csv]
    [SET] INTERVAL <duration>
    STATUS

Examples::

    ALL 2H                    -> summary of every sensor, last 2 hours
    TEMPERATURE 2026-08-13     -> summary of temperature readings on that UTC day
    SALINITY 6H CSV           -> raw salinity samples from the last 6 hours
    INTERVAL 30M              -> change the buoy's collection interval to 30 minutes
    STATUS                    -> uptime, battery, memory/disk, DB size, sensor health
    HELP                      -> usage + currently known sensor names

``INTERVAL`` changes the same interval the main loop already sleeps for
between collection cycles -- today that's just an always-on daemon's sample
rate, but it's meant to describe how often the buoy should wake up, collect,
and power back down once that duty-cycling exists. It's a whole-buoy setting,
not tied to any one sensor.

Replies default to a compact per-sensor summary (count/min/max/avg/last) computed
with one SQL aggregate pass -- cheap on the Pi and tiny over the air. Raw/csv
detail is capped at ``MAX_RAW_ROWS_PER_QUERY`` rows and a reply is capped at
``MAX_TEXT_REPLY_CHUNKS`` radio messages, so a phone typo like "ALL 2026-01-01..2026-12-31"
can't turn into an unbounded, battery-draining transmit run -- narrow queries and the
planned WiFi bulk sync are the intended path for full history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .timespec import (
    DURATION_HELP,
    TIME_SPEC_HELP,
    TimeSpecError,
    looks_like_time_spec,
    parse_duration,
    parse_time_spec,
)

MAX_RAW_ROWS_PER_QUERY = 200
TEXT_CHUNK_MAX_CHARS = 180
MAX_TEXT_REPLY_CHUNKS = 8
_HEADER_RESERVE = len("[99/99] ")

_LEADING_VERBS = {"GET", "QUERY", "DATA", "FETCH"}
_RAW_FLAGS = {"CSV", "RAW"}


def chunk_lines(
    lines: List[str],
    max_chars: int = TEXT_CHUNK_MAX_CHARS - _HEADER_RESERVE,
    max_chunks: int = MAX_TEXT_REPLY_CHUNKS,
) -> Tuple[List[str], bool]:
    """Greedily pack ``lines`` into newline-joined chunks <= ``max_chars``.

    Returns ``(chunks, truncated)``; ``truncated`` is True if there were more
    chunks than ``max_chunks`` allows, so the caller can append a notice.
    """
    if not lines:
        return [""], False
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        added_len = len(line) + (1 if current else 0)
        if current and current_len + added_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += added_len
    if current:
        chunks.append("\n".join(current))
    truncated = len(chunks) > max_chunks
    if truncated:
        chunks = chunks[:max_chunks]
    return chunks, truncated


def _split_sensor_and_time(tokens: List[str]) -> Optional[Tuple[str, str]]:
    if len(tokens) != 2:
        return None
    a, b = tokens
    if looks_like_time_spec(a) and not looks_like_time_spec(b):
        return b, a
    return a, b


def _match_sensors(token: str, available: List[str]) -> List[str]:
    token_lower = token.lower()
    if token_lower in ("all", "*"):
        return list(available)
    exact = [s for s in available if s.lower() == token_lower]
    if exact:
        return exact
    return [s for s in available if token_lower in s.lower()]


def _format_summary_lines(rows: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for r in rows:
        last_dt = datetime.fromtimestamp(r["last_timestamp"], tz=timezone.utc).strftime("%m-%d %H:%MZ")
        lines.append(
            f"{r['sensor']}[{r['unit']}] n={r['count']} min={r['min_value']:.2f} "
            f"max={r['max_value']:.2f} avg={r['avg_value']:.2f} last={last_dt}"
        )
    return lines


def _format_raw_lines(rows: List[Dict[str, Any]], multi_sensor: bool) -> List[str]:
    lines = []
    for r in rows:
        ts = datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).strftime("%m-%dT%H:%M:%SZ")
        if multi_sensor:
            lines.append(f"{ts} {r['sensor']}={r['value']:.3f}{r['unit']}")
        else:
            lines.append(f"{ts} {r['value']:.3f}{r['unit']}")
    return lines


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}d{hours}h{minutes:02d}m" if days else f"{hours}h{minutes:02d}m"


def _format_status_lines(status: Dict[str, Any]) -> List[str]:
    lines = [f"uptime: {_format_uptime(status.get('uptime_seconds', 0))}"]

    if not status.get("clock_sane", True):
        lines.append("WARNING: system clock looks wrong -- timestamps may be mistimed")

    battery_v = status.get("battery_voltage")
    if battery_v is not None:
        current = status.get("battery_current") or 0.0
        flag = " LOW" if status.get("low_power") else ""
        lines.append(f"battery: {battery_v:.2f}V {current:.2f}A{flag}")

    memory = status.get("memory")
    if memory:
        lines.append(
            f"mem: {memory['available_mb']:.0f}MB free / {memory['total_mb']:.0f}MB "
            f"({memory['used_percent']:.0f}% used)"
        )

    disk = status.get("disk")
    if disk:
        lines.append(
            f"disk: {disk['free_mb']:.0f}MB free / {disk['total_mb']:.0f}MB "
            f"({disk['used_percent']:.0f}% used)"
        )

    db_size = status.get("db_size_mb")
    row_count = status.get("row_count")
    max_rows = status.get("max_rows")
    if db_size is not None or row_count is not None:
        size_part = f"{db_size:.1f}MB" if db_size is not None else "?"
        if row_count is not None and max_rows:
            rows_part = f"{row_count}/{max_rows} rows ({100.0 * row_count / max_rows:.0f}%)"
        elif row_count is not None:
            rows_part = f"{row_count} rows"
        else:
            rows_part = ""
        lines.append(f"db: {size_part} {rows_part}".strip())

    sensor_health = status.get("sensor_health") or {}
    if sensor_health:
        down = [name for name, ok in sensor_health.items() if not ok]
        if down:
            lines.append(f"sensors down: {', '.join(down)}")
        else:
            lines.append(f"sensors ok: {', '.join(sensor_health)}")

    return lines


class TextQueryService:
    """Answers plain-text data queries typed from a phone with a text reply."""

    def __init__(
        self,
        radio,
        store,
        logger: logging.Logger,
        max_raw_rows: int = MAX_RAW_ROWS_PER_QUERY,
        set_interval: Optional[Callable[[float], float]] = None,
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        self._radio = radio
        self._store = store
        self._logger = logger
        self._max_raw_rows = max_raw_rows
        self._set_interval = set_interval
        self._status_provider = status_provider

    def attach(self) -> None:
        """Register this service as the radio's plain-text handler."""
        self._radio.register_text_handler(self._handle)

    def _handle(self, text: str) -> None:
        try:
            reply_lines = self._build_reply(text)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("text_query_failed", extra={"error": str(exc), "text": text})
            reply_lines = ["error handling query; send HELP for syntax"]
        self._send_lines(reply_lines)

    def _send_lines(self, lines: List[str]) -> None:
        chunks, truncated = chunk_lines(lines)
        total = len(chunks) + (1 if truncated else 0)
        for i, body in enumerate(chunks, start=1):
            header = f"[{i}/{total}] " if total > 1 else ""
            self._radio.send_text(header + body)
        if truncated:
            self._radio.send_text(
                f"[{total}/{total}] truncated at {MAX_TEXT_REPLY_CHUNKS} msgs; "
                "narrow the time range, or wait for wifi sync"
            )

    def _build_reply(self, text: str) -> List[str]:
        tokens = text.split()
        if tokens and tokens[0].upper() in _LEADING_VERBS:
            tokens = tokens[1:]
        if not tokens:
            return self._usage_lines()

        if tokens[0].upper() == "SET" and len(tokens) > 1 and tokens[1].upper() == "INTERVAL":
            tokens = tokens[1:]
        if tokens[0].upper() == "INTERVAL":
            return self._handle_interval_command(tokens[1:])

        if tokens[0].upper() == "HELP":
            return self._help_lines()

        if tokens[0].upper() == "STATUS":
            return self._handle_status_command()

        raw = False
        if tokens and tokens[-1].upper() in _RAW_FLAGS:
            raw = True
            tokens = tokens[:-1]

        pair = _split_sensor_and_time(tokens)
        if pair is None:
            return self._usage_lines()
        sensor_token, time_token = pair

        try:
            since, until = parse_time_spec(time_token)
        except TimeSpecError as exc:
            return [f"bad time range: {exc}", "send HELP for syntax"]

        available = self._store.distinct_sensors()
        sensors = _match_sensors(sensor_token, available)
        if not sensors:
            known = ", ".join(available) if available else "none yet"
            return [f"unknown sensor {sensor_token!r}. known: {known}"]

        if raw:
            return self._raw_lines(sensors, since, until)
        return self._summary_lines(sensors, since, until)

    def _summary_lines(self, sensors: List[str], since: float, until: float) -> List[str]:
        rows = self._store.summarize(since_timestamp=since, until_timestamp=until, sensors=sensors)
        if not rows:
            return ["no data in that range"]
        return _format_summary_lines(rows)

    def _raw_lines(self, sensors: List[str], since: float, until: float) -> List[str]:
        rows: List[Dict[str, Any]] = []
        for s in sensors:
            rows.extend(
                self._store.query(
                    since_timestamp=since,
                    until_timestamp=until,
                    sensor=s,
                    limit=self._max_raw_rows + 1,
                )
            )
        rows.sort(key=lambda r: r["timestamp"])
        capped = len(rows) > self._max_raw_rows
        rows = rows[: self._max_raw_rows]
        if not rows:
            return ["no data in that range"]
        lines = _format_raw_lines(rows, multi_sensor=len(sensors) > 1)
        if capped:
            lines.append(f"...capped at {self._max_raw_rows} rows; narrow the range for more detail")
        return lines

    def _handle_interval_command(self, tokens: List[str]) -> List[str]:
        if self._set_interval is None:
            return ["interval control is not configured on this buoy"]
        if len(tokens) != 1:
            return [f"usage: [SET] INTERVAL <{DURATION_HELP}>", "send HELP for details"]
        try:
            seconds = parse_duration(tokens[0])
        except TimeSpecError as exc:
            return [f"bad duration: {exc}"]
        applied = self._set_interval(seconds)
        return [f"collection interval set to {applied:.0f}s"]

    def _handle_status_command(self) -> List[str]:
        if self._status_provider is None:
            return ["status is not configured on this buoy"]
        try:
            status = self._status_provider()
        except Exception as exc:  # noqa: BLE001
            return [f"status error: {exc}"]
        return _format_status_lines(status)

    def _usage_lines(self) -> List[str]:
        return [f"usage: <sensor|ALL> <{TIME_SPEC_HELP}> [csv]", "send HELP for details"]

    def _help_lines(self) -> List[str]:
        available = self._store.distinct_sensors()
        known = ", ".join(available) if available else "none yet"
        lines = [
            "Buoy query commands:",
            f"<sensor|ALL> <{TIME_SPEC_HELP}> [csv]",
            "Ex: ALL 2H | TEMPERATURE 2026-08-13 | SALINITY 6H CSV",
            f"Sensors: {known}",
        ]
        if self._set_interval is not None:
            lines.append(f"[SET] INTERVAL <{DURATION_HELP}> -- change collection interval")
        if self._status_provider is not None:
            lines.append("STATUS -- uptime, battery, memory/disk, DB size, sensor health")
        return lines
