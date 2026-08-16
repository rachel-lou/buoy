"""Tests for phone-facing time-range parsing and text query commands."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from comms.textquery import TextQueryService, chunk_lines  # noqa: E402
from comms.timespec import (  # noqa: E402
    TimeSpecError,
    looks_like_time_spec,
    parse_duration,
    parse_time_spec,
)
from data.store import DataStore  # noqa: E402
from sensors import Reading  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests.query")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


class TestParseTimeSpec(unittest.TestCase):
    def test_relative_hours(self):
        now = 1_700_000_000.0
        since, until = parse_time_spec("2h", now=now)
        self.assertEqual(until, now)
        self.assertEqual(since, now - 7200)

    def test_relative_minutes_and_days_case_insensitive(self):
        now = 1_700_000_000.0
        since, until = parse_time_spec("30M", now=now)
        self.assertEqual(since, now - 1800)
        since, until = parse_time_spec("1D", now=now)
        self.assertEqual(since, now - 86400)

    def test_single_date_is_a_full_utc_day(self):
        since, until = parse_time_spec("2026-08-13")
        self.assertEqual(until - since, 86400)
        expected_start = datetime(2026, 8, 13, tzinfo=timezone.utc).timestamp()
        self.assertEqual(since, expected_start)

    def test_date_range_is_inclusive(self):
        since, until = parse_time_spec("2026-08-01..2026-08-03")
        self.assertEqual(until - since, 3 * 86400)

    def test_zero_duration_rejected(self):
        with self.assertRaises(TimeSpecError):
            parse_time_spec("0h")

    def test_invalid_calendar_date_rejected(self):
        with self.assertRaises(TimeSpecError):
            parse_time_spec("2026-13-40")

    def test_backwards_date_range_rejected(self):
        with self.assertRaises(TimeSpecError):
            parse_time_spec("2026-08-05..2026-08-01")

    def test_unrecognized_spec_rejected(self):
        with self.assertRaises(TimeSpecError):
            parse_time_spec("whenever")

    def test_looks_like_time_spec(self):
        self.assertTrue(looks_like_time_spec("2h"))
        self.assertTrue(looks_like_time_spec("2026-08-13"))
        self.assertTrue(looks_like_time_spec("2026-08-01..2026-08-03"))
        self.assertFalse(looks_like_time_spec("temperature"))
        self.assertFalse(looks_like_time_spec("all"))


class TestParseDuration(unittest.TestCase):
    def test_parses_each_unit(self):
        self.assertEqual(parse_duration("5s"), 5.0)
        self.assertEqual(parse_duration("30m"), 1800.0)
        self.assertEqual(parse_duration("2h"), 7200.0)
        self.assertEqual(parse_duration("1d"), 86400.0)

    def test_case_insensitive(self):
        self.assertEqual(parse_duration("2H"), 7200.0)

    def test_zero_rejected(self):
        with self.assertRaises(TimeSpecError):
            parse_duration("0s")

    def test_date_like_input_rejected(self):
        with self.assertRaises(TimeSpecError):
            parse_duration("2026-08-13")


class TestChunkLines(unittest.TestCase):
    def test_packs_within_budget_and_preserves_content(self):
        lines = [f"line-{i}" * 5 for i in range(6)]
        chunks, truncated = chunk_lines(lines, max_chars=40, max_chunks=10)
        self.assertFalse(truncated)
        for c in chunks:
            self.assertLessEqual(len(c), 40)
        self.assertEqual("\n".join(chunks).split("\n"), lines)

    def test_truncates_after_max_chunks(self):
        lines = ["x"] * 50
        chunks, truncated = chunk_lines(lines, max_chars=2, max_chunks=3)
        self.assertTrue(truncated)
        self.assertEqual(len(chunks), 3)

    def test_empty_input_returns_single_empty_chunk(self):
        chunks, truncated = chunk_lines([])
        self.assertEqual(chunks, [""])
        self.assertFalse(truncated)


class _RecordingRadio:
    """Stand-in for ``Radio``: records ``send_text`` calls made by the service."""

    def __init__(self) -> None:
        self.sent: List[str] = []
        self._handler = None

    def register_text_handler(self, handler) -> None:
        self._handler = handler

    def send_text(self, text: str) -> bool:
        self.sent.append(text)
        return True

    def deliver(self, text: str) -> None:
        assert self._handler is not None, "attach() was never called"
        self._handler(text)


class TestTextQueryService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.store = DataStore(self.tmp.name, _silent_logger(), max_rows=10_000)
        self.now = time.time()
        self.store.write_many(
            [
                Reading(self.now - 3600, "temperature", 18.0, "C", 0),
                Reading(self.now - 1800, "temperature", 19.0, "C", 0),
                Reading(self.now - 1800, "depth", 1.5, "m", 0),
            ]
        )
        self.radio = _RecordingRadio()
        self.service = TextQueryService(self.radio, self.store, _silent_logger())
        self.service.attach()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def _reply(self) -> str:
        return "\n".join(self.radio.sent)

    def test_all_summary_covers_every_sensor(self):
        self.radio.deliver("ALL 2H")
        reply = self._reply()
        self.assertIn("temperature[C]", reply)
        self.assertIn("n=2", reply)
        self.assertIn("depth[m]", reply)
        self.assertIn("n=1", reply)

    def test_substring_sensor_match_is_case_insensitive(self):
        self.radio.deliver("temp 2h")
        reply = self._reply()
        self.assertIn("temperature[C]", reply)
        self.assertNotIn("depth[m]", reply)

    def test_leading_verb_is_optional(self):
        self.radio.deliver("GET temperature 2h")
        reply = self._reply()
        self.assertIn("temperature[C]", reply)

    def test_time_and_sensor_token_order_is_forgiving(self):
        self.radio.deliver("2h temperature")
        reply = self._reply()
        self.assertIn("temperature[C]", reply)

    def test_raw_csv_detail_lists_individual_rows(self):
        self.radio.deliver("TEMPERATURE 2H CSV")
        reply = self._reply()
        self.assertIn("18.000C", reply)
        self.assertIn("19.000C", reply)

    def test_unknown_sensor_lists_whats_available(self):
        self.radio.deliver("BANANA 2H")
        reply = self._reply()
        self.assertIn("unknown sensor", reply)
        self.assertIn("temperature", reply)

    def test_bad_time_spec_is_reported(self):
        self.radio.deliver("temperature tomorrow")
        reply = self._reply()
        self.assertIn("bad time range", reply)

    def test_help_lists_syntax_and_known_sensors(self):
        self.radio.deliver("HELP")
        reply = self._reply()
        self.assertIn("Buoy query commands", reply)
        self.assertIn("temperature", reply)
        self.assertIn("depth", reply)

    def test_garbage_input_gets_usage_hint(self):
        self.radio.deliver("blah blah blah")
        reply = self._reply()
        self.assertIn("usage:", reply)

    def test_no_data_in_range_is_reported_cleanly(self):
        self.radio.deliver("temperature 2026-01-01")
        reply = self._reply()
        self.assertIn("no data in that range", reply)

    def test_raw_rows_are_capped(self):
        ts = self.now
        many = [Reading(ts - i, "wave_hs", float(i), "m", 0) for i in range(1, 30)]
        self.store.write_many(many)
        service = TextQueryService(self.radio, self.store, _silent_logger(), max_raw_rows=5)
        service.attach()
        self.radio.deliver("wave_hs 1d csv")
        reply = self._reply()
        self.assertIn("capped at 5 rows", reply)


class TestIntervalCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.store = DataStore(self.tmp.name, _silent_logger(), max_rows=10_000)
        self.radio = _RecordingRadio()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def _reply(self) -> str:
        return "\n".join(self.radio.sent)

    def test_interval_command_applies_and_reports(self):
        service = TextQueryService(self.radio, self.store, _silent_logger(), set_interval=lambda s: s)
        service.attach()
        self.radio.deliver("INTERVAL 30M")
        self.assertIn("collection interval set to 1800s", self._reply())

    def test_set_prefix_is_optional(self):
        applied = []
        service = TextQueryService(
            self.radio, self.store, _silent_logger(), set_interval=lambda s: applied.append(s) or s
        )
        service.attach()
        self.radio.deliver("SET INTERVAL 5M")
        self.assertEqual(applied, [300.0])

    def test_interval_without_callback_reports_unavailable(self):
        service = TextQueryService(self.radio, self.store, _silent_logger())
        service.attach()
        self.radio.deliver("INTERVAL 5M")
        self.assertIn("not configured", self._reply())

    def test_bad_duration_is_reported(self):
        service = TextQueryService(self.radio, self.store, _silent_logger(), set_interval=lambda s: s)
        service.attach()
        self.radio.deliver("INTERVAL tomorrow")
        self.assertIn("bad duration", self._reply())

    def test_help_mentions_interval_when_configured(self):
        service = TextQueryService(self.radio, self.store, _silent_logger(), set_interval=lambda s: s)
        service.attach()
        self.radio.deliver("HELP")
        self.assertIn("INTERVAL", self._reply())

    def test_help_omits_interval_when_not_configured(self):
        service = TextQueryService(self.radio, self.store, _silent_logger())
        service.attach()
        self.radio.deliver("HELP")
        self.assertNotIn("INTERVAL", self._reply())


class TestStatusCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.store = DataStore(self.tmp.name, _silent_logger(), max_rows=10_000)
        self.radio = _RecordingRadio()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def _reply(self) -> str:
        return "\n".join(self.radio.sent)

    def _status_service(self, status):
        service = TextQueryService(self.radio, self.store, _silent_logger(), status_provider=lambda: status)
        service.attach()
        return service

    def test_status_reports_key_fields(self):
        self._status_service(
            {
                "uptime_seconds": 3725,  # 1h02m05s
                "battery_voltage": 12.34,
                "battery_current": 0.45,
                "low_power": False,
                "memory": {"total_mb": 972.0, "available_mb": 812.0, "used_percent": 16.5},
                "disk": {"total_mb": 30000.0, "free_mb": 14200.0, "used_percent": 52.7},
                "db_size_mb": 128.4,
                "row_count": 45213,
                "max_rows": 500000,
                "sensor_health": {"usb_depth_temp": True},
                "clock_sane": True,
            }
        )
        self.radio.deliver("STATUS")
        reply = self._reply()
        self.assertIn("uptime: 1h02m", reply)
        self.assertIn("12.34V", reply)
        self.assertIn("mem:", reply)
        self.assertIn("disk:", reply)
        self.assertIn("db:", reply)
        self.assertIn("sensors ok: usb_depth_temp", reply)
        self.assertNotIn("WARNING", reply)

    def test_status_flags_insane_clock(self):
        self._status_service({"uptime_seconds": 10, "clock_sane": False})
        self.radio.deliver("STATUS")
        self.assertIn("WARNING", self._reply())

    def test_status_flags_unhealthy_sensor(self):
        self._status_service({"uptime_seconds": 10, "sensor_health": {"usb_depth_temp": False}})
        self.radio.deliver("STATUS")
        self.assertIn("sensors down: usb_depth_temp", self._reply())

    def test_status_without_provider_reports_unavailable(self):
        service = TextQueryService(self.radio, self.store, _silent_logger())
        service.attach()
        self.radio.deliver("STATUS")
        self.assertIn("not configured", self._reply())

    def test_status_error_is_reported_cleanly(self):
        def boom():
            raise RuntimeError("sensor blew up")

        service = TextQueryService(self.radio, self.store, _silent_logger(), status_provider=boom)
        service.attach()
        self.radio.deliver("STATUS")
        self.assertIn("status error", self._reply())

    def test_help_mentions_status_when_configured(self):
        self._status_service({})
        self.radio.deliver("HELP")
        self.assertIn("STATUS", self._reply())


if __name__ == "__main__":
    unittest.main()
