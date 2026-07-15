"""Tests for the data recorder and exporter."""

import json
from pathlib import Path

import pytest

from weather_station.core.database import WeatherDatabase
from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.recording.data_recorder import DataRecorder
from weather_station.recording.exporter import DataExporter


class MockRecorderSensor(SensorBase):
    """Simple sensor that returns a fixed reading for recorder tests."""

    name = "mock_test"
    metrics = ["temperature_c"]
    bus_type = "i2c"
    description = "Mock sensor for recorder tests"

    def __init__(self, mock_mode=True):
        super().__init__(mock_mode=mock_mode)

    def _read_hardware(self) -> SensorReading | None:
        return SensorReading(
            sensor_name=self.name,
            metrics={"temperature_c": 22.0},
            units={"temperature_c": "celsius"},
        )

    def _read_mock(self) -> SensorReading:
        return SensorReading(
            sensor_name=self.name,
            metrics={"temperature_c": 20.0},
            units={"temperature_c": "celsius"},
        )


class FailingSensor(SensorBase):
    """Sensor that always fails to read."""

    name = "failing"
    metrics = ["val"]
    bus_type = "i2c"
    description = "Always fails"

    def _read_hardware(self) -> SensorReading | None:
        return None

    def _read_mock(self) -> SensorReading:
        return SensorReading(sensor_name=self.name, metrics={"val": 0.0})


class TestDataRecorder:
    """Test the DataRecorder."""

    def test_record_single(self, db):
        sensor = MockRecorderSensor(mock_mode=True)
        sensor.initialize()
        recorder = DataRecorder(db, [sensor], station_id="test01")
        reading = recorder.record_single(sensor)
        assert reading is not None
        assert reading.metrics["temperature_c"] == 20.0
        # Verify it was stored
        readings = db.get_readings(station_id="test01")
        assert len(readings) == 1

    def test_record_cycle(self, db):
        """Test a single recording cycle with multiple sensors."""
        s1 = MockRecorderSensor(mock_mode=True)
        s1.initialize()
        s2 = FailingSensor(mock_mode=True)
        s2.initialize()
        recorder = DataRecorder(db, [s1, s2], station_id="test01")
        recorder._record_cycle()
        # s1 should have produced 1 reading, s2 in mock mode returns 0.0
        readings = db.get_readings(station_id="test01")
        assert len(readings) == 2  # both sensors in mock mode return readings

    def test_health_check(self, db):
        sensor = MockRecorderSensor(mock_mode=True)
        sensor.initialize()
        recorder = DataRecorder(db, [sensor], station_id="test01")
        health = recorder.health_check()
        assert health["running"] is False
        assert health["active_sensors"] == 1

    def test_start_stop(self, db):
        """Test that start/stop works with a short interval."""
        sensor = MockRecorderSensor(mock_mode=True)
        sensor.initialize()
        recorder = DataRecorder(
            db, [sensor], station_id="test01", sample_interval=1
        )
        recorder.start()
        assert recorder._running is True
        # Wait for at least one cycle
        import time
        time.sleep(2.5)
        recorder.stop()
        assert recorder._running is False
        assert recorder._cycle_count >= 1
        # Verify data was recorded
        readings = db.get_readings(station_id="test01")
        assert len(readings) > 0


class TestDataExporter:
    """Test the DataExporter."""

    def test_export_csv(self, db, tmp_path):
        db.insert_reading(station_id="test01", sensor_name="bme680",
                          metric="temperature_c", value=22.0, unit="celsius")
        db.insert_reading(station_id="test01", sensor_name="bme680",
                          metric="humidity_pct", value=55.0, unit="percent")

        exporter = DataExporter(db)
        output = tmp_path / "export.csv"
        count = exporter.export_csv(output, station_id="test01")
        assert count == 2
        assert output.exists()
        content = output.read_text()
        assert "temperature_c" in content
        assert "humidity_pct" in content

    def test_export_json(self, db, tmp_path):
        db.insert_reading(station_id="test01", sensor_name="bme680",
                          metric="temperature_c", value=22.0, unit="celsius")

        exporter = DataExporter(db)
        output = tmp_path / "export.json"
        count = exporter.export_json(output, station_id="test01")
        assert count == 1
        assert output.exists()
        data = json.loads(output.read_text())
        assert len(data) == 1
        assert data[0]["metric"] == "temperature_c"

    def test_export_daily_summary_csv(self, db, tmp_path):
        db.upsert_daily_summary("2024-01-15", "test01", "bme680.temperature_c",
                                 10.0, 25.0, 17.5, 1440)
        exporter = DataExporter(db)
        output = tmp_path / "summary.csv"
        count = exporter.export_daily_summary_csv(output, station_id="test01")
        assert count == 1
        assert output.exists()

    def test_export_with_sensor_filter(self, db, tmp_path):
        db.insert_reading(station_id="test01", sensor_name="bme680",
                          metric="temperature_c", value=22.0)
        db.insert_reading(station_id="test01", sensor_name="pms5003",
                          metric="pm2_5_ugm3", value=12.0)
        exporter = DataExporter(db)
        output = tmp_path / "filtered.csv"
        count = exporter.export_csv(output, sensor_name="bme680", station_id="test01")
        assert count == 1
        content = output.read_text()
        assert "temperature_c" in content
        assert "pm2_5_ugm3" not in content