"""
WeatherCloud forwarding adapter.

WHY this service:
  WeatherCloud is a free global network of weather stations with
  maps, graphs, and social features.  Good for visual display and
  sharing.

PROTOCOL:
  HTTP GET with URL query parameters.
  Endpoint: http://api.weathercloud.net/v01/set
  Authentication: deviceid + devicekey in the URL params
  Units: METRIC, but values are scaled by ×10 (integer).
  - temp: °C × 10        (e.g. 22.5°C → 225)
  - hum: %               (no scaling)
  - bar: hPa × 10        (e.g. 1013.25 → 10133)
  - wspd: m/s × 10       (e.g. 5.2 → 52)
  - wdir: degrees         (no scaling)
  - rain: mm × 10        (e.g. 1.2 → 12)
  - rainrate: mm/h × 10
  - time: "YYYYMMDD HHMMSS" (local time)

  Also requires fixed params: ver (software version), type (station type)

Docs: obtained from pywws source (pywws.service.weathercloud) and
official API docs from WeatherCloud support.

NOTE: No external dependencies.  Uses stdlib urllib only.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from weather_station.forwarding.service_base import ForwardingResult, ForwardingServiceBase

logger = logging.getLogger(__name__)

# WeatherCloud endpoint
WC_URL = "http://api.weathercloud.net/v01/set"


def _scale10(value: float | None) -> int | None:
    """Scale a float by 10 and round to integer (WeatherCloud convention)."""
    if value is None:
        return None
    return int(round(value * 10))


class WeathercloudService(ForwardingServiceBase):
    """Forward weather data to WeatherCloud.

    Uses the v01/set HTTP GET endpoint.  All metric values are
    multiplied by 10 and sent as integers (per the WeatherCloud API).
    """

    name = "weathercloud"
    description = "WeatherCloud (HTTP GET, metric ×10 units)"

    def is_enabled(self) -> bool:
        """True if device ID and key are both configured."""
        return (
            self.config.weathercloud_enabled
            and bool(self.config.weathercloud_device_id)
            and bool(self.config.weathercloud_device_key)
        )

    def format_payload(self, readings: dict[str, Any]) -> str:
        """Convert readings to a WeatherCloud URL query string.

        Returns the full URL with all parameters.  WeatherCloud uses
        metric units scaled by 10, sent as GET params.
        """
        c = self.config
        params: dict[str, str] = {
            "deviceid": c.weathercloud_device_id,
            "devicekey": c.weathercloud_device_key,
            "ver": "1.0",
            "type": "481",  # generic weather station type code
        }

        # Temperature (°C × 10)
        if readings.get("temperature_c") is not None:
            params["temp"] = str(_scale10(readings["temperature_c"]))

        # Humidity (%)
        if readings.get("humidity_pct") is not None:
            params["hum"] = str(int(round(readings["humidity_pct"])))

        # Dew point (°C × 10)
        if readings.get("dew_point_c") is not None:
            params["dew"] = str(_scale10(readings["dew_point_c"]))

        # Pressure (hPa × 10)
        if readings.get("pressure_hpa") is not None:
            params["bar"] = str(_scale10(readings["pressure_hpa"]))

        # Wind speed average (m/s × 10)
        if readings.get("wind_speed_mps") is not None:
            params["wspd"] = str(_scale10(readings["wind_speed_mps"]))
            params["wspdavg"] = str(_scale10(readings["wind_speed_mps"]))

        # Wind gust (m/s × 10)
        if readings.get("wind_gust_mps") is not None:
            params["wspdhi"] = str(_scale10(readings["wind_gust_mps"]))

        # Wind direction (degrees)
        if readings.get("wind_dir_deg") is not None:
            wdir = int(round(readings["wind_dir_deg"]))
            params["wdir"] = str(wdir)
            params["wdiravg"] = str(wdir)

        # Rain — daily total (mm × 10)
        if readings.get("rain_daily_mm") is not None:
            params["rain"] = str(_scale10(readings["rain_daily_mm"]))

        # Rain rate — last hour (mm/h × 10)
        if readings.get("rain_mm") is not None:
            params["rainrate"] = str(_scale10(readings["rain_mm"]))

        # Solar radiation (W/m² × 10)
        if readings.get("solar_radiation_wm2") is not None:
            params["solarrad"] = str(_scale10(readings["solar_radiation_wm2"]))

        # UV index (× 10)
        if readings.get("uv_index") is not None:
            params["uvi"] = str(_scale10(readings["uv_index"]))

        # Timestamp — "YYYYMMDD HHMMSS" format
        ts = readings.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                params["time"] = dt.strftime("%Y%m%d %H%M%S")
            except (ValueError, TypeError):
                pass

        query = urllib.parse.urlencode(params)
        return f"{WC_URL}?{query}"

    def send(self, payload: str) -> ForwardingResult:
        """Send the HTTP GET request to WeatherCloud.

        WeatherCloud returns a plain-text status code: "200" = success.
        In mock mode, log the URL and return success.
        """
        if self.mock_mode:
            logger.info("[weathercloud] MOCK send: %s", payload)
            return ForwardingResult(
                service=self.name,
                success=True,
                message="Mock mode: payload logged (not sent)",
            )

        try:
            req = urllib.request.Request(payload, method="GET")
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
            # WeatherCloud returns "200" as plain text on success
            if body == "200":
                logger.debug("[weathercloud] upload successful")
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
