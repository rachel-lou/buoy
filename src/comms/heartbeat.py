"""Periodic heartbeat broadcaster."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict

from . import Packet
from .radio import Radio


class HeartbeatService:
    """Send a status heartbeat over the radio at a fixed interval."""

    def __init__(
        self,
        radio: Radio,
        logger: logging.Logger,
        status_provider: Callable[[], Dict],
        interval_seconds: float = 300.0,
    ) -> None:
        self._radio = radio
        self._logger = logger
        self._status_provider = status_provider
        self._interval = float(interval_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread = threading.Thread(
            target=self._run, daemon=True
        )

    def build_packet(self) -> Packet:
        """Build the next heartbeat packet from the supplied status provider."""
        try:
            payload = self._status_provider() or {}
        except Exception as exc:  # noqa: BLE001
            self._logger.error("heartbeat_status_failed", extra={"error": str(exc)})
            payload = {"error": str(exc)}
        payload.setdefault("timestamp", time.time())
        return Packet.build("heartbeat", payload)

    def send_once(self) -> bool:
        """Build and transmit a single heartbeat. Returns True on success."""
        packet = self.build_packet()
        ok = self._radio.send(packet)
        if not ok:
            self._logger.warning("heartbeat_send_failed")
        return ok

    def start(self) -> None:
        """Begin the periodic heartbeat thread."""
        if self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat thread."""
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        # Emit one immediately on startup so peers get fresh state.
        self.send_once()
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._interval):
                break
            self.send_once()
