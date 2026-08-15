"""Tests for the USB-serial depth/temp sensor driver."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data.store import DataStore  # noqa: E402
from sensors import QUALITY_ERROR, QUALITY_GOOD  # noqa: E402
from sensors.usb_depth_temp import UsbDepthTempSensor  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests.usb_depth_temp")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


class _FakeSerial:
    """Minimal pyserial-compatible mock with an in-memory rx buffer."""

    def __init__(self, *args, **kwargs):
        self.rx = bytearray()
        self.closed = False

    def read(self, n):
        if not self.rx:
            time.sleep(0.01)
            return b""
        data = bytes(self.rx[:n])
        del self.rx[:n]
        return data

    def close(self):
        self.closed = True


def _fake_serial_module(endpoint, ports=None):
    module = MagicMock()
    module.Serial = MagicMock(return_value=endpoint)
    module.tools.list_ports.comports = MagicMock(return_value=ports or [])
    return module


class TestLineParsing(unittest.TestCase):
    def test_parses_real_sample_line(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2")
        sensor._parse_line(b"Temperature:23.370 , Pressure:100.53700 kPa, Deep:-0.001 m")
        by_sensor = {r.sensor: r for r in sensor.read()}
        self.assertAlmostEqual(by_sensor["temperature"].value, 23.370)
        self.assertEqual(by_sensor["temperature"].unit, "C")
        self.assertAlmostEqual(by_sensor["pressure"].value, 1005.3700, places=3)
        self.assertEqual(by_sensor["pressure"].unit, "mbar")
        self.assertAlmostEqual(by_sensor["depth"].value, -0.001)
        self.assertEqual(by_sensor["depth"].unit, "m")
        self.assertTrue(sensor.healthy)
        self.assertAlmostEqual(sensor.last_temperature_c, 23.370)

    def test_ignores_raw_adc_debug_line(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2")
        sensor._parse_line(b"raw pressure ADC(D1)=6095856 , raw temp ADC(D2) = 7727088")
        self.assertIsNone(sensor._latest)

    def test_ignores_garbled_partial_line(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2")
        sensor._parse_line(b"Temperaw pressure ADC(D1)=6095856 , raw temp ADC(D2) = 7726064")
        self.assertIsNone(sensor._latest)


class TestReadStaleness(unittest.TestCase):
    def test_no_data_yet_is_error_quality(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2", stale_after_seconds=30)
        readings = sensor.read()
        self.assertTrue(all(r.quality_flag == QUALITY_ERROR for r in readings))
        self.assertFalse(sensor.healthy)

    def test_stale_data_is_error_quality(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2", stale_after_seconds=0.05)
        sensor._parse_line(b"Temperature:23.370 , Pressure:100.53700 kPa, Deep:-0.001 m")
        time.sleep(0.15)
        readings = sensor.read()
        self.assertTrue(all(r.quality_flag == QUALITY_ERROR for r in readings))

    def test_fresh_data_is_good_quality(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2", stale_after_seconds=30)
        sensor._parse_line(b"Temperature:23.370 , Pressure:100.53700 kPa, Deep:-0.001 m")
        readings = sensor.read()
        self.assertTrue(all(r.quality_flag == QUALITY_GOOD for r in readings))
        self.assertTrue(sensor.healthy)


class TestDiscovery(unittest.TestCase):
    def test_explicit_device_skips_discovery(self):
        module = _fake_serial_module(_FakeSerial())
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2")
        self.assertEqual(sensor._discover_device(), "/dev/ttyUSB2")
        module.tools.list_ports.comports.assert_not_called()

    def test_auto_discovers_by_vid_pid(self):
        ports = [
            SimpleNamespace(device="/dev/ttyUSB0", vid=0x0403, pid=0x6001),  # unrelated FTDI adapter
            SimpleNamespace(device="/dev/ttyUSB2", vid=0x1A86, pid=0x7523),  # our CH341 board
        ]
        module = _fake_serial_module(_FakeSerial(), ports=ports)
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="auto")
        self.assertEqual(sensor._discover_device(), "/dev/ttyUSB2")

    def test_auto_discovery_finds_nothing(self):
        module = _fake_serial_module(_FakeSerial(), ports=[])
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="auto")
        self.assertIsNone(sensor._discover_device())

    def test_open_failure_marks_unhealthy(self):
        module = _fake_serial_module(_FakeSerial(), ports=[])
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="auto", reconnect_delay_seconds=0.05)
        self.assertFalse(sensor._open_serial())
        self.assertFalse(sensor.healthy)


class TestEndToEndReaderThread(unittest.TestCase):
    def test_reader_thread_picks_up_streamed_lines(self):
        fake = _FakeSerial()
        module = _fake_serial_module(fake)
        sensor = UsbDepthTempSensor(module, _silent_logger(), device="/dev/ttyUSB2", stale_after_seconds=30)
        sensor.start()
        try:
            fake.rx.extend(
                b"raw pressure ADC(D1)=6095856 , raw temp ADC(D2) = 7727088\n"
                b"Temperature:23.370 , Pressure:100.53700 kPa, Deep:-0.001 m\n"
            )
            deadline = time.time() + 2.0
            readings = []
            while time.time() < deadline:
                readings = sensor.read()
                if all(r.quality_flag == QUALITY_GOOD for r in readings):
                    break
                time.sleep(0.05)
            by_sensor = {r.sensor: r for r in readings}
            self.assertAlmostEqual(by_sensor["temperature"].value, 23.370)
        finally:
            sensor.stop()
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
