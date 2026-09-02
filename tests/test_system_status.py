"""Tests for host-level system status (memory/disk/clock sanity)."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.system_status import (  # noqa: E402
    clock_looks_sane,
    read_disk_info,
    read_file_size_mb,
    read_memory_info,
)

_FAKE_MEMINFO = """MemTotal:        8000000 kB
MemFree:         1000000 kB
MemAvailable:    2000000 kB
Buffers:          100000 kB
Cached:           500000 kB
"""


class TestReadMemoryInfo(unittest.TestCase):
    def test_parses_proc_meminfo(self):
        with patch("builtins.open", mock_open(read_data=_FAKE_MEMINFO)):
            info = read_memory_info()
        self.assertIsNotNone(info)
        self.assertAlmostEqual(info["total_mb"], 8_000_000 / 1024.0)
        self.assertAlmostEqual(info["available_mb"], 2_000_000 / 1024.0)
        self.assertAlmostEqual(info["used_percent"], 100.0 * (1 - 2_000_000 / 8_000_000), places=1)

    def test_falls_back_to_memfree_when_memavailable_missing(self):
        data = "MemTotal:  8000000 kB\nMemFree:  1000000 kB\n"
        with patch("builtins.open", mock_open(read_data=data)):
            info = read_memory_info()
        self.assertAlmostEqual(info["available_mb"], 1_000_000 / 1024.0)

    def test_returns_none_when_unreadable(self):
        with patch("builtins.open", side_effect=OSError("no such file")):
            info = read_memory_info()
        self.assertIsNone(info)


class TestReadDiskInfo(unittest.TestCase):
    def test_reports_real_filesystem_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = read_disk_info(tmp)
        self.assertIsNotNone(info)
        self.assertGreater(info["total_mb"], 0)
        self.assertGreaterEqual(info["free_mb"], 0)

    def test_never_raises_on_a_bad_path(self):
        info = read_disk_info(os.path.join(tempfile.gettempdir(), "definitely-does-not-exist-xyz"))
        self.assertTrue(info is None or isinstance(info, dict))


class TestReadFileSizeMb(unittest.TestCase):
    def test_reports_real_file_size(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x" * 2048)
            path = tmp.name
        try:
            size = read_file_size_mb(path)
            self.assertAlmostEqual(size, 2048 / (1024.0 * 1024.0))
        finally:
            os.unlink(path)

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(read_file_size_mb(os.path.join(tempfile.gettempdir(), "no-such-file.db")))


class TestClockLooksSane(unittest.TestCase):
    def test_recent_time_is_sane(self):
        self.assertTrue(clock_looks_sane(time.time()))

    def test_epoch_zero_is_not_sane(self):
        self.assertFalse(clock_looks_sane(0.0))


if __name__ == "__main__":
    unittest.main()
