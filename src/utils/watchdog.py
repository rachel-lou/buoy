"""Hardware watchdog driver for /dev/watchdog, plus systemd's own watchdog ping."""
from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Optional


class HardwareWatchdog:
    """Wraps /dev/watchdog with a kicker thread.

    Writing any byte resets the timer. Writing ``b'V'`` and then closing the
    device cleanly disarms the watchdog so a graceful shutdown does not cause
    a reboot.
    """

    MAGIC_CLOSE = b"V"
    KICK_BYTE = b"\0"

    def __init__(
        self,
        logger: logging.Logger,
        device: str = "/dev/watchdog",
        kick_interval_seconds: float = 30.0,
    ) -> None:
        self._logger = logger
        self._device = device
        self._interval = float(kick_interval_seconds)
        self._fd: int = -1
        self._stop_event = threading.Event()
        self._thread: threading.Thread = threading.Thread(
            target=self._run, daemon=True
        )
        self._armed = False
        self._lock = threading.Lock()

    def arm(self) -> bool:
        """Open the device and start the kicker thread. Returns True on success."""
        with self._lock:
            if self._armed:
                return True
            try:
                self._fd = os.open(self._device, os.O_WRONLY)
                self._armed = True
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
                self._logger.info(
                    "watchdog_armed",
                    extra={"component": "watchdog", "device": self._device},
                )
                return True
            except Exception as exc:  # noqa: BLE001
                self._logger.error(
                    "watchdog_arm_failed",
                    extra={"component": "watchdog", "error": str(exc)},
                )
                self._fd = -1
                self._armed = False
                return False

    def kick(self) -> None:
        """Reset the watchdog timer once."""
        if self._fd < 0:
            return
        try:
            os.write(self._fd, self.KICK_BYTE)
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "watchdog_kick_failed",
                extra={"component": "watchdog", "error": str(exc)},
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.kick()
            if self._stop_event.wait(self._interval):
                break

    def disarm(self) -> None:
        """Stop the kicker, write the magic close byte, then close the device."""
        with self._lock:
            if not self._armed:
                return
            self._stop_event.set()
            if self._thread.is_alive():
                self._thread.join(timeout=2.0)
            if self._fd >= 0:
                try:
                    os.write(self._fd, self.MAGIC_CLOSE)
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(
                        "watchdog_disarm_write_failed",
                        extra={"component": "watchdog", "error": str(exc)},
                    )
                try:
                    os.close(self._fd)
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(
                        "watchdog_close_failed",
                        extra={"component": "watchdog", "error": str(exc)},
                    )
                self._fd = -1
            self._armed = False
            self._logger.info("watchdog_disarmed", extra={"component": "watchdog"})

    @property
    def armed(self) -> bool:
        """Whether the watchdog kicker thread is currently running."""
        return self._armed


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
    """Periodically pings systemd's own ``WatchdogSec=`` keep-alive.

    Deliberately independent of :class:`HardwareWatchdog` and the kernel
    ``/dev/watchdog`` device: if that device is ever unavailable (as it was
    when something else already held it open), the kernel watchdog kicker
    thread never starts -- but systemd's own supervision, whose entire job is
    to catch a stuck/broken process, should not depend on that same fragile
    resource in order to keep running.
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
