"""Tests for the unsolicited plain-text sensor broadcast."""
from __future__ import annotations

import logging
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from comms.broadcast import SensorBroadcastService  # noqa: E402
from sensors import Reading  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests.broadcast")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


class _RecordingRadio:
    def __init__(self, fail: bool = False) -> None:
        self.sent = []
        self._fail = fail

    def send_text(self, text: str) -> bool:
        if self._fail:
            return False
        self.sent.append(text)
        return True


class TestSensorBroadcastService(unittest.TestCase):
    def setUp(self):
        self.radio = _RecordingRadio()
        self.service = SensorBroadcastService(self.radio, _silent_logger())

    def test_sends_one_line_per_reading(self):
        now = time.time()
        readings = [
            Reading(now, "temperature", 21.81, "C", 0),
            Reading(now, "pressure", 1006.28, "mbar", 0),
            Reading(now, "depth", 0.008, "m", 0),
        ]
        self.service.broadcast(readings)
        combined = "\n".join(self.radio.sent)
        self.assertIn("temperature=21.810C", combined)
        self.assertIn("pressure=1006.280mbar", combined)
        self.assertIn("depth=0.008m", combined)

    def test_empty_readings_sends_nothing(self):
        self.service.broadcast([])
        self.assertEqual(self.radio.sent, [])

    def test_many_readings_are_chunked_across_messages(self):
        now = time.time()
        readings = [Reading(now, f"sensor_{i}", float(i), "u", 0) for i in range(40)]
        self.service.broadcast(readings)
        self.assertGreater(len(self.radio.sent), 1)
        combined = "\n".join(self.radio.sent)
        for i in range(40):
            self.assertIn(f"sensor_{i}=", combined)

    def test_lines_carry_utc_time_of_day(self):
        now = time.time()
        self.service.broadcast([Reading(now, "temperature", 21.81, "C", 0)])
        self.assertRegex(self.radio.sent[0], r"\d{2}:\d{2}:\d{2}Z temperature=21\.810C")

    def test_successful_broadcast_is_logged(self):
        now = time.time()
        readings = [Reading(now, "temperature", 21.81, "C", 0), Reading(now, "pressure", 1006.28, "mbar", 0)]
        with self.assertLogs("tests.broadcast", level="INFO") as cm:
            self.service.broadcast(readings)
        record = next(r for r in cm.records if r.getMessage() == "broadcast_sent")
        self.assertEqual(record.reading_count, 2)
        self.assertEqual(record.chunk_count, 1)

    def test_send_failure_is_logged_as_error_not_success(self):
        radio = _RecordingRadio(fail=True)
        service = SensorBroadcastService(radio, _silent_logger())
        readings = [Reading(time.time(), "temperature", 21.81, "C", 0)]
        with self.assertLogs("tests.broadcast", level="INFO") as cm:
            service.broadcast(readings)
        messages = [r.getMessage() for r in cm.records]
        self.assertIn("broadcast_send_failed", messages)
        self.assertNotIn("broadcast_sent", messages)
        record = next(r for r in cm.records if r.getMessage() == "broadcast_send_failed")
        self.assertEqual(record.failed_chunks, 1)
        self.assertEqual(radio.sent, [])


if __name__ == "__main__":
    unittest.main()
