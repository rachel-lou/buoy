"""Communication subsystem: packet protocol and radio I/O."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


PACKET_TYPES = {
    "heartbeat",
    "data_request",
    "data_response",
    "set_interval",
    "ota",
    "ack",
    "nack",
}


@dataclass
class Packet:
    """Wire-format radio packet.

    The checksum is the hex-encoded SHA-256 of the JSON-serialized payload
    (sorted keys, no whitespace). This guarantees both sides compute the same
    digest from the same data.
    """

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    checksum: str = ""

    @staticmethod
    def compute_checksum(payload: Dict[str, Any]) -> str:
        """Return the canonical SHA-256 hex digest of ``payload``."""
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def build(cls, packet_type: str, payload: Optional[Dict[str, Any]] = None) -> "Packet":
        """Create a packet with the checksum filled in."""
        if packet_type not in PACKET_TYPES:
            raise ValueError(f"Unknown packet type: {packet_type}")
        payload = payload or {}
        return cls(type=packet_type, payload=payload, checksum=cls.compute_checksum(payload))

    def verify(self) -> bool:
        """Recompute the checksum and compare to the one carried in the packet."""
        return self.checksum == self.compute_checksum(self.payload)

    def to_bytes(self) -> bytes:
        """Serialize the packet to a newline-terminated JSON line."""
        return (
            json.dumps(
                {"type": self.type, "payload": self.payload, "checksum": self.checksum},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "Packet":
        """Parse a packet from a JSON line. Raises ``ValueError`` on malformed input."""
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed packet: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError("packet must be a JSON object")
        ptype = obj.get("type")
        payload = obj.get("payload", {})
        checksum = obj.get("checksum", "")
        if ptype not in PACKET_TYPES:
            raise ValueError(f"unknown type: {ptype}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return cls(type=ptype, payload=payload, checksum=checksum)
