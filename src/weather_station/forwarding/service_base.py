"""
Base class for all weather data forwarding services.

WHY a common base:
  Each online weather service (Wunderground, CWOP, WeatherCloud,
  OpenWeatherMap) has its own API format, transport (HTTP GET, HTTP
  POST JSON, raw TCP), and unit conventions.  The base class provides
  the common interface: ``format_payload()`` converts readings to the
  service's format, ``send()`` transmits them, and ``health_check()``
  reports status.  This mirrors the SensorBase pattern used by the
  sensor drivers.

  The forwarder calls each enabled service once per forward cycle,
  passing a normalized dict of the latest readings.  Each service
  handles its own unit conversions and error handling.

Normalized reading dict
  The forwarder passes a dict with these possible keys (all optional —
  only sensors that produced data will have entries)::

      {
          "timestamp": "2024-01-15T10:32:35+00:00",   # ISO 8601 UTC
          "temperature_c": 22.5,        # Celsius
          "humidity_pct": 65.0,         # %
          "pressure_hpa": 1013.25,      # hPa
          "wind_dir_deg": 230.0,       # degrees (0-360)
          "wind_speed_mps": 5.2,        # m/s
          "wind_gust_mps": 7.8,         # m/s
          "rain_mm": 1.2,              # mm in the last hour
          "rain_daily_mm": 3.4,        # mm since local midnight
          "dew_point_c": 15.8,         # Celsius
          "uv_index": 3.2,
          "solar_radiation_wm2": 450.0,
          "pm25_ugm3": 12.0,
          "pm10_ugm3": 18.0,
          "tvoc_ppb": 150.0,
          "co2_eq_ppm": 600.0,
      }

NOTE: Subclasses must set the class attributes ``name`` and
``description``.  They must implement ``format_payload()`` and
``send()``.  ``is_enabled`` should be overridden to check the
service's specific config fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from weather_station.core.config import ForwardingConfig

logger = logging.getLogger(__name__)


@dataclass
class ForwardingResult:
    """Result of a single forwarding attempt.

    Attributes:
        success: True if the data was accepted by the remote service.
        message: Human-readable status (for logging / CLI display).
        service: Name of the service this result came from.
        timestamp: ISO 8601 UTC of when the attempt was made.
    """
    service: str
    success: bool
    message: str
    timestamp: str = ""


class ForwardingServiceBase:
    """Abstract base for all weather data forwarding services.

    Subclasses must:
      - Set ``name`` and ``description``
      - Implement ``is_enabled()`` to check service-specific config
      - Implement ``format_payload()`` to convert readings dict → wire format
      - Implement ``send()`` to transmit the payload

    The ``mock_mode`` flag (set by the forwarder when the station runs
    in mock mode) causes ``send()`` to log the payload instead of making
    network calls, so the full pipeline can be tested without hardware
    or internet.
    """

    name: str = "base"
    description: str = "Generic forwarding service"

    def __init__(self, config: ForwardingConfig, mock_mode: bool = False) -> None:
        self.config = config
        self.mock_mode = mock_mode
        self._total_sent: int = 0
        self._total_success: int = 0
        self._total_failures: int = 0
        self._last_result: ForwardingResult | None = None
        self._consecutive_failures: int = 0

    # ── To override in subclasses ───────────────────────────────────

    def is_enabled(self) -> bool:
        """Return True if this service is configured and ready."""
        return False

    def format_payload(self, readings: dict[str, Any]) -> Any:
        """Convert the normalized readings dict to the service's wire format.

        Subclasses must implement this.  The return type depends on the
        service (dict for JSON, str for URL params, bytes for raw TCP).
        """
        raise NotImplementedError

    def send(self, payload: Any) -> ForwardingResult:
        """Transmit the payload to the remote service.

        Subclasses must implement this.  Returns a ForwardingResult.
        In mock mode, log the payload and return success without network.
        """
        raise NotImplementedError

    # ── Common interface used by the forwarder ───────────────────────

    def forward(self, readings: dict[str, Any]) -> ForwardingResult:
        """Format readings into a payload and send to the remote service.

        This is the main entry point called by DataForwarder each cycle.
        Handles error wrapping and statistics tracking.
        """
        if not self.is_enabled():
            return ForwardingResult(
                service=self.name,
                success=False,
                message="Service not enabled or not configured",
            )

        try:
            payload = self.format_payload(readings)
            if payload is None:
                return ForwardingResult(
                    service=self.name,
                    success=False,
                    message="No valid data to send (payload was None)",
                )
            result = self.send(payload)
        except Exception as e:
            result = ForwardingResult(
                service=self.name,
                success=False,
                message=f"Exception: {e}",
            )
            logger.error("[%s] forwarding failed: %s", self.name, e)

        self._total_sent += 1
        if result.success:
            self._total_success += 1
            self._consecutive_failures = 0
        else:
            self._total_failures += 1
            self._consecutive_failures += 1

        self._last_result = result
        return result

    # ── Status ────────────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Return a health/status dict for the CLI and dashboard."""
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.is_enabled(),
            "mock_mode": self.mock_mode,
            "total_sent": self._total_sent,
            "total_success": self._total_success,
            "total_failures": self._total_failures,
            "consecutive_failures": self._consecutive_failures,
            "last_result": {
                "success": self._last_result.success,
                "message": self._last_result.message,
            } if self._last_result else None,
        }
