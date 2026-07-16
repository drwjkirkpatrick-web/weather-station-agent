"""
Data forwarder: periodically push weather readings to online services.

WHY a dedicated forwarder:
  Like the DataRecorder, the forwarder runs in its own background thread
  at a configurable interval (default: 5 minutes).  It reads the latest
  readings from the database, normalizes them into a common dict format,
  and pushes to each enabled forwarding service.

  This decouples forwarding from recording — if a remote service is
  down or slow, the local station keeps recording without interruption.

THREAD SAFETY:
  The forwarder only reads from the database (never writes), and the
  SQLite WAL mode allows concurrent readers, so there's no lock
  contention with the recorder thread.

NORMALIZATION:
  The database stores readings in a key-value format (sensor_name,
  metric, value).  The forwarder flattens the latest reading for each
  metric into a single dict with standard keys like "temperature_c",
  "humidity_pct", "pressure_hpa", etc.  Each service adapter then
  converts from this normalized format to its wire format.

METRIC MAP:
  The forwarder maps (sensor_name, metric) pairs from the database to
  normalized keys.  For example:
    ("bme680", "temperature_c") → "temperature_c"
    ("bme680", "humidity_pct")  → "humidity_pct"
    ("anemometer", "wind_speed_mps") → "wind_speed_mps"
    ("wind_vane", "wind_dir_deg") → "wind_dir_deg"
    ("rain_gauge", "rain_mm") → "rain_mm"
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from weather_station.core.config import Config, ForwardingConfig
from weather_station.core.database import WeatherDatabase
from weather_station.forwarding.service_base import ForwardingResult, ForwardingServiceBase
from weather_station.forwarding.services import (
    CWOPService,
    OpenWeatherMapService,
    WeathercloudService,
    WundergroundService,
)

logger = logging.getLogger(__name__)

# ── Metric mapping: DB metric name → normalized key ───────────────────
# When the database has multiple sensors for the same metric (e.g.
# BME680 and BME280 both produce temperature_c), the first one found
# (in sensor order) wins.  This is fine — the station typically has
# only one primary sensor for each metric.
#
# If a sensor produces a metric not listed here, the metric name from
# the DB is used directly as the normalized key.  This lets new sensors
# work without updating this map, as long as their metric names follow
# the convention.

METRIC_MAP: dict[str, str] = {
    # Temperature
    "temperature_c": "temperature_c",
    # Humidity
    "humidity_pct": "humidity_pct",
    # Pressure
    "pressure_hpa": "pressure_hpa",
    # Wind — anemometer
    "wind_speed_mps": "wind_speed_mps",
    # Wind — vane (sensor uses "wind_direction_deg", we normalize it)
    "wind_direction_deg": "wind_dir_deg",
    # Rain
    "rain_mm": "rain_mm",
    "rain_rate_mmh": "rain_rate_mmh",
    # Light / UV
    "uv_index": "uv_index",
    "light_lux": "light_lux",
    # Air quality — PMS5003
    "pm1_0_ugm3": "pm1_0_ugm3",
    "pm2_5_ugm3": "pm25_ugm3",
    "pm10_ugm3": "pm10_ugm3",
    # Air quality — MQ135
    "co2_ppm": "co2_eq_ppm",
    # Air quality — SGP30
    "co2_eq_ppm": "co2_eq_ppm",
    "tvoc_ppb": "tvoc_ppb",
}


class DataForwarder:
    """Periodically forward weather data to online services.

    Runs in a background thread.  Call ``start()`` to begin forwarding
    and ``stop()`` to shut down cleanly.  Only active when
    ``config.forwarding.enabled`` is True and at least one service is
    configured.
    """

    def __init__(
        self,
        db: WeatherDatabase,
        config: Config,
        mock_mode: bool = False,
    ) -> None:
        self.db = db
        self.config = config
        self.forwarding_config: ForwardingConfig = config.forwarding
        self.mock_mode = mock_mode

        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Build service adapters (only active ones are used)
        self._services: list[ForwardingServiceBase] = self._build_services()

        # Statistics
        self._cycle_count: int = 0
        self._total_forwarded: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0

    def _build_services(self) -> list[ForwardingServiceBase]:
        """Build all service adapters, regardless of enabled state.

        Each adapter's ``is_enabled()`` method decides whether it's
        active.  This way the health_check / CLI shows all configured
        services even if they're temporarily disabled.
        """
        fc = self.forwarding_config
        return [
            WundergroundService(fc, mock_mode=self.mock_mode),
            CWOPService(fc, mock_mode=self.mock_mode),
            WeathercloudService(fc, mock_mode=self.mock_mode),
            OpenWeatherMapService(fc, mock_mode=self.mock_mode),
        ]

    @property
    def active_services(self) -> list[ForwardingServiceBase]:
        """Return only services that are enabled and ready."""
        return [s for s in self._services if s.is_enabled()]

    @property
    def is_active(self) -> bool:
        """True if forwarding is enabled and at least one service is configured."""
        return self.forwarding_config.enabled and len(self.active_services) > 0

    def start(self) -> None:
        """Start the forwarding thread (if any services are active)."""
        if self._running:
            logger.warning("Forwarder already running")
            return
        if not self.forwarding_config.enabled:
            logger.info("Forwarding disabled in config — not starting")
            return
        if not self.active_services:
            logger.warning("Forwarding enabled but no services configured — not starting")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="data-forwarder",
        )
        self._thread.start()
        active = ", ".join(s.name for s in self.active_services)
        logger.info(
            "Data forwarder started (interval=%ds, services=[%s])",
            self.forwarding_config.forward_interval_seconds,
            active,
        )

    def stop(self) -> None:
        """Signal the forwarding thread to stop and wait for it."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Data forwarder stopped")

    def _run(self) -> None:
        """Main forwarding loop."""
        while not self._stop_event.is_set():
            self._cycle_count += 1
            try:
                self._forward_cycle()
            except Exception as e:
                logger.error("Forwarding cycle %d failed: %s", self._cycle_count, e)
                self._total_failures += 1
            # Wait for the interval, but wake early if stopped
            self._stop_event.wait(self.forwarding_config.forward_interval_seconds)

    def _forward_cycle(self) -> None:
        """Read latest data from DB and push to each enabled service."""
        readings = self._normalize_latest_readings()
        if not readings:
            logger.debug("No readings to forward (cycle %d)", self._cycle_count)
            return

        results: list[ForwardingResult] = []
        for service in self._services:
            if not service.is_enabled():
                continue
            result = service.forward(readings)
            results.append(result)
            self._total_forwarded += 1
            if result.success:
                self._total_successes += 1
            else:
                self._total_failures += 1
            logger.info(
                "[forwarder] %s: %s — %s",
                result.service,
                "✓" if result.success else "✗",
                result.message,
            )

    def _normalize_latest_readings(self) -> dict[str, Any]:
        """Read the latest readings from the DB and normalize them.

        Returns a flat dict with normalized metric keys.  Also includes
        the station's latitude, longitude, and elevation (needed by
        CWOP for position data).
        """
        latest = self.db.get_latest_readings(station_id=self.config.station_id)
        if not latest:
            return {}

        normalized: dict[str, Any] = {}

        # Add station position (from config, not from readings)
        normalized["latitude"] = self.config.latitude
        normalized["longitude"] = self.config.longitude
        normalized["elevation_m"] = self.config.elevation_m

        # Use the most recent timestamp across all readings
        timestamps = [r.get("timestamp", "") for r in latest if r.get("timestamp")]
        if timestamps:
            normalized["timestamp"] = max(timestamps)

        for row in latest:
            metric = row.get("metric", "")
            value = row.get("value")
            if value is None:
                continue
            # Map to normalized key, or use the metric name directly
            normalized_key = METRIC_MAP.get(metric, metric)
            if not normalized_key:
                continue
            normalized[normalized_key] = float(value)

        return normalized

    def forward_once(self) -> list[ForwardingResult]:
        """Do a single forward cycle (for CLI use).

        Returns the results for each enabled service.  Does not start
        the background thread — useful for testing or manual triggers.
        """
        readings = self._normalize_latest_readings()
        if not readings:
            return []

        results: list[ForwardingResult] = []
        for service in self._services:
            if not service.is_enabled():
                continue
            result = service.forward(readings)
            results.append(result)
        return results

    def health_check(self) -> dict[str, Any]:
        """Return forwarder health and per-service status."""
        return {
            "running": self._running,
            "enabled": self.forwarding_config.enabled,
            "cycle_count": self._cycle_count,
            "total_forwarded": self._total_forwarded,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "forward_interval_s": self.forwarding_config.forward_interval_seconds,
            "active_services": [s.name for s in self.active_services],
            "services": [s.health_check() for s in self._services],
        }
