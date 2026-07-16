"""
Tests for the forwarding service adapters and DataForwarder.

WHY mock_mode testing:
  All service adapters support mock_mode=True, which logs the payload
  instead of making network calls.  This lets us test the full pipeline
  (format_payload → send) without internet access, matching the
  project's existing mock-mode testing philosophy.

  Each test verifies:
  1. is_enabled() correctly reflects config state
  2. format_payload() produces correctly formatted output
  3. send() in mock mode returns success
  4. Unit conversions are correct
  5. The DataForwarder normalizes DB readings and calls services
"""

from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest

from weather_station.core.config import Config, ForwardingConfig
from weather_station.forwarding.forwarder import DataForwarder
from weather_station.forwarding.services.cwop import (
    CWOPService,
    _decimal_to_aprs_lat,
    _decimal_to_aprs_lon,
)
from weather_station.forwarding.services.openweathermap import OpenWeatherMapService
from weather_station.forwarding.services.weathercloud import WeathercloudService
from weather_station.forwarding.services.wunderground import (
    WundergroundService,
    _c_to_f,
    _hpa_to_inhg,
    _mm_to_inch,
    _ms_to_mph,
)

# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_readings():
    """Normalized readings dict as the forwarder would produce."""
    return {
        "timestamp": "2024-01-15T10:32:35+00:00",
        "temperature_c": 22.5,
        "humidity_pct": 65.0,
        "pressure_hpa": 1013.25,
        "wind_dir_deg": 230.0,
        "wind_speed_mps": 5.2,
        "wind_gust_mps": 7.8,
        "rain_mm": 1.2,
        "rain_daily_mm": 3.4,
        "dew_point_c": 15.8,
        "uv_index": 3.2,
        "solar_radiation_wm2": 450.0,
    }


@pytest.fixture
def wu_config():
    """ForwardingConfig with Wunderground enabled."""
    return ForwardingConfig(
        enabled=True,
        wunderground_enabled=True,
        wunderground_station_id="KTEST01",
        wunderground_password="secret123",
    )


@pytest.fixture
def cwop_config():
    """ForwardingConfig with CWOP enabled."""
    return ForwardingConfig(
        enabled=True,
        cwop_enabled=True,
        cwop_station_id="EW9876",
    )


@pytest.fixture
def wc_config():
    """ForwardingConfig with WeatherCloud enabled."""
    return ForwardingConfig(
        enabled=True,
        weathercloud_enabled=True,
        weathercloud_device_id="123456789012",
        weathercloud_device_key="abcdef0123456789abcdef0123456789",
    )


@pytest.fixture
def owm_config():
    """ForwardingConfig with OpenWeatherMap enabled."""
    return ForwardingConfig(
        enabled=True,
        openweathermap_enabled=True,
        openweathermap_api_key="test_api_key_123",
        openweathermap_station_id="583436dd9643a9000196b8d6",
    )


# ── Unit conversion tests ─────────────────────────────────────────────

class TestUnitConversions:
    """Test the unit conversion helpers used by the adapters."""

    def test_c_to_f(self):
        assert _c_to_f(0) == 32.0
        assert _c_to_f(100) == 212.0
        assert _c_to_f(22.5) == pytest.approx(72.5)

    def test_ms_to_mph(self):
        assert _ms_to_mph(0) == 0.0
        assert _ms_to_mph(1) == pytest.approx(2.237, abs=0.01)

    def test_hpa_to_inhg(self):
        assert _hpa_to_inhg(1013.25) == pytest.approx(29.92, abs=0.01)

    def test_mm_to_inch(self):
        assert _mm_to_inch(25.4) == pytest.approx(1.0, abs=0.01)

    def test_decimal_to_aprs_lat(self):
        result = _decimal_to_aprs_lat(33.2673)
        assert result == "3316.04N"

    def test_decimal_to_aprs_lat_negative(self):
        result = _decimal_to_aprs_lat(-33.2673)
        assert result.endswith("S")

    def test_decimal_to_aprs_lon(self):
        result = _decimal_to_aprs_lon(-96.5327)
        assert result == "09631.96W"

    def test_decimal_to_aprs_lon_positive(self):
        result = _decimal_to_aprs_lon(96.5327)
        assert result.endswith("E")


# ── Wunderground tests ─────────────────────────────────────────────────

