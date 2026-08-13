"""SQLite-backed data store with ring buffer and export helpers."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional

try:
    from ..sensors import Reading
except (ImportError, ValueError):  # tests put src/ on sys.path directly
    from sensors import Reading  # type: ignore


DB_PATH = os.environ.get("BUOY_DB_PATH", "/data/buoy.db")
MAX_ROWS = int(os.environ.get("BUOY_MAX_ROWS", 500_000))

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    sensor TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    quality_flag INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_sensor ON readings(sensor);
"""


class DataStore:
    """Thread-safe SQLite store with a fixed-row ring buffer."""

    def __init__(self, path: str = DB_PATH, max_rows: int = MAX_ROWS):
        # Create directories if they don't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)

        self._max_rows = max_rows
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        log.info(f"DataStore initialised at {path}")

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        log.info("Database schema verified")

    def write(self, reading: Reading) -> int:
        """Insert a single reading. Returns its row id."""
        return self.write_many([reading])[0]

    def write_many(self, readings: Iterable[Reading]) -> List[int]:
        """Insert multiple readings in one transaction, prune, and return new ids."""
        rows = list(readings)
        if not rows:
            return []
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                ids: List[int] = []
                for r in rows:
                    cur = self._conn.execute(
                        "INSERT INTO readings (timestamp, sensor, value, unit, quality_flag) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (r.timestamp, r.sensor, float(r.value), r.unit, int(r.quality_flag)),
                    )
                    ids.append(int(cur.lastrowid))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._prune_if_needed()
            return ids

    def _prune_if_needed(self) -> int:
        """Drop oldest rows so the table size <= ``max_rows``. Returns rows deleted."""
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM readings"
            ).fetchone()[0]
            if count <= self._max_rows:
                return 0
            excess = count - self._max_rows
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "DELETE FROM readings WHERE id IN ("
                    "SELECT id FROM readings ORDER BY id ASC LIMIT ?"
                    ")",
                    (excess,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._logger.info(
                "ring_buffer_pruned",
                extra={"module": "store", "rows_deleted": excess},
            )
            return excess

    def row_count(self) -> int:
        """Return the total number of rows currently stored."""
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0])

    def query(
        self,
        since_timestamp: Optional[float] = None,
        until_timestamp: Optional[float] = None,
        sensor: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Query readings filtered by time range and sensor."""
        clauses: List[str] = []
        params: List[Any] = []
        if since_timestamp is not None:
            clauses.append("timestamp >= ?")
            params.append(float(since_timestamp))
        if until_timestamp is not None:
            clauses.append("timestamp <= ?")
            params.append(float(until_timestamp))
        if sensor is not None:
            clauses.append("sensor = ?")
            params.append(str(sensor))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, timestamp, sensor, value, unit, quality_flag "
            f"FROM readings {where} ORDER BY timestamp ASC LIMIT ?"
        )
        params.append(int(limit))
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def export_csv(
        self,
        path: Optional[str] = None,
        since_timestamp: Optional[float] = None,
        until_timestamp: Optional[float] = None,
        sensor: Optional[str] = None,
        limit: int = 1_000_000,
    ) -> str:
        """Export matching rows as CSV. Returns the CSV text and, if ``path``
        is provided, also writes it to disk.
        """
        rows = self.query(
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
            sensor=sensor,
            limit=limit,
        )
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["id", "timestamp", "sensor", "value", "unit", "quality_flag"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        text = buffer.getvalue()
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as fh:
                fh.write(text)
        return text

    def export_json(
        self,
        path: Optional[str] = None,
        since_timestamp: Optional[float] = None,
        until_timestamp: Optional[float] = None,
        sensor: Optional[str] = None,
        limit: int = 1_000_000,
    ) -> str:
        """Export matching rows as JSON. Returns the JSON text and, if ``path``
        is provided, writes it to disk.
        """
        rows = self.query(
            since_timestamp=since_timestamp,
            until_timestamp=until_timestamp,
            sensor=sensor,
            limit=limit,
        )
        text = json.dumps(rows, separators=(",", ":"))
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    def flush(self) -> None:
        """Commit any pending data and run a checkpoint."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("flush_failed", extra={"error": str(exc)})

    def close(self) -> None:
        """Flush and close the underlying SQLite connection."""
        with self._lock:
            self.flush()
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("close_failed", extra={"error": str(exc)})