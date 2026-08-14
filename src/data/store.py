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

    def __init__(self, db_path: str = DB_PATH, logger: Optional[logging.Logger] = None, max_rows: int = MAX_ROWS):
        self._db_path = db_path
        self._max_rows = int(max_rows)
        # Reentrant so _prune_if_needed() can be called from within a method
        # that already holds the lock (e.g. write_many) without deadlocking.
        self._lock = threading.RLock()
        self._logger = logger or logging.getLogger(__name__)
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._logger.info("store_initialised", extra={"component": "store", "path": db_path})

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._logger.info("store_schema_verified", extra={"component": "store"})

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
                extra={"component": "store", "rows_deleted": excess},
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

    def distinct_sensors(self) -> List[str]:
        """Return every sensor name that currently has at least one row."""
        with self._lock:
            cur = self._conn.execute("SELECT DISTINCT sensor FROM readings ORDER BY sensor")
            return [row[0] for row in cur.fetchall()]

    def summarize(
        self,
        since_timestamp: Optional[float] = None,
        until_timestamp: Optional[float] = None,
        sensors: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return per-sensor aggregates (count/min/max/avg/last) for the given filter.

        Computed with SQL aggregates over the existing timestamp/sensor indexes so a
        multi-hour or multi-day summary costs one indexed pass instead of shipping every
        row back to the caller -- important when the caller is about to relay the result
        over a low-bandwidth radio link.
        """
        clauses: List[str] = []
        params: List[Any] = []
        if since_timestamp is not None:
            clauses.append("timestamp >= ?")
            params.append(float(since_timestamp))
        if until_timestamp is not None:
            clauses.append("timestamp <= ?")
            params.append(float(until_timestamp))
        if sensors is not None:
            sensors = list(sensors)
            if not sensors:
                return []
            clauses.append(f"sensor IN ({','.join('?' for _ in sensors)})")
            params.extend(str(s) for s in sensors)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT sensor, unit, COUNT(*) AS count, MIN(value) AS min_value, "
            "MAX(value) AS max_value, AVG(value) AS avg_value, "
            "MAX(timestamp) AS last_timestamp "
            f"FROM readings {where} GROUP BY sensor, unit ORDER BY sensor"
        )
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