class TestWundergroundService:
    """Test the Weather Underground PWS adapter."""

    def test_disabled_by_default(self):
        """Service should be disabled with default config."""
        svc = WundergroundService(ForwardingConfig())
        assert not svc.is_enabled()

    def test_enabled_with_config(self, wu_config):
        """Service should be enabled when ID + password are set."""
        svc = WundergroundService(wu_config)
        assert svc.is_enabled()

    def test_enabled_without_password(self):
        """Service should be disabled without password."""
        svc = WundergroundService(ForwardingConfig(
            wunderground_enabled=True,
            wunderground_station_id="KTEST",
        ))
        assert not svc.is_enabled()

    def test_format_payload_contains_required_params(self, wu_config, sample_readings):
        """Payload URL must contain ID, PASSWORD, action, dateutc."""
        svc = WundergroundService(wu_config, mock_mode=True)
        url = svc.format_payload(sample_readings)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert params["ID"][0] == "KTEST01"
        assert params["PASSWORD"][0] == "secret123"
        assert params["action"][0] == "updateraw"
        assert "dateutc" in params

    def test_format_payload_imperial_units(self, wu_config, sample_readings):
        """Payload should contain imperial unit conversions."""
        svc = WundergroundService(wu_config, mock_mode=True)
        url = svc.format_payload(sample_readings)
        params = parse_qs(urlparse(url).query)

        # 22.5°C → 72.5°F
        assert float(params["tempf"][0]) == pytest.approx(72.5, abs=0.1)
        # 5.2 m/s → ~11.6 mph
        assert float(params["windspeedmph"][0]) == pytest.approx(11.6, abs=0.1)
        # 1013.25 hPa → ~29.92 inHg
        assert float(params["baromin"][0]) == pytest.approx(29.92, abs=0.01)
        # 1.2 mm → ~0.047 inches
        assert float(params["rainin"][0]) == pytest.approx(0.047, abs=0.01)

    def test_format_payload_partial_data(self, wu_config):
        """Should handle partial readings (only some metrics present)."""
        svc = WundergroundService(wu_config, mock_mode=True)
        partial = {"timestamp": "2024-01-15T10:00:00+00:00", "temperature_c": 20.0}
        url = svc.format_payload(partial)
        params = parse_qs(urlparse(url).query)

        assert "tempf" in params
        assert "windspeedmph" not in params

    def test_mock_send_returns_success(self, wu_config, sample_readings):
        """Mock mode send should log and return success."""
        svc = WundergroundService(wu_config, mock_mode=True)
        payload = svc.format_payload(sample_readings)
        result = svc.send(payload)

        assert result.success
        assert "Mock" in result.message

    def test_forward_full_cycle(self, wu_config, sample_readings):
        """Forward() should format + send + track stats."""
        svc = WundergroundService(wu_config, mock_mode=True)
        result = svc.forward(sample_readings)

        assert result.success
        assert svc._total_sent == 1
        assert svc._total_success == 1

    def test_rapidfire_uses_rtupdate(self, wu_config, sample_readings):
        """Rapidfire mode should use the rtupdate server."""
        rapid_config = replace(wu_config, wunderground_rapidfire=True)
        svc = WundergroundService(rapid_config, mock_mode=True)
        url = svc.format_payload(sample_readings)

        assert "rtupdate.wunderground.com" in url

    def test_dateutc_now_fallback(self, wu_config):
        """Missing timestamp should fall back to 'now'."""
        svc = WundergroundService(wu_config, mock_mode=True)
        url = svc.format_payload({})
        params = parse_qs(urlparse(url).query)

        assert params["dateutc"][0] == "now"


# ── CWOP tests ─────────────────────────────────────────────────────────

