"""Tests for the ESP32 sensor drivers and ingest API.

These tests run on the development machine (Linux) using mock mode.
They verify that all ESP32 sensor drivers:
1. Initialize successfully in mock mode
2. Return SensorReading with correct metric keys
3. Serialize to JSON-compatible dicts
4. Are importable and py_compile clean

The ingest API tests verify JSON payload validation and database storage.
"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

# Allow relative imports from ESP32 modules during testing
ESP32_DIR = Path(__file__).parent.parent / "esp32"
SENSORS_DIR = ESP32_DIR / "sensors"
sys.path.insert(0, str(ESP32_DIR))

# Ensure package namespaces exist without loading real __init__.py
for pkg in ["sensors"]:
    sys.modules.setdefault(pkg, types.ModuleType(pkg))


def _load_esp32_module(name, path):
    """Load an ESP32 module directly (bypass MicroPython-only imports)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def sensor_base():
    """Load the ESP32 SensorBase."""
    return _load_esp32_module("sensor_base", ESP32_DIR / "sensor_base.py")


@pytest.fixture
def mock_sensor_cls(sensor_base):
    """Return a minimal mock sensor for testing the base class."""
    SensorBase = sensor_base.SensorBase
    SensorReading = sensor_base.SensorReading

    class MockSensor(SensorBase):
        name = "mock"
        metrics = ["temperature_c", "humidity_pct"]
        bus_type = "i2c"

        def _read_hardware(self):
            return SensorReading(
                sensor_name=self.name,
                metrics={"temperature_c": 22.5, "humidity_pct": 55.0},
                units={"temperature_c": "celsius", "humidity_pct": "percent"},
            )

        def _read_mock(self):
            return self._read_hardware()

    return MockSensor


# ── SensorBase tests ───────────────────────────────────────────

def test_sensor_base_mock_initialization(mock_sensor_cls):
    s = mock_sensor_cls(mock_mode=True)
    assert s.initialize() is True
    assert s.is_healthy is True


def test_sensor_base_mock_read(mock_sensor_cls):
    s = mock_sensor_cls(mock_mode=True)
    s.initialize()
    r = s.read()
    assert r is not None
    assert r.sensor_name == "mock"
    assert "temperature_c" in r.metrics
    assert "humidity_pct" in r.metrics


def test_sensor_base_reading_to_dict(mock_sensor_cls):
    s = mock_sensor_cls(mock_mode=True)
    s.initialize()
    r = s.read()
    d = r.to_dict()
    assert d["sensor_name"] == "mock"
    assert isinstance(d["metrics"], dict)
    assert isinstance(d["timestamp"], str)
    # Should be JSON-serializable
    _ = json.dumps(d)


def test_sensor_base_health_check(mock_sensor_cls):
    s = mock_sensor_cls(mock_mode=True)
    s.initialize()
    s.read()
    hc = s.health_check()
    assert hc["name"] == "mock"
    assert hc["bus_type"] == "i2c"
    assert hc["health_score"] > 0.5
    assert hc["initialized"] is True


def test_sensor_base_uninitialized_read(mock_sensor_cls):
    s = mock_sensor_cls(mock_mode=False)
    # Don't initialize
    r = s.read()
    assert r is None


# ── Individual ESP32 sensor driver tests ───────────────────────

