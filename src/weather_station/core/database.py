"""
SQLite database layer for weather station readings.

WHY SQLite:
  The Pi Zero 2 W has 512 MB RAM and runs from SD card.  SQLite is the
  only sensible embedded database — zero config, file-based, and handles
  concurrent reads from the dashboard while the recorder writes.

WHY WAL mode:
  Write-Ahead Logging allows simultaneous readers and a writer without
  locking, which is essential because the Flask dashboard will be polling
  while the recorder inserts new readings.

NOTE: All timestamps are stored as ISO 8601 strings (UTC) for
portability.  We use TEXT columns rather than SQLite datetime types
because Python's datetime.isoformat() round-trips cleanly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# ── Schema definition ───────────────────────────────────────────────────
# Single table holds all sensor readings in a flexible key-value style.
# This makes it trivial to add new sensors without schema migrations.

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,           -- ISO 8601 UTC
    station_id  TEXT    NOT NULL,
    sensor_name TEXT    NOT NULL,           -- e.g. 'bme680', 'pms5003'
    metric      TEXT    NOT NULL,           -- e.g. 'temperature_c', 'pm25'
    value       REAL,                        -- numeric reading
    unit        TEXT,                        -- 'celsius', 'hpa', 'ugm3', etc.
    metadata    TEXT                          -- JSON blob for extra data
);

CREATE INDEX IF NOT EXISTS idx_readings_time
    ON readings (timestamp);

CREATE INDEX IF NOT EXISTS idx_readings_sensor_metric
    ON readings (sensor_name, metric);

CREATE INDEX IF NOT EXISTS idx_readings_station
    ON readings (station_id);

-- ── Alerts table ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    station_id  TEXT    NOT NULL,
    sensor_name TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    value       REAL,
    threshold   REAL,
    operator    TEXT,                        -- 'gt', 'lt', 'gte', 'lte'
    severity     TEXT,                        -- 'info', 'warning', 'critical'
    message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_time
    ON alerts (timestamp);

-- ── Daily summaries table (populated by report generator) ──────────────
CREATE TABLE IF NOT EXISTS daily_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,             -- 'YYYY-MM-DD'
    station_id  TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    min_value   REAL,
    max_value   REAL,
    avg_value   REAL,
    count       INTEGER,
    UNIQUE (date, station_id, metric)
);

CREATE INDEX IF NOT EXISTS idx_summaries_date
    ON daily_summaries (date);
"""


