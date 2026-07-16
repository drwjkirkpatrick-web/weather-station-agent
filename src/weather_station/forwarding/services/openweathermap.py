"""
OpenWeatherMap Station API 3.0 forwarding adapter.

WHY this service:
  OpenWeatherMap is a major global weather data provider.  Their
  Station API 3.0 lets PWS owners contribute data that's used in their
  forecasting and data products.

PROTOCOL:
  HTTP POST with JSON body.
  Endpoint: https://api.openweathermap.org/data/3.0/measurements
  Auth: API key as query param (?appid=...)
  Content-Type: application/json
  Body: array of measurement objects.
  Units: METRIC (°C, m/s, hPa, mm).
  Expected HTTP 204 (No Content) on success.

  Example body:
  [{
      "station_id": "583436dd9643a9000196b8d6",
      "dt": 1479817340,            # Unix timestamp
      "temperature": 18.7,          # Celsius
      "wind_speed": 1.2,            # m/s
      "wind_gust": 3.4,            # m/s
      "wind_deg": 230,             # degrees
      "pressure": 1021,            # hPa
      "humidity": 87,              # %
      "rain_1h": 2.0,              # mm
  }]

  NOTE: Station must be registered first via POST /stations to get
  the internal station_id.  This adapter only sends measurements;
  station registration is a one-time setup done separately.

Docs: https://openweathermap.org/stations

NOTE: No external dependencies.  Uses stdlib urllib only.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from weather_station.forwarding.service_base import ForwardingResult, ForwardingServiceBase

logger = logging.getLogger(__name__)

OWM_URL = "https://api.openweathermap.org/data/3.0/measurements"


class OpenWeatherMapService(ForwardingServiceBase):
    """Forward weather data to OpenWeatherMap Station API 3.0.

    Uses HTTP POST with JSON body.  All units are metric (no scaling).
    Requires a pre-registered station_id and API key.
    """

    name = "openweathermap"
    description = "OpenWeatherMap Station API 3.0 (HTTP POST JSON, metric)"

    def is_enabled(self) -> bool:
        """True if API key and station ID are both configured."""
        return (
            self.config.openweathermap_enabled
            and bool(self.config.openweathermap_api_key)
            and bool(self.config.openweathermap_station_id)
        )

    def format_payload(self, readings: dict[str, Any]) -> dict[str, Any]:
        """Convert readings to an OpenWeatherMap measurement object.

        Returns a dict ready to be JSON-serialized as a single-element
        array in the request body.  Only includes fields we have data
        for — OWM accepts partial measurements.
        """
        c = self.config
        payload: dict[str, Any] = {
            "station_id": c.openweathermap_station_id,
        }

        # Unix timestamp (seconds)
        ts = readings.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                payload["dt"] = int(dt.timestamp())
            except (ValueError, TypeError):
                pass
        if "dt" not in payload:
            payload["dt"] = int(datetime.now(UTC).timestamp())

        # Temperature (°C — direct)
        if readings.get("temperature_c") is not None:
            payload["temperature"] = round(readings["temperature_c"], 1)

        # Humidity (%)
        if readings.get("humidity_pct") is not None:
            payload["humidity"] = int(round(readings["humidity_pct"]))

        # Pressure (hPa — direct)
        if readings.get("pressure_hpa") is not None:
            payload["pressure"] = round(readings["pressure_hpa"], 1)

        # Wind speed (m/s — direct)
        if readings.get("wind_speed_mps") is not None:
            payload["wind_speed"] = round(readings["wind_speed_mps"], 1)

        # Wind gust (m/s — direct)
        if readings.get("wind_gust_mps") is not None:
            payload["wind_gust"] = round(readings["wind_gust_mps"], 1)

        # Wind direction (degrees)
        if readings.get("wind_dir_deg") is not None:
            payload["wind_deg"] = int(round(readings["wind_dir_deg"]))

        # Rain — last hour (mm — direct)
        if readings.get("rain_mm") is not None:
            payload["rain_1h"] = round(readings["rain_mm"], 1)

        # Rain — last 24h (mm — direct)
        if readings.get("rain_24h_mm") is not None:
            payload["rain_24h"] = round(readings["rain_24h_mm"], 1)

        # Dew point (°C — direct)
        if readings.get("dew_point_c") is not None:
            payload["dew_point"] = round(readings["dew_point_c"], 1)

        return payload

    def send(self, payload: dict[str, Any]) -> ForwardingResult:
        """Send the HTTP POST JSON request to OpenWeatherMap.

        OWM expects an array of measurements.  We send one per request.
        Returns success on HTTP 204 (No Content).
        In mock mode, log the payload and return success.
        """
        if self.mock_mode:
            logger.info("[openweathermap] MOCK send: %s",
                        json.dumps(payload, indent=2))
            return ForwardingResult(
                service=self.name,
                success=True,
                message="Mock mode: payload logged (not sent)",
            )

        c = self.config
        try:
            # Build URL with API key as query param
            url = f"{OWM_URL}?appid={urllib.parse.quote(c.openweathermap_api_key)}"
            body = json.dumps([payload]).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=c.timeout_seconds) as resp:
                status = resp.status
                resp_body = resp.read().decode("utf-8", errors="replace").strip()

            # OWM returns 204 on success (No Content)
            if status == 204:
                logger.debug("[openweathermap] upload successful (204)")
                return ForwardingResult(
                    service=self.name,
                    success=True,
                    message="OK (204)",
                )
            return ForwardingResult(
                service=self.name,
                success=False,
                message=f"HTTP {status}: {resp_body[:200]}",
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            return ForwardingResult(
                service=self.name,
                success=False,
                message=f"HTTP {e.code}: {body}",
            )
        except Exception as e:
            return ForwardingResult(
                service=self.name,
                success=False,
                message=f"HTTP error: {e}",
            )