def test_bme280_mock(sensor_base):
    BME280Sensor = _load_esp32_module("sensors.bme280", SENSORS_DIR / "bme280.py").BME280Sensor
    SensorReading = sensor_base.SensorReading

    # Fake I2C object for mock mode
    class FakeI2C:
        pass

    s = BME280Sensor(i2c_bus=FakeI2C(), mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert set(r.metrics.keys()) == {"temperature_c", "humidity_pct", "pressure_hpa"}
    assert r.units["temperature_c"] == "celsius"


def test_dht22_mock(sensor_base):
    DHT22Sensor = _load_esp32_module("sensors.dht22", SENSORS_DIR / "dht22.py").DHT22Sensor
    s = DHT22Sensor(pin=4, mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert set(r.metrics.keys()) == {"temperature_c", "humidity_pct"}


def test_ds18b20_mock(sensor_base):
    DS18B20Sensor = _load_esp32_module("sensors.ds18b20", SENSORS_DIR / "ds18b20.py").DS18B20Sensor
    s = DS18B20Sensor(pin=4, mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert "temperature_c" in r.metrics


def test_anemometer_mock(sensor_base):
    AnemometerSensor = _load_esp32_module("sensors.anemometer", SENSORS_DIR / "anemometer.py").AnemometerSensor
    s = AnemometerSensor(pin=14, mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert "wind_speed_mps" in r.metrics
    assert r.metrics["wind_speed_mps"] >= 0.0


def test_rain_gauge_mock(sensor_base):
    RainGaugeSensor = _load_esp32_module("sensors.rain_gauge", SENSORS_DIR / "rain_gauge.py").RainGaugeSensor
    s = RainGaugeSensor(pin=12, mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert "rain_mm" in r.metrics
    assert "rain_rate_mmh" in r.metrics


def test_bh1750_mock(sensor_base):
    BH1750Sensor = _load_esp32_module("sensors.bh1750", SENSORS_DIR / "bh1750.py").BH1750Sensor

    class FakeI2C:
        pass

    s = BH1750Sensor(i2c_bus=FakeI2C(), mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert "light_lux" in r.metrics


def test_mq135_mock(sensor_base):
    MQ135Sensor = _load_esp32_module("sensors.mq135", SENSORS_DIR / "mq135.py").MQ135Sensor
    s = MQ135Sensor(pin=34, mock_mode=True)
    assert s.initialize() is True
    r = s.read()
    assert r is not None
    assert "co2_ppm" in r.metrics
    assert "air_quality_index" in r.metrics


# ── Networking tests (mock mode) ───────────────────────────────

def test_network_manager_import():
    """Networking module loads without MicroPython modules."""
    # The networking module will fail to import on CPython because of
    # network/umqtt/urequests.  We verify it exists and the source is clean.
    path = ESP32_DIR / "networking.py"
    assert path.exists()
    import py_compile
    py_compile.compile(str(path), doraise=True)


# ── Ingest API tests ───────────────────────────────────────────

from weather_station.core.database import WeatherDatabase
from weather_station.ingest.api import create_ingest_blueprint
from flask import Flask
import tempfile


@pytest.fixture
def test_app():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = WeatherDatabase(db_path)
    app = Flask(__name__)
    app.register_blueprint(create_ingest_blueprint(db))
    yield app, db
    os.unlink(db_path)


def test_ingest_valid_payload(test_app):
    app, db = test_app
    payload = {
        "station_id": "esp32-01",
        "station_name": "Garden Node",
        "readings": [
            {
                "sensor_name": "bme280",
                "timestamp": "2024-01-15T12:00:00Z",
                "metrics": {"temperature_c": 22.5, "humidity_pct": 55.0},
                "units": {"temperature_c": "celsius", "humidity_pct": "percent"},
            }
        ],
    }
    with app.test_client() as client:
        resp = client.post("/api/ingest", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is True
        assert data["inserted"] == 2  # 2 metrics


def test_ingest_missing_fields(test_app):
    app, db = test_app
    payload = {"station_id": "esp32-01"}  # missing readings
    with app.test_client() as client:
        resp = client.post("/api/ingest", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert data["success"] is False


def test_ingest_namespaced_storage(test_app):
    app, db = test_app
    payload = {
        "station_id": "esp32-01",
        "readings": [
            {
                "sensor_name": "bme280",
                "timestamp": "2024-01-15T12:00:00Z",
                "metrics": {"temperature_c": 22.5},
                "units": {"temperature_c": "celsius"},
            }
        ],
    }
    with app.test_client() as client:
        client.post("/api/ingest", data=json.dumps(payload), content_type="application/json")
        rows = db.get_readings(station_id="esp32-01", limit=10)
        assert len(rows) >= 1
        assert rows[0]["sensor_name"] == "esp32-01/bme280"


def test_api_nodes(test_app):
    app, db = test_app
    payload = {
        "station_id": "esp32-01",
        "readings": [
            {
                "sensor_name": "bme280",
                "timestamp": "2024-01-15T12:00:00Z",
                "metrics": {"temperature_c": 22.5},
                "units": {"temperature_c": "celsius"},
            }
        ],
    }
    with app.test_client() as client:
        client.post("/api/ingest", data=json.dumps(payload), content_type="application/json")
        resp = client.get("/api/nodes")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert any(n["station_id"] == "esp32-01" for n in data["nodes"])
