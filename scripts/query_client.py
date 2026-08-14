#!/usr/bin/env python3
"""Test client for the buoy's over-radio data queries.

Two ways to use this:

1. ``--demo`` -- runs the whole phone-query pipeline offline, in-process, with
   no hardware and no serial port. It wires two ``Radio`` instances together
   through an in-memory loopback "cable", seeds a temporary SQLite store with
   a few hours of synthetic readings, and shows exactly what a phone typing
   into the Meshtastic app would see, plus a scripted JSON data_request being
   reassembled from its chunked response. Run it with::

       python scripts/query_client.py --demo

2. Real hardware -- point ``--port`` at a serial port connected to a second
   Meshtastic node (paired on the same channel as the buoy's node, e.g. a
   laptop's USB LoRa dongle, or a phone running Termux with a USB/BLE-serial
   bridge) and send either a phone-style text command or a structured
   data_request::

       python scripts/query_client.py --port COM5 --text "ALL 2H"
       python scripts/query_client.py --port COM5 --json --sensor temperature --time-spec 2026-08-13 --out temps.csv
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import math
import os
import queue
import sys
import tempfile
import time
import zlib
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from comms import Packet  # noqa: E402
from comms.radio import DataRequestService, Radio  # noqa: E402
from comms.textquery import TextQueryService  # noqa: E402
from comms.timespec import TimeSpecError, parse_time_spec  # noqa: E402
from data.store import DataStore  # noqa: E402
from sensors import Reading  # noqa: E402


def _logger() -> logging.Logger:
    logger = logging.getLogger("query_client")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    return logger


# ---- shared request/response helpers, used by both --demo and real hardware ----

def query_text(radio: Radio, command: str, timeout: float = 3.0) -> List[str]:
    """Send a phone-style text command and return whatever reply lines arrive."""
    replies: List[str] = []
    radio.register_text_handler(lambda t: replies.append(t))
    radio.send_text(command)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
    return replies


def query_json(
    radio: Radio,
    since: float,
    until: float,
    sensor: Optional[str],
    limit: int = 5000,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """Send a data_request and reassemble the (possibly chunked) response into rows."""
    chunks_by_request: Dict[Any, Dict[int, Dict[str, Any]]] = {}
    nacks: List[Dict[str, Any]] = []

    def on_response(pkt: Packet) -> None:
        rid = pkt.payload["request_id"]
        chunks_by_request.setdefault(rid, {})[pkt.payload["chunk_index"]] = pkt.payload

    def on_nack(pkt: Packet) -> None:
        nacks.append(pkt.payload)

    radio.register_handler("data_response", on_response)
    radio.register_handler("nack", on_nack)
    radio.send(
        Packet.build(
            "data_request",
            {"since_timestamp": since, "until_timestamp": until, "sensor": sensor, "limit": limit},
        )
    )

    deadline = time.time() + timeout
    complete_id = None
    while time.time() < deadline:
        for rid, chunks in chunks_by_request.items():
            if chunks and len(chunks) == next(iter(chunks.values()))["chunk_count"]:
                complete_id = rid
                break
        if complete_id is not None or nacks:
            break
        time.sleep(0.1)

    if nacks:
        raise RuntimeError(f"buoy nacked request: {nacks[0]}")
    if complete_id is None:
        raise TimeoutError("no complete data_response received within timeout")

    ordered = chunks_by_request[complete_id]
    data_b64 = "".join(ordered[i]["data"] for i in range(len(ordered)))
    compressed = base64.b64decode(data_b64) if data_b64 else b""
    body = json.loads(zlib.decompress(compressed)) if compressed else {"rows": []}
    return body["rows"]


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    fieldnames = ["id", "timestamp", "sensor", "value", "unit", "quality_flag"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---- in-memory loopback "cable" so --demo needs no hardware at all ----

class _LoopbackEndpoint:
    """Minimal pyserial-compatible object backed by a pair of queues."""

    def __init__(self, inbox: "queue.Queue[bytes]", outbox: "queue.Queue[bytes]") -> None:
        self._inbox = inbox
        self._outbox = outbox
        self._buffer = bytearray()

    def write(self, data: bytes) -> int:
        self._outbox.put(bytes(data))
        return len(data)

    def read(self, n: int) -> bytes:
        if not self._buffer:
            try:
                self._buffer.extend(self._inbox.get(timeout=0.5))
            except queue.Empty:
                return b""
        chunk = bytes(self._buffer[:n])
        del self._buffer[:n]
        return chunk

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _LoopbackSerialModule:
    """Fake ``serial`` module whose ``Serial()`` always returns a fixed endpoint."""

    def __init__(self, endpoint: _LoopbackEndpoint) -> None:
        self._endpoint = endpoint

    def Serial(self, *args, **kwargs):  # noqa: N802 - matches pyserial's API
        return self._endpoint


def make_loopback_radio_pair(logger: logging.Logger) -> tuple:
    """Build two Radios wired to each other, simulating a buoy and a phone's node."""
    a_to_b: "queue.Queue[bytes]" = queue.Queue()
    b_to_a: "queue.Queue[bytes]" = queue.Queue()
    ep_buoy = _LoopbackEndpoint(inbox=b_to_a, outbox=a_to_b)
    ep_phone = _LoopbackEndpoint(inbox=a_to_b, outbox=b_to_a)
    buoy_radio = Radio(_LoopbackSerialModule(ep_buoy), logger, device="loopback-buoy")
    phone_radio = Radio(_LoopbackSerialModule(ep_phone), logger, device="loopback-phone")
    return buoy_radio, phone_radio


