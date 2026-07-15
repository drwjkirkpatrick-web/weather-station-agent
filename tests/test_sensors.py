"""
Integration tests for all sensor drivers.

These tests run every sensor in mock mode, verifying that:
  1. Each sensor initializes correctly
  2. read() returns a SensorReading with the expected metrics
  3. All declared metrics are present in the reading
  4. Values are within plausible physical ranges

WHY mock mode for integration tests:
  We don't have physical sensors on the development machine.  Mock mode
  exercises the full code path (SensorBase.read → _read_mock) and
  validates the interface contract for every sensor.
"""

import pytest

from weather_station.core.mock_manager import MockManager
from weather_station.sensors import (
    BME680Sensor, BME280Sensor, SHT31Sensor, PMS5003Sensor,
    SGP30Sensor, VEML7700Sensor, DS3231Sensor,
    AnemometerSensor, WindVaneSensor, RainGaugeSensor, MQ135Sensor,
)


# ── Helper ──────────────────────────────────────────────────────────

def assert_reading_valid(reading, sensor_name, expected_metrics):
    """Assert that a SensorReading has all expected metrics with valid values."""
    assert reading is not None, f"{sensor_name} returned None"
    assert reading.sensor_name == sensor_name
    for metric in expected_metrics:
        assert metric in reading.metrics, f"Missing metric '{metric}' in {sensor_name}"
        assert reading.metrics[metric] is not None, f"Metric '{metric}' is None in {sensor_name}"


# ── Per-sensor tests ─────────────────────────────────────────────────

class TestBME680:
    expected = ["temperature_c", "humidity_pct", "pressure_hpa", "gas_resistance_ohms", "iaq"]

    def test_mock_initialize(self):
        sensor = BME680Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = BME680Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "bme680", self.expected)

    def test_mock_values_in_range(self):
        sensor = BME680Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert -40 <= reading.metrics["temperature_c"] <= 55
        assert 0 <= reading.metrics["humidity_pct"] <= 100
        assert 950 <= reading.metrics["pressure_hpa"] <= 1060

    def test_health_check(self):
        sensor = BME680Sensor(mock_mode=True)
        sensor.initialize()
        health = sensor.health_check()
        assert health["name"] == "bme680"
        assert health["bus_type"] == "i2c"


class TestBME280:
    expected = ["temperature_c", "humidity_pct", "pressure_hpa"]

    def test_mock_initialize(self):
        sensor = BME280Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = BME280Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "bme280", self.expected)


class TestSHT31:
    expected = ["temperature_c", "humidity_pct"]

    def test_mock_initialize(self):
        sensor = SHT31Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = SHT31Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "sht31", self.expected)


class TestPMS5003:
    expected = ["pm1_0_ugm3", "pm2_5_ugm3", "pm10_ugm3",
                "pm_n_0_3um", "pm_n_0_5um", "pm_n_1_0um",
                "pm_n_2_5um", "pm_n_5_0um", "pm_n_10um"]

    def test_mock_initialize(self):
        sensor = PMS5003Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = PMS5003Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "pms5003", self.expected)

    def test_mock_pm25_in_range(self):
        sensor = PMS5003Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert 0 <= reading.metrics["pm2_5_ugm3"] <= 500


class TestSGP30:
    expected = ["co2_eq_ppm", "tvoc_ppb"]

    def test_mock_initialize(self):
        sensor = SGP30Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = SGP30Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "sgp30", self.expected)


class TestVEML7700:
    expected = ["light_lux"]

    def test_mock_initialize(self):
        sensor = VEML7700Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = VEML7700Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "veml7700", self.expected)

    def test_light_in_range(self):
        sensor = VEML7700Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert 0 <= reading.metrics["light_lux"] <= 120000


class TestDS3231:
    expected = ["temperature_c"]

    def test_mock_initialize(self):
        sensor = DS3231Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = DS3231Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert reading is not None
        assert reading.sensor_name == "ds3231"
        # Should have a temperature metric
        assert "temperature_c" in reading.metrics


class TestAnemometer:
    expected = ["wind_speed_mps"]

    def test_mock_initialize(self):
        sensor = AnemometerSensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = AnemometerSensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "anemometer", self.expected)

    def test_wind_speed_in_range(self):
        sensor = AnemometerSensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert 0 <= reading.metrics["wind_speed_mps"] <= 60


class TestWindVane:
    expected = ["wind_direction_deg", "wind_direction_cardinal"]

    def test_mock_initialize(self):
        sensor = WindVaneSensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = WindVaneSensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "wind_vane", self.expected)

    def test_direction_in_range(self):
        sensor = WindVaneSensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert 0 <= reading.metrics["wind_direction_deg"] <= 360


class TestRainGauge:
    expected = ["rain_mm", "rain_rate_mmh"]

    def test_mock_initialize(self):
        sensor = RainGaugeSensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = RainGaugeSensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "rain_gauge", self.expected)

    def test_rain_in_range(self):
        sensor = RainGaugeSensor(mock_mode=True)
        sensor.initialize()
        recording = sensor.read()
        assert 0 <= recording.metrics["rain_mm"]
        assert 0 <= recording.metrics["rain_rate_mmh"]


class TestMQ135:
    expected = ["co2_ppm", "air_quality"]

    def test_mock_initialize(self):
        sensor = MQ135Sensor(mock_mode=True)
        assert sensor.initialize() is True

    def test_mock_read(self):
        sensor = MQ135Sensor(mock_mode=True)
        sensor.initialize()
        reading = sensor.read()
        assert_reading_valid(reading, "mq135", self.expected)


# ── All-sensors integration ──────────────────────────────────────────

class TestAllSensors:
    """Test that all sensors can be initialized and read together."""

    def test_all_sensors_mock(self):
        """All 11 sensors should initialize and read in mock mode."""
        sensors = [
            BME680Sensor(mock_mode=True),
            BME280Sensor(mock_mode=True),
            SHT31Sensor(mock_mode=True),
            PMS5003Sensor(mock_mode=True),
            SGP30Sensor(mock_mode=True),
            VEML7700Sensor(mock_mode=True),
            DS3231Sensor(mock_mode=True),
            AnemometerSensor(mock_mode=True),
            WindVaneSensor(mock_mode=True),
            RainGaugeSensor(mock_mode=True),
            MQ135Sensor(mock_mode=True),
        ]

        for s in sensors:
            assert s.initialize() is True, f"{s.name} failed to initialize"

        for s in sensors:
            reading = s.read()
            assert reading is not None, f"{s.name} returned None"
            assert reading.sensor_name == s.name
            assert len(reading.metrics) > 0, f"{s.name} returned empty metrics"