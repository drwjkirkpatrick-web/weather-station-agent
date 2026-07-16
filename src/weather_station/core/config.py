"""
Central configuration for the weather station agent.

WHY dataclasses:
  Using a frozen dataclass gives us typed, immutable config that can be
  passed around without fear of accidental mutation.  Defaults are tuned
  for the Raspberry Pi Zero 2 W (512 MB RAM, single-channel I2C, limited
  GPIO).

NOTE: All hardware addresses and GPIO pins are configurable so the user
can re-wire without touching code.  A YAML config file can override any
default via ``Config.from_yaml()``.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ── Per-sensor enable flags ─────────────────────────────────────────────
# If a sensor is not physically connected, set its flag to False.  The
# orchestrator will skip it entirely — no errors, no mock data.

@dataclass(frozen=True)
class SensorConfig:
    """Enable/disable and per-sensor parameters."""

    # I2C sensors (all share bus 1 on the Pi Zero 2 W)
    bme680_enabled: bool = True
    bme680_i2c_address: int = 0x77          # default Bosch address
    bme680_sea_level_pressure: float = 1013.25  # hPa for altitude calc

    bme280_enabled: bool = False             # secondary temp/pressure
    bme280_i2c_address: int = 0x76          # alternate address to avoid clash

    sht31_enabled: bool = False             # high-accuracy temp/humidity
    sht31_i2c_address: int = 0x44

    sgp30_enabled: bool = True               # air quality (CO2-eq, TVOC)
    sgp30_i2c_address: int = 0x58

    veml7700_enabled: bool = True            # ambient light / UV
    veml7700_i2c_address: int = 0x10

    ds3231_enabled: bool = True              # RTC for accurate timestamps
    ds3231_i2c_address: int = 0x68

    # Serial sensor
    pms5003_enabled: bool = True
    pms5003_serial_port: str = "/dev/serial0"  # Pi Zero 2 W primary UART
    pms5003_baudrate: int = 9600

    # GPIO-based sensors
    anemometer_enabled: bool = True
    anemometer_pin: int = 4                  # GPIO4 (pin 7)
    anemometer_radius_cm: float = 6.0        # cup radius for speed calc
    anemometer_calibration_factor: float = 1.0

    wind_vane_enabled: bool = True
    wind_vane_adc_channel: int = 0           # MCP3008 channel 0
    wind_vane_vin: float = 3.3               # supply voltage

    rain_gauge_enabled: bool = True
    rain_gauge_pin: int = 17                  # GPIO17 (pin 11)
    rain_gauge_bucket_ml: float = 0.2794     # ml per tip (0.011 inches)

    # Analog sensor via ADC
    mq135_enabled: bool = False
    mq135_adc_channel: int = 1               # MCP3008 channel 1
    mq135_vin: float = 3.3


# ── Recording config ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecordingConfig:
    """Data storage and sampling parameters."""

    db_path: str = "data/weather.db"
    sample_interval_seconds: int = 60        # poll sensors every 60 s
    retention_days: int = 365                # auto-delete readings older
    batch_size: int = 50                     # batch DB inserts


# ── Alert config ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AlertConfig:
    """Weather alert thresholds."""

    high_temp_c: float = 35.0
    low_temp_c: float = -10.0
    high_humidity_pct: float = 95.0
    low_humidity_pct: float = 10.0
    high_pressure_hpa: float = 1040.0
    low_pressure_hpa: float = 990.0
    high_pm25_ugm3: float = 35.0
    high_tvoc_ppb: float = 1000.0
    high_wind_mps: float = 15.0
    heavy_rain_mmh: float = 25.0


# ── Web dashboard config ───────────────────────────────────────────────

@dataclass(frozen=True)
class WebConfig:
    """Flask dashboard settings."""

    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False


# ── Data forwarding config ──────────────────────────────────────────────
# Optional: push weather data to online forecasting / citizen-science
# networks.  Each service is independently enabled.  All are off by
# default so the station runs fine without any of them.

@dataclass(frozen=True)
class ForwardingConfig:
    """Settings for forwarding weather data to online services.

    Every service is opt-in.  The forwarder runs in its own background
    thread (like the recorder) and pushes the latest readings at the
    configured interval.  No external Python dependencies — uses only
    stdlib ``urllib`` (HTTP) and ``socket`` (TCP for CWOP).
    """

    # ── General ──────────────────────────────────────────────────────
    enabled: bool = False                   # master switch
    forward_interval_seconds: int = 300     # push every 5 min (CWOP min is 5)
    timeout_seconds: int = 30               # HTTP / TCP connect+read timeout

    # ── Weather Underground PWS ─────────────────────────────────────
    # https://support.weather.com/s/article/PWS-Upload-Protocol
    # Sign up at wunderground.com, register a PWS, get ID + Station Key.
    wunderground_enabled: bool = False
    wunderground_station_id: str = ""       # e.g. "KCASANFR5"
    wunderground_password: str = ""         # Station Key (case-sensitive)
    wunderground_rapidfire: bool = False    # True → use rtupdate server

    # ── CWOP (Citizen Weather Observer Program / NOAA) ───────────────
    # http://wxqa.com/
    # Register at wxqa.com to get a CW/DW/EW/FW/GW ID.
    # Data flows to MADIS → NWS forecast models.
    cwop_enabled: bool = False
    cwop_station_id: str = ""               # e.g. "EW9876"
    cwop_server: str = "cwop.aprs.net"
    cwop_port: int = 14580                   # fallback: port 23

    # ── WeatherCloud ────────────────────────────────────────────────
    # https://weathercloud.net/
    # Create a device, get a Device ID and Key from the dashboard.
    weathercloud_enabled: bool = False
    weathercloud_device_id: str = ""         # 12-digit numeric ID
    weathercloud_device_key: str = ""        # 32-char hex key

    # ── OpenWeatherMap Station API 3.0 ──────────────────────────────
    # https://openweathermap.org/stations
    # Register a station via the API (POST /stations) to get the
    # internal station_id, then enable forwarding here.
    openweathermap_enabled: bool = False
    openweathermap_api_key: str = ""
    openweathermap_station_id: str = ""      # internal ID from OWM (24 hex)


# ── Top-level config ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Config:
    """Master configuration object for the weather station."""

    # Unique station identity
    station_name: str = "weather-station-01"
    station_id: str = "ws01"
    latitude: float = 44.0                    # default: Oregon
    longitude: float = -123.0
    elevation_m: float = 150.0

    # Operating mode
    mock_mode: bool = False                   # True = simulate all sensors
    verbose: bool = False

    # Sub-configs
    sensors: SensorConfig = field(default_factory=SensorConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    web: WebConfig = field(default_factory=WebConfig)
    forwarding: ForwardingConfig = field(default_factory=ForwardingConfig)

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to a plain dict (for logging / dashboard)."""
        return asdict(self)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        """Load configuration from a YAML file, falling back to defaults.

        Any key not present in the YAML file retains its default value.
        Nested dicts map to nested dataclasses (sensors, recording, etc.).

        NOTE: This is intentionally tolerant — unknown keys are ignored
        rather than raising, so partial config files work fine.
        """
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        # Extract sub-config dicts, defaulting to empty if absent
        sensor_data = raw.pop("sensors", {})
        recording_data = raw.pop("recording", {})
        alert_data = raw.pop("alerts", {})
        web_data = raw.pop("web", {})
        forwarding_data = raw.pop("forwarding", {})

        # Build sub-configs, ignoring unknown keys
        def _build(cls_: type, data: dict) -> object:
            known = {f.name for f in cls_.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls_(**filtered)

        return cls(
            **{k: v for k, v in raw.items() if k in
               {f.name for f in cls.__dataclass_fields__.values()}},
            sensors=_build(SensorConfig, sensor_data),
            recording=_build(RecordingConfig, recording_data),
            alerts=_build(AlertConfig, alert_data),
            web=_build(WebConfig, web_data),
            forwarding=_build(ForwardingConfig, forwarding_data),
        )

    @classmethod
    def default(cls) -> Config:
        """Return the default configuration (all sensors enabled, mock off)."""
        return cls()