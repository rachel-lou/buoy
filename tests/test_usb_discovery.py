"""Tests for the shared USB-serial device discovery helper."""
from __future__ import annotations

import logging
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.usb_discovery import discover_usb_serial_device  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests.usb_discovery")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


def _module_with_ports(ports):
    module = MagicMock()
    module.tools.list_ports.comports = MagicMock(return_value=ports)
    return module


class TestDiscoverUsbSerialDevice(unittest.TestCase):
    def test_finds_matching_port(self):
        ports = [
            SimpleNamespace(device="/dev/ttyUSB0", vid=0x1A86, pid=0x7523),
            SimpleNamespace(device="/dev/ttyUSB1", vid=0x10C4, pid=0xEA60),
        ]
        module = _module_with_ports(ports)
        result = discover_usb_serial_device(module, 0x10C4, 0xEA60, _silent_logger())
        self.assertEqual(result, "/dev/ttyUSB1")

    def test_returns_none_when_no_match(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB0", vid=0x1A86, pid=0x7523)]
        module = _module_with_ports(ports)
        result = discover_usb_serial_device(module, 0x10C4, 0xEA60, _silent_logger())
        self.assertIsNone(result)

    def test_returns_none_on_empty_port_list(self):
        module = _module_with_ports([])
        result = discover_usb_serial_device(module, 0x10C4, 0xEA60, _silent_logger())
        self.assertIsNone(result)

    def test_returns_none_and_does_not_raise_when_enumeration_fails(self):
        module = MagicMock()
        module.tools.list_ports.comports = MagicMock(side_effect=OSError("no such subsystem"))
        result = discover_usb_serial_device(module, 0x10C4, 0xEA60, _silent_logger())
        self.assertIsNone(result)

    def test_works_without_a_logger(self):
        module = _module_with_ports([])
        result = discover_usb_serial_device(module, 0x10C4, 0xEA60)
        self.assertIsNone(result)

    def test_ignores_ports_missing_vid_pid_attributes(self):
        ports = [SimpleNamespace(device="/dev/ttyUSB0")]  # no vid/pid at all
        module = _module_with_ports(ports)
        result = discover_usb_serial_device(module, 0x10C4, 0xEA60, _silent_logger())
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