class TestCWOPService:
    """Test the CWOP APRS adapter."""

    def test_disabled_by_default(self):
        svc = CWOPService(ForwardingConfig())
        assert not svc.is_enabled()

    def test_enabled_with_config(self, cwop_config):
        svc = CWOPService(cwop_config)
        assert svc.is_enabled()

    def test_format_packet_contains_station_id(self, cwop_config, sample_readings):
        """APRS packet should start with the station callsign."""
        svc = CWOPService(cwop_config, mock_mode=True)
        packet = svc.format_payload({**sample_readings, "latitude": 33.2673, "longitude": -96.5327})

        assert packet.startswith("EW9876>APRS,TCPIP*:")

    def test_format_packet_contains_position(self, cwop_config, sample_readings):
        """Packet should contain APRS-format lat/lon."""
        svc = CWOPService(cwop_config, mock_mode=True)
        packet = svc.format_payload({**sample_readings, "latitude": 33.2673, "longitude": -96.5327})

        assert "3316.04N" in packet
        assert "09631.96W" in packet

    def test_format_packet_contains_weather_data(self, cwop_config, sample_readings):
        """Packet should contain wind, temp, pressure, humidity data."""
        svc = CWOPService(cwop_config, mock_mode=True)
        packet = svc.format_payload({**sample_readings, "latitude": 33.2673, "longitude": -96.5327})

        # Wind direction (230°)
        assert "_230/" in packet
        # Wind speed (~12 mph from 5.2 m/s)
        assert "/012" in packet
        # Temperature (22.5°C → 72.5°F → rounds to 72 or 73)
        assert "t72" in packet or "t73" in packet
        # Humidity (65%)
        assert "h65" in packet
        # Pressure (1013.25 hPa → 10132 or 10133 tenths-mb)
        assert "b1013" in packet

    def test_format_packet_missing_values(self, cwop_config):
        """Missing values should use '...' per APRS spec."""
        svc = CWOPService(cwop_config, mock_mode=True)
        packet = svc.format_payload({"latitude": 33.2673, "longitude": -96.5327})

        # No wind data → ...
        assert "_.../..." in packet
        # No temp → t...
        assert "t..." in packet

    def test_mock_send_returns_success(self, cwop_config, sample_readings):
        """Mock mode send should log and return success."""
        svc = CWOPService(cwop_config, mock_mode=True)
        packet = svc.format_payload({**sample_readings, "latitude": 33.2673, "longitude": -96.5327})
        result = svc.send(packet)

        assert result.success
        assert "Mock" in result.message

    def test_forward_full_cycle(self, cwop_config, sample_readings):
        """Forward() should format + send + track stats."""
        svc = CWOPService(cwop_config, mock_mode=True)
        readings = {**sample_readings, "latitude": 33.2673, "longitude": -96.5327}
        result = svc.forward(readings)

        assert result.success
        assert svc._total_sent == 1


# ── WeatherCloud tests ─────────────────────────────────────────────────

class TestWeathercloudService:
    """Test the WeatherCloud adapter."""

    def test_disabled_by_default(self):
        svc = WeathercloudService(ForwardingConfig())
        assert not svc.is_enabled()

    def test_enabled_with_config(self, wc_config):
        svc = WeathercloudService(wc_config)
        assert svc.is_enabled()

    def test_format_payload_contains_auth(self, wc_config, sample_readings):
        """Payload should contain deviceid and devicekey."""
        svc = WeathercloudService(wc_config, mock_mode=True)
        url = svc.format_payload(sample_readings)
        params = parse_qs(urlparse(url).query)

        assert params["deviceid"][0] == "123456789012"
        assert params["devicekey"][0] == "abcdef0123456789abcdef0123456789"

    def test_format_payload_scaled_values(self, wc_config, sample_readings):
        """Metric values should be scaled by 10 (WeatherCloud convention)."""
        svc = WeathercloudService(wc_config, mock_mode=True)
        url = svc.format_payload(sample_readings)
        params = parse_qs(urlparse(url).query)

        # 22.5°C → 225 (×10)
        assert params["temp"][0] == "225"
        # 1013.25 hPa → 10133 (×10, may round to 10132 due to float representation)
        assert params["bar"][0] in ("10132", "10133")
        # 5.2 m/s → 52 (×10)
        assert params["wspd"][0] == "52"
        # 65% humidity → 65 (no scaling)
        assert params["hum"][0] == "65"

    def test_mock_send_returns_success(self, wc_config, sample_readings):
        svc = WeathercloudService(wc_config, mock_mode=True)
        url = svc.format_payload(sample_readings)
        result = svc.send(url)

        assert result.success
        assert "Mock" in result.message

    def test_forward_full_cycle(self, wc_config, sample_readings):
        svc = WeathercloudService(wc_config, mock_mode=True)
        result = svc.forward(sample_readings)

        assert result.success
        assert svc._total_sent == 1


# ── OpenWeatherMap tests ──────────────────────────────────────────────

