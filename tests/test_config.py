"""Tests for the configuration system (core/config.py)."""

import tempfile
from pathlib import Path

from weather_station.core.config import Config, SensorConfig, RecordingConfig, AlertConfig


class TestConfigDefaults:
    """Verify default configuration values are sensible."""

    def test_default_config_creates(self):
        config = Config.default()
        assert config.station_name == "weather-station-01"
        assert config.station_id == "ws01"
        assert config.mock_mode is False

    def test_default_sensors(self):
        config = Config.default()
        assert config.sensors.bme680_enabled is True
        assert config.sensors.pms5003_enabled is True
        assert config.sensors.anemometer_enabled is True
        assert config.sensors.rain_gauge_enabled is True
        assert config.sensors.mq135_enabled is False

    def test_default_recording(self):
        config = Config.default()
        assert config.recording.sample_interval_seconds == 60
        assert config.recording.retention_days == 365

    def test_default_alerts(self):
        config = Config.default()
        assert config.alerts.high_temp_c == 35.0
        assert config.alerts.low_temp_c == -10.0
        assert config.alerts.high_pm25_ugm3 == 35.0

    def test_config_is_frozen(self):
        """Config should be immutable (frozen dataclass)."""
        config = Config.default()
        try:
            config.station_name = "modified"
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass  # expected


class TestConfigFromYaml:
    """Test loading config from YAML files."""

    def test_missing_file_returns_defaults(self):
        config = Config.from_yaml("/nonexistent/path.yaml")
        assert config.station_name == "weather-station-01"

    def test_partial_override(self, tmp_path):
        """Partial YAML should override only specified values."""
        yaml_content = """
station_name: "my-station"
station_id: "custom01"
mock_mode: true
"""
        path = tmp_path / "config.yaml"
        path.write_text(yaml_content)
        config = Config.from_yaml(path)
        assert config.station_name == "my-station"
        assert config.station_id == "custom01"
        assert config.mock_mode is True
        # Unspecified values keep defaults
        assert config.sensors.bme680_enabled is True

    def test_nested_sensor_config(self, tmp_path):
        """Sensor sub-config should be parsed from nested YAML."""
        yaml_content = """
sensors:
  bme680_enabled: false
  bme280_enabled: true
  bme280_i2c_address: 0x76
"""
        path = tmp_path / "config.yaml"
        path.write_text(yaml_content)
        config = Config.from_yaml(path)
        assert config.sensors.bme680_enabled is False
        assert config.sensors.bme280_enabled is True
        assert config.sensors.bme280_i2c_address == 0x76

    def test_unknown_keys_ignored(self, tmp_path):
        """Unknown keys should not cause errors."""
        yaml_content = """
station_name: "test"
unknown_key: "ignored"
sensors:
  bme680_enabled: false
  unknown_sensor_key: "ignored"
"""
        path = tmp_path / "config.yaml"
        path.write_text(yaml_content)
        config = Config.from_yaml(path)
        assert config.station_name == "test"

    def test_to_dict_roundtrip(self):
        """to_dict should produce a serializable dict."""
        config = Config.default()
        d = config.to_dict()
        assert isinstance(d, dict)
        assert "station_name" in d
        assert "sensors" in d
        assert isinstance(d["sensors"], dict)