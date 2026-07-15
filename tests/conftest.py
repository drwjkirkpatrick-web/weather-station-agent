"""
Test fixtures shared across all weather station tests.

WHY a conftest.py:
  Pytest loads this file automatically before running tests in this
  directory.  It gives us shared fixtures (temp databases, mock configs)
  that every test can request by name — no duplication.
"""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from weather_station.core.config import Config
from weather_station.core.database import WeatherDatabase
from weather_station.core.mock_manager import MockManager


@pytest.fixture
def tmp_db_path() -> str:
    """Provide a temporary database path that gets cleaned up."""
    tmp = tempfile.mkdtemp(prefix="weather_test_")
    return str(Path(tmp) / "test_weather.db")


@pytest.fixture
def db(tmp_db_path: str) -> WeatherDatabase:
    """Provide a fresh WeatherDatabase instance with a temp file."""
    return WeatherDatabase(tmp_db_path)


@pytest.fixture
def mock_config() -> Config:
    """Provide a Config with mock_mode=True for testing without hardware."""
    return Config(
        station_name="test-station",
        station_id="test01",
        mock_mode=True,
        verbose=False,
    )


@pytest.fixture
def default_config() -> Config:
    """Provide the default Config."""
    return Config.default()


@pytest.fixture
def mock_manager() -> MockManager:
    """Provide a MockManager with a fixed seed for reproducible test data."""
    return MockManager(seed=42)


@pytest.fixture
def sample_readings() -> list[dict[str, Any]]:
    """Provide a batch of sample readings for testing the recorder and alert engine."""
    return [
        {
            "timestamp": "2024-01-15T10:00:00+00:00",
            "station_id": "test01",
            "sensor_name": "bme680",
            "metric": "temperature_c",
            "value": 22.5,
            "unit": "celsius",
        },
        {
            "timestamp": "2024-01-15T10:00:00+00:00",
            "station_id": "test01",
            "sensor_name": "bme680",
            "metric": "humidity_pct",
            "value": 55.0,
            "unit": "percent",
        },
        {
            "timestamp": "2024-01-15T10:00:00+00:00",
            "station_id": "test01",
            "sensor_name": "bme680",
            "metric": "pressure_hpa",
            "value": 1013.25,
            "unit": "hpa",
        },
        {
            "timestamp": "2024-01-15T10:00:00+00:00",
            "station_id": "test01",
            "sensor_name": "pms5003",
            "metric": "pm2_5_ugm3",
            "value": 12.5,
            "unit": "ugm3",
        },
        {
            "timestamp": "2024-01-15T10:00:00+00:00",
            "station_id": "test01",
            "sensor_name": "anemometer",
            "metric": "wind_speed_mps",
            "value": 3.2,
            "unit": "mps",
        },
    ]