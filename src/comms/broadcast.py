"""plain-text sensor broadcast.
readings go out on the
channel as plain text for any Meshtastic node to see -- but driven by the
buoy's own collection cycle instead of a standalone script, and reusing the
same LoRa-safe chunking as :class:`~comms.textquery.TextQueryService` rather
than assuming everything fits in one message.

Unlike a query reply, there's no requester to address, so a broadcast that
gets truncated is just logged rather than followed up with a notice message.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from .textquery import chunk_lines


def _format_broadcast_lines(readings: List) -> List[str]:
    lines = []
    for r in readings:
        ts = datetime.fromtimestamp(r.timestamp, tz=timezone.utc).strftime("%H:%M:%SZ")
        lines.append(f"{ts} {r.sensor}={r.value:.3f}{r.unit}")
    return lines


class SensorBroadcastService:
    """Sends the readings from each collection cycle as plain radio text."""

    def __init__(self, radio, logger: logging.Logger) -> None:
        self._radio = radio
        self._logger = logger

    def broadcast(self, readings: List) -> None:
        """Format and send ``readings`` as one or more plain-text radio lines.

        No-op if ``readings`` is empty -- nothing collected, nothing to say.
        """
        if not readings:
            return
        lines = _format_broadcast_lines(readings)
        chunks, truncated = chunk_lines(lines)
        failed = sum(1 for chunk in chunks if not self._radio.send_text(chunk))
        if failed:
            self._logger.error(
                "broadcast_send_failed",
                extra={"component": "broadcast", "failed_chunks": failed, "chunk_count": len(chunks)},
            )
        else:
            self._logger.info(
                "broadcast_sent",
                extra={"component": "broadcast", "reading_count": len(readings), "chunk_count": len(chunks)},
            )
        if truncated:
            self._logger.warning(
                "broadcast_truncated",
                extra={"component": "broadcast", "reading_count": len(readings)},
            )
