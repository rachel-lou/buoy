"""Systemd per-service watchdog ping.

"""
from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Optional


def sd_notify(message: str) -> bool:
    """Send a raw sd_notify datagram to systemd's notify socket.

    No-op (returns False) when ``NOTIFY_SOCKET`` isn't set, e.g. running
    outside systemd in local dev or under the test suite.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstract namespace socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(addr)
        sock.sendall(message.encode("utf-8"))
        return True
    except OSError:
        return False
    finally:
        sock.close()


class SystemdWatchdogNotifier:
    """Periodically pings systemd's own per-service ``WatchdogSec=`` keep-alive.

    This is entirely separate from the kernel ``/dev/watchdog`` device (which
    systemd itself already owns natively -- see the module docstring) and
    doesn't touch it. It only talks to systemd's notify socket, which is what
    actually detects buoy.service hanging specifically.
    """

    def __init__(self, logger: logging.Logger, interval_seconds: float = 30.0) -> None:
        self._logger = logger
        self._interval = float(interval_seconds)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Notify systemd we're ready and start the periodic watchdog ping.

        No-op when not running under systemd (``NOTIFY_SOCKET`` unset).
        """
        if not os.environ.get("NOTIFY_SOCKET"):
            return
        sd_notify("READY=1")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._logger.info(
            "systemd_watchdog_started",
            extra={"component": "watchdog", "interval_seconds": self._interval},
        )

    def stop(self) -> None:
        """Stop the periodic ping thread, if running."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            sd_notify("WATCHDOG=1")
            if self._stop_event.wait(self._interval):
                break
