"""Tests for the SensorBase abstract class and SensorReading dataclass."""

import pytest
from weather_station.core.sensor_base import SensorBase, SensorReading


class TestSensorReading:
    """Test the SensorReading dataclass."""

    def test_auto_timestamp(self):
        """If no timestamp is given, one should be auto-generated."""
        reading = SensorReading(sensor_name="test")
        assert reading.timestamp != ""
        # Should be ISO 8601
        assert "T" in reading.timestamp

    def test_explicit_timestamp(self):
        reading = SensorReading(sensor_name="test", timestamp="2024-01-15T10:00:00+00:00")
        assert reading.timestamp == "2024-01-15T10:00:00+00:00"

    def test_metrics_default_empty(self):
        reading = SensorReading(sensor_name="test")
        assert reading.metrics == {}
        assert reading.units == {}
        assert reading.metadata == {}


class FakeSensor(SensorBase):
    """A fake sensor for testing the base class behavior."""

    name = "fake"
    metrics = ["temperature_c"]
    bus_type = "i2c"
    description = "Fake sensor for testing"

    def __init__(self, mock_mode=True, fail_hardware=False):
        super().__init__(mock_mode=mock_mode)
        self._fail_hardware = fail_hardware

    def _read_hardware(self) -> SensorReading | None:
        if self._fail_hardware:
            return None
        return SensorReading(
            sensor_name=self.name,
            metrics={"temperature_c": 22.5},
            units={"temperature_c": "celsius"},
        )

    def _read_mock(self) -> SensorReading:
        return SensorReading(
            sensor_name=self.name,
            metrics={"temperature_c": 20.0},
            units={"temperature_c": "celsius"},
        )


class TestSensorBase:
    """Test SensorBase lifecycle and health tracking."""

    def test_mock_initialize(self):
        sensor = FakeSensor(mock_mode=True)
        assert sensor.initialize() is True
        assert sensor._initialized is True

    def test_mock_read(self):
        sensor = FakeSensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert reading is not None
        assert reading.sensor_name == "fake"
        assert reading.metrics["temperature_c"] == 20.0

    def test_hardware_read_success(self):
        sensor = FakeSensor(mock_mode=False)
        sensor.initialize()
        reading = sensor.read()
        assert reading is not None
        assert reading.metrics["temperature_c"] == 22.5

    def test_hardware_read_failure(self):
        sensor = FakeSensor(mock_mode=False, fail_hardware=True)
        sensor.initialize()
        reading = sensor.read()
        assert reading is None

    def test_health_tracking_on_success(self):
        sensor = FakeSensor(mock_mode=True)
        sensor.initialize()
        sensor.read()
        health = sensor.health_check()
        assert health["health_score"] == 1.0
        assert health["consecutive_failures"] == 0
        assert health["mock_mode"] is True

    def test_health_tracking_on_failure(self):
        sensor = FakeSensor(mock_mode=False, fail_hardware=True)
        sensor.initialize()
        sensor.read()
        sensor.read()
        health = sensor.health_check()
        assert health["health_score"] < 1.0
        assert health["consecutive_failures"] == 2

    def test_is_healthy_property(self):
        sensor = FakeSensor(mock_mode=True)
        sensor.initialize()
        assert sensor.is_healthy is True

    def test_read_exception_handled(self):
        """If _read_hardware raises, read() should catch it and return None."""

        class ExceptionSensor(SensorBase):
            name = "exc"
            metrics = ["val"]
            bus_type = "i2c"

            def _read_hardware(self):
                raise OSError("hardware error")

        sensor = ExceptionSensor(mock_mode=False)
        sensor.initialize()
        reading = sensor.read()
        assert reading is None

    def test_uninitialized_sensor_returns_none(self):
        """A sensor that hasn't been initialized should return None on read."""
        sensor = FakeSensor(mock_mode=False)
        sensor._initialized = False
        reading = sensor.read()
        assert reading is None

    def test_health_check_dict(self):
        sensor = FakeSensor(mock_mode=True)
        sensor.initialize()
        sensor.read()
        health = sensor.health_check()
        assert "name" in health
        assert "bus_type" in health
        assert "initialized" in health
        assert "health_score" in health
        assert "metrics" in health
        assert health["name"] == "fake"