"""Meshtastic radio I/O over UART with OTA + data-request handling."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional

from . import Packet

try:
    from ..utils.usb_discovery import discover_usb_serial_device
except (ImportError, ValueError):  # tests put src/ on sys.path directly
    from utils.usb_discovery import discover_usb_serial_device  # type: ignore


class Radio:
    """Line-oriented packet radio bound to a serial port.

    Operates a background reader thread that decodes incoming packets and
    dispatches them to registered handlers. Outgoing writes are mutex-locked
    to prevent interleaving between sender threads.

    ``device`` may be an explicit path (e.g. a fixed GPIO UART like
    ``/dev/ttyS0``, stable because it's not USB-enumerated) or ``"auto"`` to
    discover a USB-connected node by vendor/product ID instead -- necessary
    when the radio is connected over USB, since that path isn't stable
    across reboots/replugs the way a wired GPIO UART is.
    """

    def __init__(
        self,
        serial_module,
        logger: logging.Logger,
        device: str = "/dev/ttyS0",
        vendor_id: Optional[int] = None,
        product_id: Optional[int] = None,
        baud: int = 115200,
        read_timeout_seconds: float = 0.5,
    ) -> None:
        self._serial_module = serial_module
        self._logger = logger
        self._configured_device = device
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._baud = baud
        self._read_timeout = read_timeout_seconds
        self._serial = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._handlers: Dict[str, Callable[[Packet], None]] = {}
        self._text_handler: Optional[Callable[[str], None]] = None
        self._open()

    def _resolve_device(self) -> Optional[str]:
        if self._configured_device and self._configured_device != "auto":
            return self._configured_device
        if self._vendor_id is None or self._product_id is None:
            return None
        return discover_usb_serial_device(self._serial_module, self._vendor_id, self._product_id, self._logger)

    def _open(self) -> None:
        device = self._resolve_device()
        if not device:
            self._serial = None
            self._logger.error("radio_open_failed", extra={"error": "no matching device found"})
            return
        try:
            self._serial = self._serial_module.Serial(
                port=device,
                baudrate=self._baud,
                timeout=self._read_timeout,
            )
            self._logger.info(
                "radio_open", extra={"component": "radio", "device": device}
            )
        except Exception as exc:  # noqa: BLE001
            self._serial = None
            self._logger.error("radio_open_failed", extra={"error": str(exc)})

    def register_handler(self, packet_type: str, handler: Callable[[Packet], None]) -> None:
        """Bind ``handler`` to inbound packets of ``packet_type``."""
        self._handlers[packet_type] = handler

    def register_text_handler(self, handler: Callable[[str], None]) -> None:
        """Bind ``handler`` to inbound lines that are not JSON packets.

        Lets a plain-text command typed by a human (e.g. via a phone's Meshtastic
        app) reach application code without having to be wrapped in the
        checksummed JSON envelope the machine-to-machine protocol uses.
        """
        self._text_handler = handler

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
                extra={"component": "radio", "type": packet.type, "error": str(exc)},
            )
            return False

    def send_text(self, text: str) -> bool:
        """Write a raw plain-text line (no JSON envelope) to the serial link.

        Used for human-readable replies to phone-typed text commands, kept
        separate from :meth:`send` so those replies never need a checksum a
        person would have to compute by hand.
        """
        if self._serial is None:
            self._open()
        if self._serial is None:
            return False
        data = (text.rstrip("\n") + "\n").encode("utf-8")
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
                "radio_send_text_failed", extra={"component": "radio", "error": str(exc)}
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
        except ValueError:
            self._dispatch_text(line)
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

    def _dispatch_text(self, line: bytes) -> None:
        """Route a line that failed JSON packet parsing to the text handler.

        Guards against reacting to RF noise / corrupted packets: only lines
        that decode cleanly as short printable text are handed off.
        """
        if self._text_handler is None:
            self._logger.warning("radio_parse_failed", extra={"error": "unrecognized line"})
            return
        try:
            text = line.decode("utf-8").strip()
        except UnicodeDecodeError:
            self._logger.warning("radio_parse_failed", extra={"error": "undecodable bytes"})
            return
        if not text or len(text) > 512 or not text.isprintable():
            self._logger.warning("radio_parse_failed", extra={"error": "unrecognized line"})
            return
        try:
            self._text_handler(text)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("radio_text_handler_failed", extra={"error": str(exc)})


MAX_QUERY_ROWS = 20_000
MAX_CHUNK_BASE64_CHARS = 180


class DataRequestService:
    """Answer ``data_request`` packets with zlib-compressed query results.

    Real LoRa/Meshtastic frames top out well under 256 bytes, so anything
    more than a handful of rows has to be split across multiple
    ``data_response`` packets. Each response chunk carries a shared
    ``request_id`` plus its ``chunk_index``/``chunk_count`` so the requester
    can reassemble the full base64 blob before decoding it.
    """

    def __init__(
        self,
        radio: Radio,
        store,
        logger: logging.Logger,
        max_chunk_base64_chars: int = MAX_CHUNK_BASE64_CHARS,
        max_query_rows: int = MAX_QUERY_ROWS,
    ) -> None:
        self._radio = radio
        self._store = store
        self._logger = logger
        self._max_chunk_chars = max(1, int(max_chunk_base64_chars))
        self._max_query_rows = int(max_query_rows)
        self._next_request_id = 0

    def attach(self) -> None:
        """Register the handler on the radio."""
        self._radio.register_handler("data_request", self._handle)

    def _handle(self, packet: Packet) -> None:
        since = packet.payload.get("since_timestamp")
        until = packet.payload.get("until_timestamp")
        sensor = packet.payload.get("sensor")
        limit = int(packet.payload.get("limit", 1000))
        limit = max(1, min(limit, self._max_query_rows))
        try:
            rows = self._store.query(
                since_timestamp=float(since) if since is not None else None,
                until_timestamp=float(until) if until is not None else None,
                sensor=sensor,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.error("data_query_failed", extra={"error": str(exc)})
            self._radio.send(Packet.build("nack", {"reason": "query_failed"}))
            return

        body = {"rows": rows, "since_timestamp": since, "until_timestamp": until, "sensor": sensor}
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(encoded, level=6)
        data_b64 = base64.b64encode(compressed).decode("ascii")

        self._next_request_id += 1
        request_id = self._next_request_id
        chunks = [
            data_b64[i : i + self._max_chunk_chars]
            for i in range(0, len(data_b64), self._max_chunk_chars)
        ] or [""]
        chunk_count = len(chunks)
        for index, chunk in enumerate(chunks):
            payload = {
                "request_id": request_id,
                "chunk_index": index,
                "chunk_count": chunk_count,
                "since_timestamp": since,
                "until_timestamp": until,
                "sensor": sensor,
                "count": len(rows),
                "encoding": "zlib+base64",
                "data": chunk,
            }
            self._radio.send(Packet.build("data_response", payload))


class IntervalControlService:
    """Answers ``set_interval`` packets by adjusting the buoy's collection
    interval -- the scripted/JSON counterpart to the phone-facing ``INTERVAL``
    text command handled by :class:`~comms.textquery.TextQueryService`.

    ``set_interval`` is a plain callback (``seconds -> applied_seconds``)
    rather than a sensor reference: today it just changes the main loop's
    sleep interval, but it's meant to describe how often the buoy should
    wake up, collect, and (eventually) power back down -- a whole-buoy
    concept, not something owned by any one sensor.
    """

    def __init__(self, radio: Radio, set_interval: Callable[[float], float], logger: logging.Logger) -> None:
        self._radio = radio
        self._set_interval = set_interval
        self._logger = logger

    def attach(self) -> None:
        """Register the handler on the radio."""
        self._radio.register_handler("set_interval", self._handle)

    def _handle(self, packet: Packet) -> None:
        seconds = packet.payload.get("interval_seconds")
        if seconds is None:
            self._logger.warning("set_interval_rejected", extra={"interval_seconds": seconds})
            self._radio.send(Packet.build("nack", {"reason": "missing_interval_seconds"}))
            return
        applied = self._set_interval(float(seconds))
        self._radio.send(Packet.build("ack", {"type": "set_interval", "interval_seconds": applied}))


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