def _seed_demo_data(store: DataStore, now: Optional[float] = None) -> None:
    if now is None:
        now = time.time()
    sensors = [
        ("temperature", "C", 18.0),
        ("depth", "m", 1.2),
        ("salinity", "PSU", 34.0),
        ("dissolved_oxygen", "mg/L", 7.5),
    ]
    readings = []
    for minutes_ago in range(180, 0, -5):  # 3 hours of history, sampled every 5 min
        ts = now - minutes_ago * 60
        for name, unit, base in sensors:
            value = base + 0.1 * math.sin(minutes_ago / 10.0)
            readings.append(Reading(ts, name, value, unit, 0))
    store.write_many(readings)


def _print_text_reply(command: str, replies: List[str]) -> None:
    print(f"phone> {command}")
    if not replies:
        print("buoy < (no reply)")
        return
    for reply in replies:
        for line in reply.splitlines():
            print(f"buoy < {line}")


def run_demo() -> None:
    logger = _logger()
    buoy_radio, phone_radio = make_loopback_radio_pair(logger)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = DataStore(os.path.join(tmpdir, "demo.db"), logger, max_rows=100_000)
        _seed_demo_data(store)

        TextQueryService(buoy_radio, store, logger).attach()
        DataRequestService(buoy_radio, store, logger).attach()

        buoy_radio.start()
        phone_radio.start()
        try:
            print("=== Text commands (what a phone typing in the Meshtastic app sees) ===")
            for command in ("ALL 2H", "TEMPERATURE 1H CSV", "HELP", "nonsense"):
                replies = query_text(phone_radio, command)
                _print_text_reply(command, replies)
                print()

            print("=== Structured data_request (scripted/automated client) ===")
            since, until = parse_time_spec("2h")
            rows = query_json(phone_radio, since, until, sensor="temperature")
            print("phone> data_request sensor=temperature time=2h")
            print(f"buoy < reassembled {len(rows)} row(s)")
            for row in rows[:5]:
                print(f"       {row}")
            if len(rows) > 5:
                print(f"       ...({len(rows) - 5} more)")
        finally:
            phone_radio.stop()
            buoy_radio.stop()
            store.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--demo", action="store_true", help="Run the full query pipeline offline, no hardware required"
    )
    parser.add_argument("--port", help="Serial port for a real paired Meshtastic node, e.g. COM5 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=3.0, help="Seconds to wait for a reply")
    parser.add_argument("--text", metavar="COMMAND", help='Phone-style text command, e.g. --text "ALL 2H"')
    parser.add_argument("--json", action="store_true", help="Send a structured data_request instead of text")
    parser.add_argument("--sensor", help="Sensor filter for --json (omit for all sensors)")
    parser.add_argument("--time-spec", default="2h", help="Time range for --json: 2h, 1d, or YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=5000, help="Row cap for --json")
    parser.add_argument("--out", help="Write reassembled --json rows to this CSV file")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.demo:
        run_demo()
        return 0

    if not args.port:
        print("error: --port is required unless --demo is given", file=sys.stderr)
        return 2

    import serial  # real hardware only; --demo never needs pyserial installed

    logger = _logger()
    radio = Radio(serial, logger, device=args.port, baud=args.baud)
    radio.start()
    try:
        if args.json:
            try:
                since, until = parse_time_spec(args.time_spec)
            except TimeSpecError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            try:
                rows = query_json(radio, since, until, args.sensor, limit=args.limit, timeout=args.timeout)
            except (RuntimeError, TimeoutError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"received {len(rows)} row(s)")
            for row in rows[:20]:
                print(row)
            if len(rows) > 20:
                print(f"...({len(rows) - 20} more)")
            if args.out:
                write_csv(args.out, rows)
                print(f"wrote {args.out}")
        else:
            command = args.text or "HELP"
            _print_text_reply(command, query_text(radio, command, timeout=args.timeout))
    finally:
        radio.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
