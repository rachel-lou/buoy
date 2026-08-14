"""Utility helpers: watchdog, power monitoring, config and logging."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from typing import Any, Dict, Iterable

import yaml


REQUIRED_TOP_LEVEL_KEYS = ("sensors", "power", "radio", "data", "watchdog", "logging")


class JsonFormatter(logging.Formatter):
    """A minimal structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        # Call sites tag their log line with a logical subsystem/sensor name via
        # extra={"component": ...} rather than "module" -- "module" is a LogRecord
        # attribute the stdlib already populates from the calling file, and passing
        # it through extra= raises a KeyError the moment the call actually fires.
        base: Dict[str, Any] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int((record.created % 1) * 1000):03d}Z",
            "level": record.levelname,
            "module": getattr(record, "component", None) or record.module,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in {
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "component", "exc_info", "exc_text", "stack_info", "lineno",
                "funcName", "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "asctime", "taskName",
            }:
                continue
            base[key] = value
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)
        return json.dumps(base, default=str)


def configure_logging(log_path: str, max_bytes: int, backup_count: int, level: str) -> logging.Logger:
    """Configure root logging with rotating JSON output."""
    logger = logging.getLogger("buoy")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=int(max_bytes), backupCount=int(backup_count)
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonFormatter())
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger


def load_config(path: str) -> Dict[str, Any]:
    """Load and validate the YAML configuration file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    validate_config(data)
    return data


def validate_config(config: Dict[str, Any], required: Iterable[str] = REQUIRED_TOP_LEVEL_KEYS) -> None:
    """Raise ``ValueError`` if mandatory keys are missing."""
    missing = [k for k in required if k not in config]
    if missing:
        raise ValueError(f"config missing required keys: {missing}")
    sensors = config["sensors"]
    if not isinstance(sensors, dict):
        raise ValueError("'sensors' section must be a mapping")
    for key in ("sample_interval_seconds", "depth_temp", "imu", "mcp3008",
                "dissolved_oxygen", "salinity", "leak"):
        if key not in sensors:
            raise ValueError(f"'sensors' missing required key: {key}")
    if not isinstance(config["power"].get("low_power_threshold_v"), (int, float)):
        raise ValueError("'power.low_power_threshold_v' must be numeric")
