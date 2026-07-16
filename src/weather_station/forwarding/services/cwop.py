"""
CWOP (Citizen Weather Observer Program) forwarding adapter.

WHY this service:
  CWOP feeds data directly to NOAA's MADIS database, which is used by
  NWS forecast models.  This is the most impactful service for actually
  improving weather forecasts — your station data helps NWS forecasts.

PROTOCOL:
  Raw TCP connection to an APRS-IS server (cwop.aprs.net:14580).
  1. Connect to TCP port 14580
  2. Read the server greeting line
  3. Send login:  "user EW9876 pass -1 vers WeatherStationAgent 1.0\r\n"
  4. Read the server ack line
  5. Send APRS weather packet:
     "EW9876>APRS,TCPIP*:@060151z3316.04N/09631.96W_120/005g010t021r000p000P000h75b10322\r\n"
  6. Disconnect

  Packet field format (APRS weather position):
    @hhmmssz        — UTC timestamp (HHMMSSz)
    ddmm.hhN        — latitude (degrees, minutes, hundredths)
    dddmm.hhW       — longitude (degrees, minutes, hundredths)
    _ddd/sss        — wind direction (deg) / wind speed (mph)
    gggg            — wind gust (mph)
    tttt            — temperature (°F, signed)
    rrrr            — rain last hour (hundredths of inch)
    pppp            — rain last 24h (hundredths of inch)
    PPPP            — rain since midnight (hundredths of inch)
    hh              — humidity (%; h00 = 100%)
    bbbbbb          — pressure (tenths of mbar = hPa × 10)

Docs: http://wxqa.com/faq.html

NOTE: CWOP requires packets at most every 5 minutes.  The forwarder's
default interval is 300s (5 min), which is the minimum allowed.
"""

from __future__ import annotations

import logging
import socket
from datetime import UTC, datetime
from typing import Any

from weather_station.forwarding.service_base import ForwardingResult, ForwardingServiceBase

logger = logging.getLogger(__name__)

# ── Unit conversion helpers ──────────────────────────────────────────
# CWOP uses a mix: temperature in °F, wind in mph, pressure in tenths-hPa,
# rain in hundredths-of-inches, humidity in %.

def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0

def _ms_to_mph(ms: float) -> float:
    return ms * 2.23693629

def _mm_to_hundredths_inch(mm: float) -> int:
    """mm → hundredths of inches (integer)."""
    return int(round(mm * 0.0393701 * 100))

def _hpa_to_tenths_mb(hpa: float) -> int:
    """hPa → tenths of millibar (= tenths of hPa, just ×10)."""
    return int(round(hpa * 10))


def _decimal_to_aprs_lat(lat: float) -> str:
    """Convert decimal latitude to APRS format ddmm.hhN.

    Example: 33.2673 → "3316.04N"
    """
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    deg = int(lat)
    minutes = (lat - deg) * 60
    min_whole = int(minutes)
    min_hundredths = int(round((minutes - min_whole) * 100))
    # Handle rounding overflow (e.g. 99.99 → 100)
    if min_hundredths >= 100:
        min_hundredths -= 100
        min_whole += 1
    if min_whole >= 60:
        min_whole -= 60
        deg += 1
    return f"{deg:02d}{min_whole:02d}.{min_hundredths:02d}{hemi}"


def _decimal_to_aprs_lon(lon: float) -> str:
    """Convert decimal longitude to APRS format dddmm.hhW.

    Example: -96.5327 → "09631.96W"
    """
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    deg = int(lon)
    minutes = (lon - deg) * 60
    min_whole = int(minutes)
    min_hundredths = int(round((minutes - min_whole) * 100))
    if min_hundredths >= 100:
        min_hundredths -= 100
        min_whole += 1
    if min_whole >= 60:
        min_whole -= 60
        deg += 1
    return f"{deg:03d}{min_whole:02d}.{min_hundredths:02d}{hemi}"