class TestOpenWeatherMapService:
    """Test the OpenWeatherMap Station API 3.0 adapter."""

    def test_disabled_by_default(self):
        svc = OpenWeatherMapService(ForwardingConfig())
        assert not svc.is_enabled()

    def test_enabled_with_config(self, owm_config):
        svc = OpenWeatherMapService(owm_config)
        assert svc.is_enabled()

    def test_format_payload_contains_station_id(self, owm_config, sample_readings):
        """Payload should contain the OWM internal station_id."""
        svc = OpenWeatherMapService(owm_config, mock_mode=True)
        payload = svc.format_payload(sample_readings)

        assert payload["station_id"] == "583436dd9643a9000196b8d6"

    def test_format_payload_metric_units(self, owm_config, sample_readings):
        """Payload should use metric units (no scaling)."""
        svc = OpenWeatherMapService(owm_config, mock_mode=True)
        payload = svc.format_payload(sample_readings)

        # Temperature in °C (direct)
        assert payload["temperature"] == pytest.approx(22.5, abs=0.1)
        # Wind speed in m/s (direct)
        assert payload["wind_speed"] == pytest.approx(5.2, abs=0.1)
        # Pressure in hPa (direct)
        assert payload["pressure"] == pytest.approx(1013.25, abs=0.1)
        # Humidity in %
        assert payload["humidity"] == 65

    def test_format_payload_unix_timestamp(self, owm_config, sample_readings):
        """Payload should contain a Unix timestamp."""
        svc = OpenWeatherMapService(owm_config, mock_mode=True)
        payload = svc.format_payload(sample_readings)

        assert "dt" in payload
        assert isinstance(payload["dt"], int)
        assert payload["dt"] > 0

    def test_mock_send_returns_success(self, owm_config, sample_readings):
        svc = OpenWeatherMapService(owm_config, mock_mode=True)
        payload = svc.format_payload(sample_readings)
        result = svc.send(payload)

        assert result.success
        assert "Mock" in result.message

    def test_forward_full_cycle(self, owm_config, sample_readings):
        svc = OpenWeatherMapService(owm_config, mock_mode=True)
        result = svc.forward(sample_readings)

        assert result.success
        assert svc._total_sent == 1


# ── DataForwarder tests ───────────────────────────────────────────────

