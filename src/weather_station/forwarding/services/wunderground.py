"""
Weather Underground PWS forwarding adapter.

WHY this service:
  Weather Underground's Personal Weather Station (PWS) network is one
  of the largest community weather networks.  Data uploaded appears
  on their site in real-time and contributes to their forecasting.

PROTOCOL:
  HTTP GET with URL-encoded query parameters.
  - Standard:  https://weatherstation.wunderground.com/weatherstation/updateweatherstation.php
  - RapidFire: https://rtupdate.wunderground.com/weatherstation/updateweatherstation.php
  Required params: ID, PASSWORD, dateutc, action=updateraw
  Units: IMPERIAL (°F, mph, inches Hg, inches rain)

Docs: https://support.weather.com/s/article/PWS-Upload-Protocol

NOTE: No external dependencies.  Uses stdlib urllib only, matching the
project's zero-dependency philosophy (only flask + pyyaml).
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from weather_station.forwarding.service_base import ForwardingResult, ForwardingServiceBase

logger = logging.getLogger(__name__)

# ── Unit conversion helpers ──────────────────────────────────────────
# The station stores data in metric units.  Wunderground requires imperial.

def _c_to_f(c: float) -> float:
    """Celsius → Fahrenheit."""
    return c * 9.0 / 5.0 + 32.0

def _ms_to_mph(ms: float) -> float:
    """m/s → mph."""
    return ms * 2.23693629

def _hpa_to_inhg(hpa: float) -> float:
    """hPa → inches of mercury."""
    return hpa * 0.029529980

def _mm_to_inch(mm: float) -> float:
    """mm → inches."""
    return mm * 0.0393701


class WundergroundService(ForwardingServiceBase):
    """Forward weather data to Weather Underground PWS.

    Uses the standard PWS upload protocol (HTTP GET with URL params).
    All conversions to imperial units happen in format_payload().
    """

    name = "wunderground"
    description = "Weather Underground PWS (HTTP GET, imperial units)"

    def is_enabled(self) -> bool:
        """True if station ID and password are both configured."""
        return (
            self.config.wunderground_enabled
            and bool(self.config.wunderground_station_id)
            and bool(self.config.wunderground_password)
        )

    def format_payload(self, readings: dict[str, Any]) -> str:
        """Convert readings to a URL query string for the WU update endpoint.

        Returns the full URL with all parameters.  Only includes
        parameters for which we have data — the WU API accepts partial
        updates.
        """
        c = self.config
        params: dict[str, str] = {
            "ID": c.wunderground_station_id,
            "PASSWORD": c.wunderground_password,
            "action": "updateraw",
            # WU wants UTC in "YYYY-MM-DD HH:MM:SS" format (mysql style)
            "dateutc": _parse_utc(readings.get("timestamp")),
            "softwaretype": "weather-station-agent",
        }

        # Temperature (°F)
        if readings.get("temperature_c") is not None:
            params["tempf"] = f"{_c_to_f(readings['temperature_c']):.1f}"

        # Humidity (%)
        if readings.get("humidity_pct") is not None:
            params["humidity"] = f"{readings['humidity_pct']:.0f}"

        # Dew point (°F)
        if readings.get("dew_point_c") is not None:
            params["dewptf"] = f"{_c_to_f(readings['dew_point_c']):.1f}"

        # Pressure (inHg)
        if readings.get("pressure_hpa") is not None:
            params["baromin"] = f"{_hpa_to_inhg(readings['pressure_hpa']):.2f}"

        # Wind direction (degrees)
        if readings.get("wind_dir_deg") is not None:
            params["winddir"] = f"{readings['wind_dir_deg']:.0f}"

        # Wind speed (mph)
        if readings.get("wind_speed_mps") is not None:
            params["windspeedmph"] = f"{_ms_to_mph(readings['wind_speed_mps']):.1f}"

        # Wind gust (mph)
        if readings.get("wind_gust_mps") is not None:
            params["windgustmph"] = f"{_ms_to_mph(readings['wind_gust_mps']):.1f}"

        # Rain — last hour (inches)
        if readings.get("rain_mm") is not None:
            params["rainin"] = f"{_mm_to_inch(readings['rain_mm']):.2f}"

        # Rain — daily total (inches)
        if readings.get("rain_daily_mm") is not None:
            params["dailyrainin"] = f"{_mm_to_inch(readings['rain_daily_mm']):.2f}"

        # Solar radiation (W/m² — same unit, just pass through)
        if readings.get("solar_radiation_wm2") is not None:
            params["solarradiation"] = f"{readings['solar_radiation_wm2']:.1f}"

        # UV index
        if readings.get("uv_index") is not None:
            params["UV"] = f"{readings['uv_index']:.1f}"

        # Build the full URL
        base = (
            "https://rtupdate.wunderground.com"
            if c.wunderground_rapidfire
            else "https://weatherstation.wunderground.com"
        )
        url = f"{base}/weatherstation/updateweatherstation.php"
        query = urllib.parse.urlencode(params)
        return f"{url}?{query}"

    def send(self, payload: str) -> ForwardingResult:
        """Send the HTTP GET request to Wunderground.

        In mock mode, log the URL and return success.
        """
        if self.mock_mode:
            logger.info("[wunderground] MOCK send: %s", payload)
            return ForwardingResult(
                service=self.name,
                success=True,
                message="Mock mode: payload logged (not sent)",
            )

        try:
            req = urllib.request.Request(payload, method="GET")
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
            # WU returns "success" on success, or an error message string
            if "success" in body.lower():
                logger.debug("[wunderground] upload successful")
                return ForwardingResult(
                    service=self.name,
                    success=True,
                    message="OK",
                )
            return ForwardingResult(
                service=self.name,
                success=False,
                message=f"Server response: {body[:200]}",
            )
        except Exception as e:
            return ForwardingResult(
                service=self.name,
                success=False,
                message=f"HTTP error: {e}",
            )


def _parse_utc(timestamp: str | None) -> str:
    """Parse an ISO 8601 timestamp into WU's 'YYYY-MM-DD HH:MM:SS' format.

    If the timestamp is missing or unparseable, returns "now" which WU
    accepts as the current server time.
    """
    if not timestamp:
        return "now"
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt_utc = dt.astimezone(UTC)
        return dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "now"
