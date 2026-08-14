"""Phone-friendly text command interface for querying stored readings.

A phone with no WiFi still has the Meshtastic app, which lets a person type a
plain-text message straight to the buoy's node. That message shows up on the
buoy's UART as a line that isn't one of our checksummed JSON packets (see
``Radio.register_text_handler``); this module turns it into a store query and
sends back a short plain-text reply, chunked to fit LoRa-sized messages.

Command grammar (case-insensitive, leading verb word optional):

    [GET] <sensor|ALL> <timespec> [csv]

Examples::

    ALL 2H                    -> summary of every sensor, last 2 hours
    TEMPERATURE 2026-08-13     -> summary of temperature readings on that UTC day
    SALINITY 6H CSV           -> raw salinity samples from the last 6 hours
    HELP                      -> usage + currently known sensor names

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
from typing import Any, Dict, List, Optional, Tuple

from .timespec import TIME_SPEC_HELP, TimeSpecError, looks_like_time_spec, parse_time_spec

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


class TextQueryService:
    """Answers plain-text data queries typed from a phone with a text reply."""

    def __init__(
        self,
        radio,
        store,
        logger: logging.Logger,
        max_raw_rows: int = MAX_RAW_ROWS_PER_QUERY,
    ) -> None:
        self._radio = radio
        self._store = store
        self._logger = logger
        self._max_raw_rows = max_raw_rows

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
        if tokens[0].upper() == "HELP":
            return self._help_lines()

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

    def _usage_lines(self) -> List[str]:
        return [f"usage: <sensor|ALL> <{TIME_SPEC_HELP}> [csv]", "send HELP for details"]

    def _help_lines(self) -> List[str]:
        available = self._store.distinct_sensors()
        known = ", ".join(available) if available else "none yet"
        return [
            "Buoy query commands:",
            f"<sensor|ALL> <{TIME_SPEC_HELP}> [csv]",
            "Ex: ALL 2H | TEMPERATURE 2026-08-13 | SALINITY 6H CSV",
            f"Sensors: {known}",
        ]