class TestDataForwarder:
    """Test the DataForwarder orchestrator."""

    def test_not_active_by_default(self, db):
        """Forwarder should be inactive with default config."""
        config = Config.default()
        fwd = DataForwarder(db=db, config=config)
        assert not fwd.is_active

    def test_active_when_enabled(self, db, wu_config):
        """Forwarder should be active when enabled + service configured."""
        config = Config(
            station_id="test01",
            forwarding=wu_config,
        )
        fwd = DataForwarder(db=db, config=config, mock_mode=True)
        assert fwd.is_active
        assert len(fwd.active_services) == 1

    def test_not_active_no_services(self, db):
        """Enabled but no services configured → not active."""
        config = Config(
            forwarding=ForwardingConfig(enabled=True),
        )
        fwd = DataForwarder(db=db, config=config)
        assert not fwd.is_active

    def test_normalize_readings(self, db):
        """Should normalize DB readings into a flat dict."""
        config = Config(
            station_id="test01",
            latitude=44.0,
            longitude=-123.0,
            elevation_m=150.0,
        )
        fwd = DataForwarder(db=db, config=config, mock_mode=True)

        # Insert some readings
        db.insert_reading(
            timestamp="2024-01-15T10:00:00+00:00",
            station_id="test01",
            sensor_name="bme680",
            metric="temperature_c",
            value=22.5,
        )
        db.insert_reading(
            timestamp="2024-01-15T10:00:00+00:00",
            station_id="test01",
            sensor_name="bme680",
            metric="humidity_pct",
            value=65.0,
        )
        db.insert_reading(
            timestamp="2024-01-15T10:00:00+00:00",
            station_id="test01",
            sensor_name="wind_vane",
            metric="wind_direction_deg",
            value=230.0,
        )

        normalized = fwd._normalize_latest_readings()

        assert normalized["temperature_c"] == 22.5
        assert normalized["humidity_pct"] == 65.0
        # wind_direction_deg should be normalized to wind_dir_deg
        assert normalized["wind_dir_deg"] == 230.0
        # Station position from config
        assert normalized["latitude"] == 44.0
        assert normalized["longitude"] == -123.0

    def test_normalize_empty_db(self, db):
        """Empty database should produce empty normalized dict."""
        config = Config(station_id="test01")
        fwd = DataForwarder(db=db, config=config, mock_mode=True)
        assert fwd._normalize_latest_readings() == {}

    def test_forward_once_with_mock(self, db, wu_config):
        """forward_once should push to enabled services in mock mode."""
        config = Config(
            station_id="test01",
            latitude=44.0,
            longitude=-123.0,
            forwarding=wu_config,
        )
        fwd = DataForwarder(db=db, config=config, mock_mode=True)

        # Insert a reading
        db.insert_reading(
            timestamp="2024-01-15T10:00:00+00:00",
            station_id="test01",
            sensor_name="bme680",
            metric="temperature_c",
            value=22.5,
        )

        results = fwd.forward_once()
        assert len(results) == 1
        assert results[0].success
        assert results[0].service == "wunderground"

    def test_forward_once_no_data(self, db, wu_config):
        """forward_once with no data should return empty list."""
        config = Config(
            station_id="test01",
            forwarding=wu_config,
        )
        fwd = DataForwarder(db=db, config=config, mock_mode=True)
        results = fwd.forward_once()
        assert results == []

    def test_health_check(self, db, wu_config):
        """Health check should return service status."""
        config = Config(
            station_id="test01",
            forwarding=wu_config,
        )
        fwd = DataForwarder(db=db, config=config, mock_mode=True)
        hc = fwd.health_check()

        assert hc["enabled"] is True
        assert hc["running"] is False
        assert "wunderground" in hc["active_services"]
        assert len(hc["services"]) == 4  # all four adapters

    def test_start_stop_not_enabled(self, db):
        """Start should be a no-op when forwarding is disabled."""
        config = Config(station_id="test01")
        fwd = DataForwarder(db=db, config=config)
        fwd.start()
        assert not fwd._running
        fwd.stop()  # should not error

    def test_multiple_services(self, db):
        """Multiple enabled services should all receive data."""
        config = Config(
            station_id="test01",
            latitude=44.0,
            longitude=-123.0,
            forwarding=ForwardingConfig(
                enabled=True,
                wunderground_enabled=True,
                wunderground_station_id="KTEST",
                wunderground_password="secret",
                weathercloud_enabled=True,
                weathercloud_device_id="123456789012",
                weathercloud_device_key="abcdef0123456789abcdef0123456789",
            ),
        )
        fwd = DataForwarder(db=db, config=config, mock_mode=True)

        db.insert_reading(
            timestamp="2024-01-15T10:00:00+00:00",
            station_id="test01",
            sensor_name="bme680",
            metric="temperature_c",
            value=20.0,
        )

        results = fwd.forward_once()
        assert len(results) == 2
        services = {r.service for r in results}
        assert "wunderground" in services
        assert "weathercloud" in services
        assert all(r.success for r in results)


# ── Config tests ───────────────────────────────────────────────────────

class TestForwardingConfig:
    """Test ForwardingConfig parsing and defaults."""

    def test_defaults_all_disabled(self):
        """Default config should have all services disabled."""
        fc = ForwardingConfig()
        assert not fc.enabled
        assert not fc.wunderground_enabled
        assert not fc.cwop_enabled
        assert not fc.weathercloud_enabled
        assert not fc.openweathermap_enabled

    def test_from_yaml_parses_forwarding(self, tmp_path):
        """Config.from_yaml should parse the forwarding section."""
        yaml_content = """
station_id: "ws01"
forwarding:
  enabled: true
  forward_interval_seconds: 600
  wunderground_enabled: true
  wunderground_station_id: "KTEST"
  wunderground_password: "secret"
"""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(yaml_content)

        config = Config.from_yaml(config_path)
        assert config.forwarding.enabled is True
        assert config.forwarding.forward_interval_seconds == 600
        assert config.forwarding.wunderground_enabled is True
        assert config.forwarding.wunderground_station_id == "KTEST"

    def test_from_yaml_ignores_unknown_keys(self, tmp_path):
        """Unknown forwarding keys should be silently ignored."""
        yaml_content = """
forwarding:
  enabled: true
  unknown_key: "should be ignored"
"""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(yaml_content)

        config = Config.from_yaml(config_path)
        assert config.forwarding.enabled is True
