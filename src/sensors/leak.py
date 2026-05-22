"""Leak detection sensor wired to a GPIO line.

The sensor is two bare wires whose resistance drops when wet; the line is
held high through an internal pull-up and pulled low on leak. Detection runs
on a GPIO edge interrupt so reaction time is independent of the main sample
loop.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional

from . import BaseSensor, Reading, QUALITY_GOOD, QUALITY_ERROR


class LeakSensor(BaseSensor):
    """GPIO-based leak detector with edge-triggered shutdown callback."""

    def __init__(
        self,
        gpio_module,
        logger: logging.Logger,
        pin: int = 17,
        bouncetime_ms: int = 200,
        on_leak: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__("leak", logger)
        self._gpio = gpio_module
        self._pin = pin
        self._bouncetime = int(bouncetime_ms)
        self._on_leak = on_leak
        self._triggered = threading.Event()
        self._lock = threading.Lock()
        self._configure()

    def _configure(self) -> None:
        try:
            self._gpio.setwarnings(False)
            self._gpio.setmode(self._gpio.BCM)
            self._gpio.setup(
                self._pin, self._gpio.IN, pull_up_down=self._gpio.PUD_UP
            )
            self._gpio.add_event_detect(
                self._pin,
                self._gpio.FALLING,
                callback=self._handle_edge,
                bouncetime=self._bouncetime,
            )
            self._mark_ok()
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"configure: {exc}")

    def _handle_edge(self, channel: int) -> None:
        # Re-sample to filter momentary spikes
        try:
            level = self._gpio.input(channel)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("leak_read_failed", extra={"error": str(exc)})
            level = 0
        if level == 0:
            with self._lock:
                already = self._triggered.is_set()
                self._triggered.set()
            self.logger.critical(
                "leak_detected", extra={"module": self.name, "pin": self._pin}
            )
            if not already and self._on_leak is not None:
                try:
                    self._on_leak()
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("leak_callback_failed", extra={"error": str(exc)})

    def set_callback(self, callback: Callable[[], None]) -> None:
        """Register or replace the on-leak shutdown callback."""
        self._on_leak = callback

    def is_leaking(self) -> bool:
        """Whether a leak edge has fired since startup."""
        return self._triggered.is_set()

    def read(self) -> List[Reading]:
        """Return current leak state (1 = leak, 0 = dry)."""
        try:
            level = self._gpio.input(self._pin)
            value = 1.0 if level == 0 else 0.0
            if self._triggered.is_set():
                value = 1.0
            self._mark_ok()
            return [Reading(self._now(), "leak", value, "bool", QUALITY_GOOD)]
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"read: {exc}")
            return [Reading(self._now(), "leak", 0.0, "bool", QUALITY_ERROR)]

    def close(self) -> None:
        """Detach the GPIO interrupt handler."""
        try:
            self._gpio.remove_event_detect(self._pin)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("leak_detach_failed", extra={"error": str(exc)})