class CWOPService(ForwardingServiceBase):
    """Forward weather data to CWOP via APRS-IS TCP.

    Connects to the APRS-IS network, logs in, sends a single weather
    position packet, and disconnects.  CWOP non-ham stations use a
    passcode of -1 (unverified) which the cwop.aprs.net servers accept.
    """

    name = "cwop"
    description = "CWOP / NOAA MADIS (TCP APRS, mixed units)"

    def is_enabled(self) -> bool:
        """True if CWOP is enabled and a station ID is configured."""
        return (
            self.config.cwop_enabled
            and bool(self.config.cwop_station_id)
        )

    def format_payload(self, readings: dict[str, Any]) -> str:
        """Build the APRS weather packet string.

        Returns a complete APRS packet ready to send over TCP.
        Missing values are replaced with '...' per APRS spec.
        """
        c = self.config
        station_id = c.cwop_station_id
        ts = readings.get("timestamp")

        # Parse timestamp → APRS format @HHMMSSz
        aprs_time = _format_aprs_time(ts)

        # Position — from station config (not from readings)
        # The forwarder passes lat/lon via readings, or we fall back
        lat = readings.get("latitude", 0.0)
        lon = readings.get("longitude", 0.0)
        aprs_lat = _decimal_to_aprs_lat(lat)
        aprs_lon = _decimal_to_aprs_lon(lon)

        # Wind direction (3 digits, degrees)
        wind_dir = readings.get("wind_dir_deg")
        dir_str = f"{int(round(wind_dir)):03d}" if wind_dir is not None else "..."

        # Wind speed (3 digits, mph)
        wind_speed = readings.get("wind_speed_mps")
        spd_str = f"{int(round(_ms_to_mph(wind_speed))):03d}" if wind_speed is not None else "..."

        # Wind gust (3 digits, mph, prefixed with 'g')
        wind_gust = readings.get("wind_gust_mps")
        gust_str = f"g{int(round(_ms_to_mph(wind_gust))):03d}" if wind_gust is not None else "g..."

        # Temperature (signed, °F)
        temp = readings.get("temperature_c")
        if temp is not None:
            temp_f = int(round(_c_to_f(temp)))
            temp_str = f"t{temp_f}" if temp_f >= 0 else f"t{temp_f}"
        else:
            temp_str = "t..."

        # Rain — last hour (hundredths of inch, 3 digits)
        rain_1h = readings.get("rain_mm")
        rain_1h_str = f"r{_mm_to_hundredths_inch(rain_1h):03d}" if rain_1h is not None else "r..."

        # Rain — last 24h (hundredths of inch, 3 digits)
        rain_24h = readings.get("rain_24h_mm")
        rain_24h_str = (
            f"p{_mm_to_hundredths_inch(rain_24h):03d}"
            if rain_24h is not None else "p..."
        )

        # Rain — since midnight (hundredths of inch, 3 digits)
        rain_daily = readings.get("rain_daily_mm")
        rain_daily_str = (
            f"P{_mm_to_hundredths_inch(rain_daily):03d}"
            if rain_daily is not None else "P..."
        )

        # Humidity (2 digits; h00 = 100%)
        humidity = readings.get("humidity_pct")
        if humidity is not None:
            h = int(round(humidity))
            hum_str = f"h{h:02d}" if h < 100 else "h00"
        else:
            hum_str = "h.."

        # Pressure (5 digits, tenths of mbar)
        pressure = readings.get("pressure_hpa")
        pres_str = f"b{_hpa_to_tenths_mb(pressure):05d}" if pressure is not None else "b....."

        # Assemble the packet
        # Format: CALLSIGN>APRS,TCPIP*:@HHMMSSzDDMM.hhN/DDDMM.hhW_data
        packet = (
            f"{station_id}>APRS,TCPIP*:"
            f"@{aprs_time}{aprs_lat}/{aprs_lon}"
            f"_{dir_str}/{spd_str}{gust_str}{temp_str}"
            f"{rain_1h_str}{rain_24h_str}{rain_daily_str}"
            f"{hum_str}{pres_str}"
        )
        return packet

    def send(self, payload: str) -> ForwardingResult:
        """Send the APRS packet over TCP to the APRS-IS server.

        In mock mode, log the packet and return success.
        """
        if self.mock_mode:
            logger.info("[cwop] MOCK send: %s", payload)
            return ForwardingResult(
                service=self.name,
                success=True,
                message="Mock mode: packet logged (not sent)",
            )

        c = self.config
        station_id = c.cwop_station_id
        login_line = (
            f"user {station_id} pass -1 vers WeatherStationAgent 1.0\r\n"
        )
        packet_line = payload + "\r\n"

        try:
            # Connect, login, send packet, disconnect
            with socket.create_connection(
                (c.cwop_server, c.cwop_port),
                timeout=c.timeout_seconds,
            ) as sock:
                # Read server greeting
                greeting = sock.recv(1024).decode("ascii", errors="replace")
                logger.debug("[cwop] server greeting: %s", greeting.strip())

                # Send login
                sock.sendall(login_line.encode("ascii"))

                # Read login ack
                ack = sock.recv(1024).decode("ascii", errors="replace")
                logger.debug("[cwop] login ack: %s", ack.strip())

                # Send weather packet
                sock.sendall(packet_line.encode("ascii"))

                # Brief wait for the server to process, then close
                sock.settimeout(2.0)
                try:
                    final = sock.recv(1024)
                    logger.debug("[cwop] response: %s",
                                  final.decode("ascii", errors="replace").strip())
                except TimeoutError:
                    pass  # no response is normal after sending

            logger.debug("[cwop] packet sent successfully")
            return ForwardingResult(
                service=self.name,
                success=True,
                message="OK",
            )
        except Exception as e:
            return ForwardingResult(
                service=self.name,
                success=False,
                message=f"TCP error: {e}",
            )


def _format_aprs_time(timestamp: str | None) -> str:
    """Parse ISO 8601 and format as APRS time 'HHMMSSz' (UTC).

    Falls back to current UTC time if timestamp is missing.
    """
    if not timestamp:
        return datetime.now(UTC).strftime("%H%M%Sz")
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%H%M%Sz")
    except (ValueError, TypeError):
        return datetime.now(UTC).strftime("%H%M%Sz")
