import serial
import time
import logging
from typing import Optional

log = logging.getLogger(__name__)


class MeshtasticSerial:
    """
    Thin driver for sending text to an ESP32 running Meshtastic firmware
    via the Meshtastic serial module. Text sent here is broadcast over
    the mesh as a plain-text message.

    Usage:
        with MeshtasticSerial("/dev/ttyUSB0") as mesh:
            mesh.send("hello from the buoy")

    Or manually:
        driver = MeshtasticSerial("/dev/ttyAMA0", baud_rate=115200)
        driver.connect()
        driver.send("hello")
        driver.disconnect()
    """

    DEFAULT_BAUD = 115200
    DEFAULT_TIMEOUT = 1.0
    # Meshtastic serial module expects a newline-terminated message.
    TERMINATOR = b"\n"

    def __init__(
        self,
        port: str,
        baud_rate: int = DEFAULT_BAUD,
        timeout: float = DEFAULT_TIMEOUT,
        inter_message_delay: float = 0.1,
    ):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.inter_message_delay = inter_message_delay
        self._conn: Optional[serial.Serial] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self._conn and self._conn.is_open:
            return
        log.info("Opening serial port %s @ %d baud", self.port, self.baud_rate)
        self._conn = serial.Serial(
            port=self.port,
            baudrate=self.baud_rate,
            timeout=self.timeout,
        )
        time.sleep(0.1)  # let the port settle after open
        log.info("Serial port open")

    def disconnect(self) -> None:
        if self._conn and self._conn.is_open:
            self._conn.close()
            log.info("Serial port closed")
        self._conn = None

    def is_connected(self) -> bool:
        return self._conn is not None and self._conn.is_open

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send(self, message: str) -> None:
        """Send a single text message to the ESP32. Raises if not connected."""
        if not self.is_connected():
            raise RuntimeError("Not connected — call connect() first")

        payload = message.encode("utf-8") + self.TERMINATOR
        self._conn.write(payload)
        self._conn.flush()
        log.debug("Sent (%d bytes): %r", len(payload), message)

        if self.inter_message_delay > 0:
            time.sleep(self.inter_message_delay)

    def send_lines(self, lines: list[str]) -> None:
        """Send each string in *lines* as a separate message."""
        for line in lines:
            self.send(line)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "MeshtasticSerial":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()
