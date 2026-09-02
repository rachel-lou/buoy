"""Host-level system status: memory, disk, and clock sanity.

Read directly from /proc and statvfs rather than adding a dependency like
psutil -- this only ever runs on a Raspberry Pi under Linux, and every
function degrades to ``None`` on any other platform (e.g. a developer's
laptop running the test suite) instead of raising.
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from typing import Dict, Optional

# Anything before this is almost certainly a Pi that booted with no RTC and no
# network to correct itself -- a sign readings from this boot may be mistimed.
_CLOCK_SANITY_BASELINE = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()


def clock_looks_sane(now: Optional[float] = None) -> bool:
    """Whether the system clock is at least plausible (not stuck at some epoch)."""
    if now is None:
        now = time.time()
    return now >= _CLOCK_SANITY_BASELINE


def read_memory_info() -> Optional[Dict[str, float]]:
    """Return ``{"total_mb", "available_mb", "used_percent"}``, or None if unavailable."""
    try:
        fields: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                fields[key] = int(rest.strip().split()[0])  # kB
        total_kb = fields["MemTotal"]
        available_kb = fields.get("MemAvailable", fields.get("MemFree", 0))
        used_percent = 100.0 * (1 - available_kb / total_kb) if total_kb else 0.0
        return {
            "total_mb": total_kb / 1024.0,
            "available_mb": available_kb / 1024.0,
            "used_percent": round(used_percent, 1),
        }
    except Exception:  # noqa: BLE001
        return None


def read_disk_info(path: str) -> Optional[Dict[str, float]]:
    """Return ``{"total_mb", "free_mb", "used_percent"}`` for the filesystem holding ``path``."""
    try:
        usage = shutil.disk_usage(path)
        used_percent = 100.0 * usage.used / usage.total if usage.total else 0.0
        return {
            "total_mb": usage.total / (1024.0 * 1024.0),
            "free_mb": usage.free / (1024.0 * 1024.0),
            "used_percent": round(used_percent, 1),
        }
    except Exception:  # noqa: BLE001
        return None


def read_file_size_mb(path: str) -> Optional[float]:
    """Size of the file at ``path`` in MB, or None if it can't be stat'd."""
    try:
        return os.path.getsize(path) / (1024.0 * 1024.0)
    except OSError:
        return None
