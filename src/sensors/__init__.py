"""Sensor base classes and shared data structures."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List


QUALITY_GOOD = 0
QUALITY_WARNING = 1
QUALITY_ERROR = 2


@dataclass
class Reading:
    """A single timestamped sensor measurement."""

    timestamp: float
    sensor: str
    value: float
    unit: str
    quality_flag: int

    def to_dict(self) -> Dict[str, Any]:
        """Return the reading as a plain dict suitable for serialization."""
        return asdict(self)


class BaseSensor:
    """Abstract base for every sensor driver.

    Concrete subclasses implement :meth:`read` and optionally :meth:`close`.
    """

    def __init__(self, name: str, logger: logging.Logger) -> None:
        self.name = name
        self.logger = logger
        self._last_error: str = ""
        self._healthy: bool = True

    @property
    def healthy(self) -> bool:
        """Whether the sensor produced a good reading on its last attempt."""
        return self._healthy

    @property
    def last_error(self) -> str:
        """Last error message recorded, empty string if none."""
        return self._last_error

    def read(self) -> List[Reading]:
        """Return one or more :class:`Reading` instances. Implemented by subclasses."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any held hardware resources. Override if needed."""
        return None

    def _now(self) -> float:
        """Current wall-clock time as a Unix timestamp."""
        return time.time()

    def _mark_ok(self) -> None:
        self._healthy = True
        self._last_error = ""

    def _mark_failed(self, message: str) -> None:
        self._healthy = False
        self._last_error = message
        self.logger.error(
            "sensor_error",
            extra={"module": self.name, "error": message},
        )
