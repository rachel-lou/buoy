"""DataStore unit tests."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data.store import DataStore  # noqa: E402
from sensors import Reading  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests.store")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


class TestDataStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.path = self.tmp.name
        self.store = DataStore(self.path, _silent_logger(), max_rows=10)

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_write_and_query(self):
        now = time.time()
        readings = [
            Reading(now, "temperature", 21.0, "C", 0),
            Reading(now + 1, "depth", 1.5, "m", 0),
        ]
        ids = self.store.write_many(readings)
        self.assertEqual(len(ids), 2)
        rows = self.store.query()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.store.row_count(), 2)

    def test_query_by_sensor_and_range(self):
        ts = time.time()
        for i in range(5):
            self.store.write(Reading(ts + i, "temperature", float(i), "C", 0))
            self.store.write(Reading(ts + i, "depth", float(i) * 0.5, "m", 0))
        rows = self.store.query(sensor="temperature")
        self.assertTrue(all(r["sensor"] == "temperature" for r in rows))
        self.assertEqual(len(rows), 5)
        rows = self.store.query(since_timestamp=ts + 2, until_timestamp=ts + 3)
        self.assertTrue(all(ts + 2 <= r["timestamp"] <= ts + 3 for r in rows))

    def test_ring_buffer_pruning(self):
        ts = time.time()
        for i in range(25):
            self.store.write(Reading(ts + i, "temperature", float(i), "C", 0))
        self.assertEqual(self.store.row_count(), 10)
        rows = self.store.query()
        # Oldest 15 should be gone, surviving values >= 15
        values = [r["value"] for r in rows]
        self.assertEqual(min(values), 15.0)
        self.assertEqual(max(values), 24.0)

    def test_export_csv(self):
        ts = time.time()
        self.store.write(Reading(ts, "temperature", 22.0, "C", 0))
        text = self.store.export_csv()
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sensor"], "temperature")

    def test_export_csv_to_file(self):
        ts = time.time()
        self.store.write(Reading(ts, "depth", 2.0, "m", 0))
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "out.csv")
            text = self.store.export_csv(path=out_path, sensor="depth")
            with open(out_path, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), text)

    def test_export_json(self):
        ts = time.time()
        self.store.write(Reading(ts, "salinity", 35.0, "PSU", 0))
        text = self.store.export_json(sensor="salinity")
        rows = json.loads(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sensor"], "salinity")

    def test_flush_does_not_raise(self):
        self.store.write(Reading(time.time(), "wave_hs", 1.0, "m", 0))
        self.store.flush()


if __name__ == "__main__":
    unittest.main()
