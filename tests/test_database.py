"""Tests for the database layer (core/database.py)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weather_station.core.database import WeatherDatabase


class TestDatabaseInit:
    """Test database initialization."""

    def test_creates_db_file(self, tmp_db_path):
        db = WeatherDatabase(tmp_db_path)
        assert Path(tmp_db_path).exists()

    def test_schema_tables_exist(self, db):
        """All three tables should exist after init."""
        with db._connect() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            names = {t[0] for t in tables}
            assert "readings" in names
            assert "alerts" in names
            assert "daily_summaries" in names

    def test_wal_mode_enabled(self, db):
        """WAL mode should be enabled for concurrent access."""
        with db._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"


class TestReadings:
    """Test reading insertion and retrieval."""

    def test_insert_single_reading(self, db):
        row_id = db.insert_reading(
            station_id="test01",
            sensor_name="bme680",
            metric="temperature_c",
            value=22.5,
            unit="celsius",
        )
        assert row_id > 0

    def test_insert_with_default_timestamp(self, db):
        """If no timestamp is given, one should be auto-generated."""
        row_id = db.insert_reading(
            sensor_name="bme680", metric="temperature_c", value=20.0,
        )
        assert row_id > 0
        readings = db.get_readings(sensor_name="bme680", station_id="ws01")
        assert len(readings) == 1
        assert readings[0]["timestamp"] != ""

    def test_insert_batch(self, db):
        readings = [
            {"sensor_name": "bme680", "metric": "temperature_c", "value": 22.0, "station_id": "test01"},
            {"sensor_name": "bme680", "metric": "humidity_pct", "value": 55.0, "station_id": "test01"},
            {"sensor_name": "pms5003", "metric": "pm2_5_ugm3", "value": 12.0, "station_id": "test01"},
        ]
        count = db.insert_readings_batch(readings)
        assert count == 3

    def test_insert_empty_batch(self, db):
        count = db.insert_readings_batch([])
        assert count == 0

    def test_insert_batch_with_metadata(self, db):
        """Metadata dict should be serialized to JSON."""
        readings = [
            {
                "sensor_name": "bme680",
                "metric": "temperature_c",
                "value": 22.0,
                "station_id": "test01",
                "metadata": {"calibration": "2024-01-01", "sensor_fw": "1.2"},
            },
        ]
        db.insert_readings_batch(readings)
        result = db.get_readings(sensor_name="bme680", station_id="test01")
        assert result[0]["metadata"] is not None
        meta = json.loads(result[0]["metadata"])
        assert meta["sensor_fw"] == "1.2"

    def test_get_latest_readings(self, db):
        """get_latest_readings should return only the most recent per metric."""
        ts1 = "2024-01-15T10:00:00+00:00"
        ts2 = "2024-01-15T11:00:00+00:00"
        db.insert_reading(timestamp=ts1, station_id="test01", sensor_name="bme680",
                          metric="temperature_c", value=20.0)
        db.insert_reading(timestamp=ts2, station_id="test01", sensor_name="bme680",
                          metric="temperature_c", value=22.0)
        latest = db.get_latest_readings("test01")
        assert len(latest) == 1
        assert latest[0]["value"] == 22.0

    def test_get_readings_with_filters(self, db):
        db.insert_reading(station_id="test01", sensor_name="bme680", metric="temperature_c", value=22.0)
        db.insert_reading(station_id="test01", sensor_name="bme680", metric="humidity_pct", value=55.0)
        db.insert_reading(station_id="test01", sensor_name="pms5003", metric="pm2_5_ugm3", value=12.0)

        # Filter by sensor
        bme_only = db.get_readings(sensor_name="bme680", station_id="test01")
        assert len(bme_only) == 2

        # Filter by metric
        temp_only = db.get_readings(metric="temperature_c", station_id="test01")
        assert len(temp_only) == 1

    def test_get_readings_series(self, db):
        """get_readings_series should return chronological time series."""
        # Use recent timestamps (within the last 24 hours) so SQLite's
        # datetime('now', '-N hours') filter can find them.
        now = datetime.now(timezone.utc)
        for i in range(5):
            ts = now - timedelta(hours=5 - i)
            db.insert_reading(
                timestamp=ts.isoformat(),
                station_id="test01",
                sensor_name="bme680",
                metric="temperature_c",
                value=20.0 + i,
            )
        series = db.get_readings_series("bme680", "temperature_c", "test01", hours=24)
        assert len(series) == 5
        # Should be in ascending order
        assert series[0]["value"] == 20.0
        assert series[4]["value"] == 24.0


class TestAlerts:
    """Test alert insertion and retrieval."""

    def test_insert_alert(self, db):
        alert_id = db.insert_alert(
            station_id="test01",
            sensor_name="bme680",
            metric="temperature_c",
            value=40.0,
            threshold=35.0,
            operator="gt",
            severity="warning",
            message="High temperature: 40.0C",
        )
        assert alert_id > 0

    def test_get_alerts(self, db):
        db.insert_alert(station_id="test01", sensor_name="bme680", metric="temperature_c",
                        value=40.0, threshold=35.0, operator="gt", severity="warning", message="test1")
        db.insert_alert(station_id="test01", sensor_name="pms5003", metric="pm2_5_ugm3",
                        value=50.0, threshold=35.0, operator="gt", severity="critical", message="test2")

        alerts = db.get_alerts("test01")
        assert len(alerts) == 2
        # Should be newest first
        assert alerts[0]["severity"] == "critical"


class TestDailySummaries:
    """Test daily summary upsert and retrieval."""

    def test_upsert_summary(self, db):
        db.upsert_daily_summary("2024-01-15", "test01", "bme680.temperature_c",
                                10.0, 25.0, 17.5, 1440)
        summaries = db.get_daily_summaries(date="2024-01-15", station_id="test01")
        assert len(summaries) == 1
        assert summaries[0]["avg_value"] == 17.5

    def test_upsert_replaces(self, db):
        """Upserting the same date+metric should replace, not duplicate."""
        db.upsert_daily_summary("2024-01-15", "test01", "bme680.temperature_c",
                                10.0, 25.0, 17.5, 1440)
        db.upsert_daily_summary("2024-01-15", "test01", "bme680.temperature_c",
                                12.0, 27.0, 19.0, 2880)
        summaries = db.get_daily_summaries(date="2024-01-15", station_id="test01")
        assert len(summaries) == 1
        assert summaries[0]["avg_value"] == 19.0


class TestMaintenance:
    """Test maintenance operations."""

    def test_prune_old_data(self, db):
        """Old readings should be deleted by prune_old_data."""
        # Insert an old reading (2 years ago)
        db.insert_reading(
            timestamp="2022-01-15T10:00:00+00:00",
            station_id="test01", sensor_name="bme680",
            metric="temperature_c", value=20.0,
        )
        deleted = db.prune_old_data(retention_days=365)
        assert deleted == 1

    def test_get_table_stats(self, db):
        db.insert_reading(station_id="test01", sensor_name="bme680",
                          metric="temperature_c", value=20.0)
        stats = db.get_table_stats()
        assert stats["readings"] == 1
        assert stats["alerts"] == 0