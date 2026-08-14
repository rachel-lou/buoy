"""Communication-subsystem unit tests."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
import unittest
import zlib
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from comms import Packet  # noqa: E402
from comms.heartbeat import HeartbeatService  # noqa: E402
from comms.radio import DataRequestService, OTAService, Radio  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests.comms")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


class TestPacket(unittest.TestCase):
    def test_build_includes_checksum(self):
        pkt = Packet.build("heartbeat", {"a": 1})
        self.assertTrue(pkt.verify())
        self.assertEqual(
            pkt.checksum,
            hashlib.sha256(b'{"a":1}').hexdigest(),
        )

    def test_round_trip(self):
        pkt = Packet.build("data_request", {"since_timestamp": 0, "sensor": "depth"})
        encoded = pkt.to_bytes()
        decoded = Packet.from_bytes(encoded.strip())
        self.assertEqual(decoded.type, pkt.type)
        self.assertEqual(decoded.payload, pkt.payload)
        self.assertTrue(decoded.verify())

    def test_bad_checksum_rejected(self):
        pkt = Packet.build("heartbeat", {"a": 1})
        pkt.checksum = "deadbeef"
        self.assertFalse(pkt.verify())

    def test_unknown_type_rejected(self):
        with self.assertRaises(ValueError):
            Packet.build("not_a_real_type", {})

    def test_malformed_payload_rejected(self):
        with self.assertRaises(ValueError):
            Packet.from_bytes(b"not json")


class _FakeSerial:
    """Minimal pyserial-compatible mock with an in-memory rx/tx."""

    def __init__(self, *args, **kwargs):
        self.tx = bytearray()
        self.rx = bytearray()

    def read(self, n):
        if not self.rx:
            time.sleep(0.01)
            return b""
        data = bytes(self.rx[:n])
        del self.rx[:n]
        return data

    def write(self, data):
        self.tx.extend(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


def _radio_module_with_fake():
    module = MagicMock()
    instance = _FakeSerial()
    module.Serial = MagicMock(return_value=instance)
    return module, instance


class TestRadio(unittest.TestCase):
    def test_send_writes_to_serial(self):
        module, fake = _radio_module_with_fake()
        radio = Radio(module, _silent_logger(), device="/dev/null", baud=115200)
        self.assertTrue(radio.send(Packet.build("heartbeat", {"x": 1})))
        self.assertIn(b'"type":"heartbeat"', bytes(fake.tx))
        radio.stop()

    def test_inbound_dispatch_and_checksum(self):
        module, fake = _radio_module_with_fake()
        radio = Radio(module, _silent_logger(), device="/dev/null", baud=115200)
        received = []
        radio.register_handler("heartbeat", lambda p: received.append(p))
        radio.start()
        try:
            pkt = Packet.build("heartbeat", {"hello": "world"})
            fake.rx.extend(pkt.to_bytes())
            deadline = time.time() + 2.0
            while time.time() < deadline and not received:
                time.sleep(0.05)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].payload, {"hello": "world"})
        finally:
            radio.stop()

    def test_bad_checksum_emits_nack(self):
        module, fake = _radio_module_with_fake()
        radio = Radio(module, _silent_logger(), device="/dev/null", baud=115200)
        radio.start()
        try:
            bad = b'{"type":"heartbeat","payload":{"a":1},"checksum":"bad"}\n'
            fake.rx.extend(bad)
            deadline = time.time() + 2.0
            while time.time() < deadline and b"nack" not in bytes(fake.tx):
                time.sleep(0.05)
            self.assertIn(b"nack", bytes(fake.tx))
        finally:
            radio.stop()

    def test_send_text_writes_plain_unwrapped_line(self):
        module, fake = _radio_module_with_fake()
        radio = Radio(module, _silent_logger(), device="/dev/null", baud=115200)
        self.assertTrue(radio.send_text("ALL 2H"))
        self.assertEqual(bytes(fake.tx), b"ALL 2H\n")
        radio.stop()

    def test_non_json_line_is_routed_to_text_handler(self):
        module, fake = _radio_module_with_fake()
        radio = Radio(module, _silent_logger(), device="/dev/null", baud=115200)
        received = []
        radio.register_text_handler(lambda t: received.append(t))
        radio.start()
        try:
            fake.rx.extend(b"ALL 2H\n")
            deadline = time.time() + 2.0
            while time.time() < deadline and not received:
                time.sleep(0.05)
            self.assertEqual(received, ["ALL 2H"])
        finally:
            radio.stop()

    def test_non_json_line_without_handler_is_ignored(self):
        module, fake = _radio_module_with_fake()
        radio = Radio(module, _silent_logger(), device="/dev/null", baud=115200)
        radio.start()
        try:
            fake.rx.extend(b"ALL 2H\n")
            time.sleep(0.3)
            # No text handler registered: nothing should be transmitted back.
            self.assertEqual(bytes(fake.tx), b"")
        finally:
            radio.stop()


class TestHeartbeat(unittest.TestCase):
    def test_packet_contents(self):
        radio = MagicMock()
        radio.send = MagicMock(return_value=True)
        service = HeartbeatService(
            radio,
            _silent_logger(),
            status_provider=lambda: {
                "uptime_seconds": 12.3,
                "battery_voltage": 12.5,
                "sensor_health": {"depth_temp": True},
                "row_count": 42,
            },
            interval_seconds=300.0,
        )
        pkt = service.build_packet()
        self.assertEqual(pkt.type, "heartbeat")
        self.assertTrue(pkt.verify())
        self.assertEqual(pkt.payload["battery_voltage"], 12.5)
        self.assertEqual(pkt.payload["sensor_health"], {"depth_temp": True})
        self.assertEqual(pkt.payload["row_count"], 42)

    def test_send_once(self):
        radio = MagicMock()
        radio.send = MagicMock(return_value=True)
        service = HeartbeatService(
            radio, _silent_logger(),
            status_provider=lambda: {"row_count": 1},
            interval_seconds=300.0,
        )
        self.assertTrue(service.send_once())
        radio.send.assert_called_once()


class TestDataRequestService(unittest.TestCase):
    def test_query_and_respond(self):
        radio = MagicMock()
        sent = []
        radio.send = MagicMock(side_effect=lambda p: sent.append(p) or True)
        radio.register_handler = MagicMock()

        store = MagicMock()
        store.query = MagicMock(return_value=[{"id": 1, "sensor": "depth"}])

        svc = DataRequestService(radio, store, _silent_logger())
        svc.attach()
        handler = radio.register_handler.call_args.args[1]

        handler(Packet.build("data_request", {"since_timestamp": 0, "sensor": "depth"}))
        self.assertEqual(len(sent), 1)
        response = sent[0]
        self.assertEqual(response.type, "data_response")
        self.assertEqual(response.payload["count"], 1)
        self.assertEqual(response.payload["encoding"], "zlib+base64")

    def test_until_timestamp_is_forwarded_to_store(self):
        radio = MagicMock()
        radio.send = MagicMock(return_value=True)
        radio.register_handler = MagicMock()

        store = MagicMock()
        store.query = MagicMock(return_value=[])

        svc = DataRequestService(radio, store, _silent_logger())
        svc.attach()
        handler = radio.register_handler.call_args.args[1]

        handler(Packet.build("data_request", {"since_timestamp": 10.0, "until_timestamp": 20.0, "sensor": "depth"}))
        store.query.assert_called_once_with(since_timestamp=10.0, until_timestamp=20.0, sensor="depth", limit=1000)

    def test_row_limit_is_capped_server_side(self):
        radio = MagicMock()
        radio.send = MagicMock(return_value=True)
        radio.register_handler = MagicMock()

        store = MagicMock()
        store.query = MagicMock(return_value=[])

        svc = DataRequestService(radio, store, _silent_logger(), max_query_rows=500)
        svc.attach()
        handler = radio.register_handler.call_args.args[1]

        handler(Packet.build("data_request", {"limit": 999999}))
        self.assertEqual(store.query.call_args.kwargs["limit"], 500)

    def test_large_response_is_chunked_and_reassembles(self):
        radio = MagicMock()
        sent = []
        radio.send = MagicMock(side_effect=lambda p: sent.append(p) or True)
        radio.register_handler = MagicMock()

        rows = [{"id": i, "sensor": "depth", "value": float(i)} for i in range(200)]
        store = MagicMock()
        store.query = MagicMock(return_value=rows)

        svc = DataRequestService(radio, store, _silent_logger(), max_chunk_base64_chars=64)
        svc.attach()
        handler = radio.register_handler.call_args.args[1]

        handler(Packet.build("data_request", {"sensor": "depth", "limit": 500}))
        self.assertGreater(len(sent), 1)

        request_ids = {p.payload["request_id"] for p in sent}
        self.assertEqual(len(request_ids), 1)
        chunk_count = sent[0].payload["chunk_count"]
        self.assertEqual(chunk_count, len(sent))
        for index, pkt in enumerate(sent):
            self.assertEqual(pkt.payload["chunk_index"], index)

        data_b64 = "".join(p.payload["data"] for p in sent)
        decompressed = zlib.decompress(base64.b64decode(data_b64))
        body = json.loads(decompressed)
        self.assertEqual(body["rows"], rows)


class TestOTAService(unittest.TestCase):
    def test_valid_ota_is_staged(self):
        radio = MagicMock()
        sent = []
        radio.send = MagicMock(side_effect=lambda p: sent.append(p) or True)
        radio.register_handler = MagicMock()

        script = b"print('updated')\n"
        sha = hashlib.sha256(script).hexdigest()
        b64 = base64.b64encode(script).decode("ascii")

        with tempfile.TemporaryDirectory() as tmp:
            staging = os.path.join(tmp, "staging", "update.py")
            svc = OTAService(radio, staging, _silent_logger())
            svc.attach()
            handler = radio.register_handler.call_args.args[1]
            handler(Packet.build("ota", {"script_b64": b64, "sha256": sha}))
            self.assertTrue(os.path.exists(staging))
            with open(staging, "rb") as fh:
                self.assertEqual(fh.read(), script)
            self.assertEqual(sent[-1].type, "ack")

    def test_bad_checksum_rejected(self):
        radio = MagicMock()
        sent = []
        radio.send = MagicMock(side_effect=lambda p: sent.append(p) or True)
        radio.register_handler = MagicMock()

        script = b"print('updated')\n"
        b64 = base64.b64encode(script).decode("ascii")

        with tempfile.TemporaryDirectory() as tmp:
            staging = os.path.join(tmp, "staging", "update.py")
            svc = OTAService(radio, staging, _silent_logger())
            svc.attach()
            handler = radio.register_handler.call_args.args[1]
            handler(Packet.build("ota", {"script_b64": b64, "sha256": "deadbeef"}))
            self.assertFalse(os.path.exists(staging))
            self.assertEqual(sent[-1].type, "nack")


if __name__ == "__main__":
    unittest.main()
