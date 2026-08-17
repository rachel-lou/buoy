"""USB-serial depth/temperature board (MS5837-style, bridged over CH341 USB-serial).

This board runs its own firmware and streams lines continuously. This
driver runs a background reader thread that keeps a "latest values" cache
up to date as lines arrive; :meth:`read` just returns whatever's currently
cached (or an error placeholder if it's gone stale), so it plugs into the
existing polled-sensor model -- ``main.py`` calls ``read()`` on the buoy's
collection cadence, same as every other sensor. 

The board enumerates as a generic CH341 USB-serial adapter, whose
``/dev/ttyUSBn`` path is not stable across reboots or replugs -- Linux assigns
it in enumeration order, alongside whatever else is plugged in. Rather than
hardcode a device path, this driver auto-discovers the port by USB
vendor/product ID via pyserial's ``list_ports``, unless an explicit device
path is given.

Sample line format (raw ADC debug lines are ignored; only this one is parsed)::

    Temperature:23.370 , Pressure:100.53700 kPa, Deep:-0.001 m
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import List, Optional, Tuple

from . import BaseSensor, QUALITY_ERROR, QUALITY_GOOD, Reading

_LINE_RE = re.compile(
    r"Temperature:\s*([-\d.]+)\s*,\s*Pressure:\s*([-\d.]+)\s*kPa\s*,\s*Deep:\s*([-\d.]+)\s*m",
    re.IGNORECASE,
)


class UsbDepthTempSensor(BaseSensor):
    """Reads temperature/pressure/depth from a free-running USB-serial board."""

    def __init__(
        self,
        serial_module,
        logger: logging.Logger,
        device: str = "auto",
        vendor_id: int = 0x1A86,
        product_id: int = 0x7523,
        baud: int = 115200,
        stale_after_seconds: float = 30.0,
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        super().__init__("usb_depth_temp", logger)
        self._serial_module = serial_module
        self._configured_device = device
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._baud = baud
        self._stale_after = float(stale_after_seconds)
        self._reconnect_delay = float(reconnect_delay_seconds)

        self._serial = None
        self._latest_lock = threading.Lock()
        # (temperature_c, pressure_mbar, depth_m, received_at)
        self._latest: Optional[Tuple[float, float, float, float]] = None
        self._last_temperature_c: Optional[float] = None

        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._mark_ok()

    # ---- lifecycle ---------------------------------------------------
    def start(self) -> None:
        """Start the background reader thread."""
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        """Stop the reader thread and close the serial port."""
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
        self._close_serial()

    def close(self) -> None:
        self.stop()

    @property
    def last_temperature_c(self) -> Optional[float]:
        """Most recent temperature in °C, or ``None`` if never received."""
        return self._last_temperature_c

    # ---- reader ------------------------------------------------------
    def _discover_device(self) -> Optional[str]:
        if self._configured_device and self._configured_device != "auto":
            return self._configured_device
        try:
            ports = self._serial_module.tools.list_ports.comports()
        except Exception as exc:  # noqa: BLE001
            self.logger.error("usb_depth_temp_discover_failed", extra={"error": str(exc)})
            return None
        for port in ports:
            if getattr(port, "vid", None) == self._vendor_id and getattr(port, "pid", None) == self._product_id:
                return port.device
        return None

    def _open_serial(self) -> bool:
        device = self._discover_device()
        if not device:
            self._mark_failed("no matching USB-serial device found")
            return False
        try:
            self._serial = self._serial_module.Serial(port=device, baudrate=self._baud, timeout=1.0)
            self.logger.info(
                "usb_depth_temp_open",
                extra={"component": "usb_depth_temp", "device": device},
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"open: {exc}")
            self._serial = None
            return False

    def _close_serial(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # noqa: BLE001
                pass
            self._serial = None

    def _reader_loop(self) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            if self._serial is None:
                if not self._open_serial():
                    self._stop_event.wait(self._reconnect_delay)
                    continue
            try:
                chunk = self._serial.read(256)
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(f"read: {exc}")
                self._close_serial()
                self._stop_event.wait(self._reconnect_delay)
                continue
            if not chunk:
                continue
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                self._parse_line(bytes(line))

    def _parse_line(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace")
        match = _LINE_RE.search(text)
        if not match:
            return  # raw ADC debug lines, partial lines, etc. -- not an error
        try:
            temp_c = float(match.group(1))
            pressure_kpa = float(match.group(2))
            depth_m = float(match.group(3))
        except ValueError:
            return
        with self._latest_lock:
            self._latest = (temp_c, pressure_kpa * 10.0, depth_m, time.time())
        self._last_temperature_c = temp_c
        self._mark_ok()

    # ---- polled-sensor interface --------------------------------------
    def read(self) -> List[Reading]:
        """Return the latest parsed reading, or an error placeholder if stale/missing."""
        with self._latest_lock:
            latest = self._latest
        now = time.time()
        if latest is None or (now - latest[3]) > self._stale_after:
            self._mark_failed("stale_or_no_data")
            return [
                Reading(now, "temperature", 0.0, "C", QUALITY_ERROR),
                Reading(now, "pressure", 0.0, "mbar", QUALITY_ERROR),
                Reading(now, "depth", 0.0, "m", QUALITY_ERROR),
            ]
        temp_c, pressure_mbar, depth_m, received_at = latest
        self._mark_ok()
        return [
            Reading(received_at, "temperature", temp_c, "C", QUALITY_GOOD),
            Reading(received_at, "pressure", pressure_mbar, "mbar", QUALITY_GOOD),
            Reading(received_at, "depth", depth_m, "m", QUALITY_GOOD),
        ]