class WeatherDatabase:
    """Thread-safe SQLite wrapper for weather data storage.

    Thread safety:
        Each call to ``get_connection()`` returns a fresh connection from
        a thread-local pool.  SQLite connections are not shareable across
        threads by default, so we use a lock for write operations.
    """

    def __init__(self, db_path: str | Path = "data/weather.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indices if they don't exist."""
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Enable WAL mode for concurrent read/write support
            conn.execute("PRAGMA journal_mode=WOL")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a SQLite connection, closing it on exit."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # access columns by name
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Write operations ───────────────────────────────────────────────

    def insert_reading(
        self,
        timestamp: str | None = None,
        station_id: str = "ws01",
        sensor_name: str = "",
        metric: str = "",
        value: float | None = None,
        unit: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert a single sensor reading. Returns the row ID."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        sql = (
            "INSERT INTO readings "
            "(timestamp, station_id, sensor_name, metric, value, unit, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(sql, (ts, station_id, sensor_name, metric, value, unit, meta_json))
            return cur.lastrowid

    def insert_readings_batch(
        self,
        readings: list[dict[str, Any]],
    ) -> int:
        """Insert multiple readings in a single transaction. Returns count."""
        if not readings:
            return 0
        sql = (
            "INSERT INTO readings "
            "(timestamp, station_id, sensor_name, metric, value, unit, metadata) "
            "VALUES (:timestamp, :station_id, :sensor_name, :metric, :value, :unit, :metadata)"
        )
        # Pre-serialize metadata to JSON strings and fill defaults
        rows: list[dict[str, Any]] = []
        for r in readings:
            row = dict(r)
            if row.get("metadata") and isinstance(row["metadata"], dict):
                row["metadata"] = json.dumps(row["metadata"])
            if "timestamp" not in row or row["timestamp"] is None:
                row["timestamp"] = datetime.now(timezone.utc).isoformat()
            # Ensure all named params are present (sqlite requires all)
            row.setdefault("station_id", "ws01")
            row.setdefault("unit", "")
            row.setdefault("metadata", None)
            rows.append(row)

        with self._write_lock, self._connect() as conn:
            conn.executemany(sql, rows)
            return len(rows)

    def insert_alert(
        self,
        timestamp: str | None = None,
        station_id: str = "ws01",
        sensor_name: str = "",
        metric: str = "",
        value: float | None = None,
        threshold: float | None = None,
        operator: str = "",
        severity: str = "warning",
        message: str = "",
    ) -> int:
        """Insert an alert record. Returns the row ID."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        sql = (
            "INSERT INTO alerts "
            "(timestamp, station_id, sensor_name, metric, value, threshold, "
            "operator, severity, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(sql, (ts, station_id, sensor_name, metric,
                                     value, threshold, operator, severity, message))
            return cur.lastrowid

    def upsert_daily_summary(
        self,
        date: str,
        station_id: str,
        metric: str,
        min_value: float,
        max_value: float,
        avg_value: float,
        count: int,
    ) -> None:
        """Insert or replace a daily summary row."""
        sql = (
            "INSERT INTO daily_summaries "
            "(date, station_id, metric, min_value, max_value, avg_value, count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (date, station_id, metric) DO UPDATE SET "
            "min_value=excluded.min_value, max_value=excluded.max_value, "
            "avg_value=excluded.avg_value, count=excluded.count"
        )
        with self._write_lock, self._connect() as conn:
            conn.execute(sql, (date, station_id, metric, min_value, max_value, avg_value, count))

    # ── Read operations ────────────────────────────────────────────────

    def get_latest_readings(self, station_id: str = "ws01") -> list[dict[str, Any]]:
        """Return the most recent reading for each (sensor, metric) pair."""
        sql = """
            SELECT r.* FROM readings r
            INNER JOIN (
                SELECT sensor_name, metric, MAX(timestamp) AS max_ts
                FROM readings
                WHERE station_id = ?
                GROUP BY sensor_name, metric
            ) latest
            ON r.sensor_name = latest.sensor_name
               AND r.metric = latest.metric
               AND r.timestamp = latest.max_ts
            WHERE r.station_id = ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (station_id, station_id)).fetchall()
            return [dict(r) for r in rows]

    def get_readings(
        self,
        sensor_name: str | None = None,
        metric: str | None = None,
        station_id: str = "ws01",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query readings with optional filters."""
        clauses = ["station_id = ?"]
        params: list[Any] = [station_id]
        if sensor_name:
            clauses.append("sensor_name = ?")
            params.append(sensor_name)
        if metric:
            clauses.append("metric = ?")
            params.append(metric)
        if start_time:
            clauses.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            clauses.append("timestamp <= ?")
            params.append(end_time)

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM readings WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_readings_series(
        self,
        sensor_name: str,
        metric: str,
        station_id: str = "ws01",
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Return a time series for a specific sensor+metric over N hours."""
        sql = (
            "SELECT timestamp, value, unit FROM readings "
            "WHERE station_id = ? AND sensor_name = ? AND metric = ? "
            "AND timestamp >= datetime('now', ?) "
            "ORDER BY timestamp ASC"
        )
        with self._connect() as conn:
            rows = conn.execute(
                sql, (station_id, sensor_name, metric, f"-{hours} hours")
            ).fetchall()
            return [dict(r) for r in rows]

    def get_alerts(
        self,
        station_id: str = "ws01",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent alerts, newest first."""
        sql = (
            "SELECT * FROM alerts WHERE station_id = ? "
            "ORDER BY timestamp DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (station_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_daily_summaries(
        self,
        date: str | None = None,
        station_id: str = "ws01",
    ) -> list[dict[str, Any]]:
        """Return daily summaries, optionally filtered by date."""
        if date:
            sql = "SELECT * FROM daily_summaries WHERE station_id = ? AND date = ?"
            params: list[Any] = [station_id, date]
        else:
            sql = "SELECT * FROM daily_summaries WHERE station_id = ? ORDER BY date DESC"
            params = [station_id]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # ── Maintenance ────────────────────────────────────────────────────

    def prune_old_data(self, retention_days: int = 365) -> int:
        """Delete readings older than retention_days. Returns count deleted."""
        sql = "DELETE FROM readings WHERE timestamp < datetime('now', ?)"
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(sql, (f"-{retention_days} days",))
            return cur.rowcount

    def get_table_stats(self) -> dict[str, int]:
        """Return row counts for each table (for health check)."""
        tables = ["readings", "alerts", "daily_summaries"]
        stats: dict[str, int] = {}
        with self._connect() as conn:
            for t in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                stats[t] = count
        return stats