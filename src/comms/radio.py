"""Meshtastic radio I/O over UART with OTA + data-request handling."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional

from . import Packet


class Radio:
    """Line-oriented packet radio bound to a UART serial port.

    Operates a background reader thread that decodes incoming packets and
    dispatches them to registered handlers. Outgoing writes are mutex-locked
    to prevent interleaving between sender threads.
    """

    def __init__(
        self,
        serial_module,
        logger: logging.Logger,
        device: str = "/dev/ttyS0",
        baud: int = 115200,
        read_timeout_seconds: float = 0.5,
    ) -> None:
        self._serial_module = serial_module
        self._logger = logger
        self._device = device
        self._baud = baud
        self._read_timeout = read_timeout_seconds
        self._serial = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._handlers: Dict[str, Callable[[Packet], None]] = {}
        self._open()

    def _open(self) -> None:
        try:
            self._serial = self._serial_module.Serial(
                port=self._device,
                baudrate=self._baud,
                timeout=self._read_timeout,
            )
            self._logger.info(
                "radio_open", extra={"module": "radio", "device": self._device}
            )
        except Exception as exc:  # noqa: BLE001
            self._serial = None
            self._logger.error("radio_open_failed", extra={"error": str(exc)})

    def register_handler(self, packet_type: str, handler: Callable[[Packet], None]) -> None:
        """Bind ``handler`` to inbound packets of ``packet_type``."""
        self._handlers[packet_type] = handler

    def start(self) -> None:
        """Start the background reader thread."""
        if self._reader_thread and self._reader_thread.is_alive():
            return
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        """Signal the reader thread to stop and join it briefly."""
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("radio_close_failed", extra={"error": str(exc)})
            self._serial = None

    def send(self, packet: Packet) -> bool:
        """Serialize and transmit ``packet``. Returns True on success."""
        if self._serial is None:
            self._open()
        if self._serial is None:
            return False
        data = packet.to_bytes()
        try:
            with self._lock:
                self._serial.write(data)
                try:
                    self._serial.flush()
                except Exception:  # noqa: BLE001
                    pass
            return True
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "radio_send_failed",
                extra={"module": "radio", "type": packet.type, "error": str(exc)},
            )
            return False

    def _reader_loop(self) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            if self._serial is None:
                self._open()
                if self._serial is None:
                    time.sleep(1.0)
                    continue
            try:
                chunk = self._serial.read(256)
            except Exception as exc:  # noqa: BLE001
                self._logger.error("radio_read_failed", extra={"error": str(exc)})
                time.sleep(1.0)
                self._serial = None
                continue
            if not chunk:
                continue
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                line = line.strip()
                if not line:
                    continue
                self._dispatch(bytes(line))

    def _dispatch(self, line: bytes) -> None:
        try:
            packet = Packet.from_bytes(line)
        except ValueError as exc:
            self._logger.warning("radio_parse_failed", extra={"error": str(exc)})
            return
        if not packet.verify():
            self._logger.warning(
                "radio_checksum_failed", extra={"type": packet.type}
            )
            self.send(Packet.build("nack", {"reason": "checksum", "type": packet.type}))
            return
        handler = self._handlers.get(packet.type)
        if handler is None:
            self._logger.info(
                "radio_no_handler", extra={"type": packet.type}
            )
            return
        try:
            handler(packet)
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "radio_handler_failed",
                extra={"type": packet.type, "error": str(exc)},
            )


class DataRequestService:
    """Answer ``data_request`` packets with zlib-compressed query results."""

    def __init__(self, radio: Radio, store, logger: logging.Logger) -> None:
        self._radio = radio
        self._store = store
        self._logger = logger

    def attach(self) -> None:
        """Register the handler on the radio."""
        self._radio.register_handler("data_request", self._handle)

    def _handle(self, packet: Packet) -> None:
        since = float(packet.payload.get("since_timestamp", 0.0))
        sensor = packet.payload.get("sensor")
        limit = int(packet.payload.get("limit", 1000))
        try:
            rows = self._store.query(since_timestamp=since, sensor=sensor, limit=limit)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("data_query_failed", extra={"error": str(exc)})
            self._radio.send(Packet.build("nack", {"reason": "query_failed"}))
            return
        body = {"rows": rows, "since_timestamp": since, "sensor": sensor}
        import json as _json

        encoded = _json.dumps(body, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(encoded, level=6)
        payload = {
            "since_timestamp": since,
            "sensor": sensor,
            "count": len(rows),
            "encoding": "zlib+base64",
            "data": base64.b64encode(compressed).decode("ascii"),
        }
        self._radio.send(Packet.build("data_response", payload))


class OTAService:
    """Receive OTA packets, verify a SHA-256 checksum, stage the script for next boot."""

    def __init__(
        self,
        radio: Radio,
        staging_path: str,
        logger: logging.Logger,
    ) -> None:
        self._radio = radio
        self._staging_path = staging_path
        self._logger = logger

    def attach(self) -> None:
        """Register the handler on the radio."""
        self._radio.register_handler("ota", self._handle)

    def _handle(self, packet: Packet) -> None:
        try:
            b64 = packet.payload["script_b64"]
            declared = str(packet.payload["sha256"]).lower()
        except KeyError as exc:
            self._logger.error("ota_missing_field", extra={"field": str(exc)})
            self._radio.send(Packet.build("nack", {"reason": "missing_field"}))
            return
        try:
            script_bytes = base64.b64decode(b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("ota_decode_failed", extra={"error": str(exc)})
            self._radio.send(Packet.build("nack", {"reason": "decode_failed"}))
            return
        computed = hashlib.sha256(script_bytes).hexdigest()
        if computed != declared:
            self._logger.error(
                "ota_checksum_mismatch",
                extra={"declared": declared, "computed": computed},
            )
            self._radio.send(Packet.build("nack", {"reason": "checksum_mismatch"}))
            return
        try:
            os.makedirs(os.path.dirname(self._staging_path), exist_ok=True)
            tmp_path = self._staging_path + ".tmp"
            with open(tmp_path, "wb") as fh:
                fh.write(script_bytes)
            os.replace(tmp_path, self._staging_path)
            self._logger.info(
                "ota_staged",
                extra={"path": self._staging_path, "bytes": len(script_bytes)},
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.error("ota_write_failed", extra={"error": str(exc)})
            self._radio.send(Packet.build("nack", {"reason": "write_failed"}))
            return
        self._radio.send(
            Packet.build(
                "ack",
                {"type": "ota", "sha256": declared, "bytes": len(script_bytes)},
            )
        )
