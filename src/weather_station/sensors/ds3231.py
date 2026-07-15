"""
DS3231 real-time clock (RTC) driver with integrated temperature sensor.

The Maxim DS3231 is a precision RTC with a built-in TCXO (temperature-
compensated crystal oscillator) and an integrated temperature sensor.
It communicates over I²C at address 0x68.  Unlike cheaper RTCs (DS1307),
the DS3231's TCXO maintains ±2 ppm accuracy across the full -40 °C to
+85 °C range, making it the gold standard for battery-backed timekeeping
on embedded platforms.

WHY this matters for weather:
  Accurate timestamps are essential for correlating sensor readings
  across the weather station and for historical data analysis.  The
  Raspberry Pi's system clock is set from NTP when network is available,
  but a weather station may be deployed offline (remote location, no
  WiFi).  The DS3231 with a CR2032 backup battery keeps time across
  reboots and power loss, guaranteeing every reading has a trustworthy
  timestamp.

  The built-in temperature sensor (±3 °C accuracy) is a bonus: it gives
  us a rough ambient temperature reading near the RTC, useful as a
  sanity check against the primary temperature sensors (BME680/BME280).
  It's not lab-grade, but if it disagrees wildly with the BME680, it
  flags a sensor fault.

SPECIAL ROLE:
  This sensor is designated the *authoritative time source* for the
  weather station.  Other sensors reference its ``rtc_time`` output to
  stamp their readings, so a drift here propagates everywhere.  The
  orchestrator should initialize this sensor first and, if healthy,
  sync the system clock to it (via ``hwclock`` or direct ``settimeofday``)
  before reading other sensors.

NOTE: adafruit_ds3231 depends on adafruit_bus_device and Blinka, both
      of which require a real I²C bus.  The import is guarded so mock
      mode works on any machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware library ────────────────────────────────────────────
# adafruit_ds3231 provides the DS3231 class wrapping register-level I²C
# access to the RTC and temperature registers.
try:
    import board  # type: ignore[import-not-found]
    import busio  # type: ignore[import-not-found]
    import adafruit_ds3231  # type: ignore[import-not-found]
    _HAS_DS3231 = True
except ImportError:
    _HAS_DS3231 = False
    logger.debug("adafruit_ds3231 not available — DS3231 will require mock mode")


# ── Constants ────────────────────────────────────────────────────────────
_DS3231_DEFAULT_ADDR = 0x68    # DS3231 fixed I²C address (no pin options)


@dataclass(frozen=True)
class DS3231Config:
    """Immutable configuration for the DS3231 driver."""

    i2c_address: int = _DS3231_DEFAULT_ADDR


class DS3231Sensor(SensorBase):
    """Driver for the DS3231 RTC + temperature sensor.

    Metrics produced:
      rtc_time       — current RTC time as ISO 8601 UTC string
      temperature_c — RTC die temperature (°C), rough ambient proxy

    NOTE: rtc_time is stored as a string (ISO 8601) in the metrics dict
          because SensorReading.metrics is typed ``dict[str, float]``.
          The float value is the Unix epoch; the ISO string is carried in
          metadata for human readability.  See _read_hardware / _read_mock
          for details.
    """

    name = "ds3231"
    bus_type = "i2c"
    description = "DS3231 RTC (authoritative timestamp) + temperature sensor"
    # NOTE: We declare rtc_time and temperature_c as the metric names.
    # The actual ISO string lives in metadata["rtc_time_iso"] because
    # SensorReading.metrics is typed dict[str, float].  The float value
    # under "rtc_time" is the Unix epoch timestamp.
    metrics = [
        "rtc_time",
        "temperature_c",
    ]

    def __init__(
        self,
        i2c_address: int = _DS3231_DEFAULT_ADDR,
        mock_mode: bool = False,
    ) -> None:
        self._config = DS3231Config(i2c_address=i2c_address)
        self._i2c = None           # busio.I2C bus handle
        self._rtc = None           # adafruit_ds3231.DS3231 object
        self._mock: MockManager | None = None
        super().__init__(mock_mode=mock_mode)

    # ── Hardware lifecycle ──────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Set up the I²C bus and DS3231 RTC object.

        Return False if the library is missing or the RTC is not
        responding at the configured address.
        """
        if not _HAS_DS3231:
            logger.error(
                "[ds3231] adafruit_ds3231 not installed — cannot use hardware mode"
            )
            return False

        try:
            self._i2c = busio.I2C(board.SCL, board.SDA)
            self._rtc = adafruit_ds3231.DS3231(self._i2c)
            logger.info(
                "[ds3231] initialized at I²C address 0x%02x",
                self._config.i2c_address,
            )
            return True
        except (ValueError, OSError) as e:
            # ValueError: no device at address.  OSError: bus error.
            logger.error("[ds3231] RTC not found at 0x%02x: %s",
                         self._config.i2c_address, e)
            self._rtc = None
            self._i2c = None
            return False
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[ds3231] unexpected init error: %s", e)
            self._rtc = None
            self._i2c = None
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Read current time and temperature from the DS3231.

        The Adafruit library's ``.datetime`` property returns a
        ``time.struct_time`` in the RTC's local time (which we treat as
        UTC — the DS3231 has no timezone awareness, so we configure it to
        UTC at setup time).  The ``.temperature`` property returns a
        float in °C.
        """
        if self._rtc is None:
            logger.warning("[ds3231] no RTC object — call initialize() first")
            return None

        try:
            # ── Read RTC time ──────────────────────────────────────────
            # .datetime returns struct_time; convert to datetime for ISO
            # formatting.  We assume the RTC is set to UTC.
            struct_time = self._rtc.datetime
            rtc_dt = datetime(
                year=struct_time.tm_year,
                month=struct_time.tm_mon,
                day=struct_time.tm_mday,
                hour=struct_time.tm_hour,
                minute=struct_time.tm_min,
                second=struct_time.tm_sec,
                tzinfo=timezone.utc,
            )
            rtc_iso = rtc_dt.isoformat()
            rtc_epoch = rtc_dt.timestamp()

            # ── Read temperature ───────────────────────────────────────
            # The DS3231 temperature register updates every 64 seconds.
            # Accuracy is ±3 °C — good for a sanity check, not for
            # primary temperature reporting.
            temp_c = float(self._rtc.temperature)

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    # Float epoch for numerical operations / storage
                    "rtc_time": rtc_epoch,
                    "temperature_c": temp_c,
                },
                units={
                    "rtc_time": "epoch_seconds",
                    "temperature_c": "celsius",
                },
                metadata={
                    # Human-readable ISO string for logs / dashboard
                    "rtc_time_iso": rtc_iso,
                    "i2c_address": self._config.i2c_address,
                    "authoritative_time": True,
                },
            )
        except OSError as e:
            # I²C read failure — bus error or sensor disconnect.
            logger.error("[ds3231] I²C read error: %s", e)
            return None
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[ds3231] unexpected read error: %s", e)
            return None

    def _read_mock(self) -> SensorReading:
        """Generate mock RTC data: system time + MockManager temperature.

        In mock mode we use the host system clock as the "RTC" time (since
        there's no hardware clock to read) and MockManager for the
        temperature value.  This keeps the timestamp pipeline exercisable
        on dev machines.
        """
        if self._mock is None:
            self._mock = MockManager()

        # Use system time as the mock RTC time
        now = datetime.now(timezone.utc)
        rtc_iso = now.isoformat()
        rtc_epoch = now.timestamp()

        # MockManager provides a diurnal-temperature-cycled value
        temp_c = self._mock.get("temperature_c")

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "rtc_time": rtc_epoch,
                "temperature_c": temp_c,
            },
            units={
                "rtc_time": "epoch_seconds",
                "temperature_c": "celsius",
            },
            metadata={
                "rtc_time_iso": rtc_iso,
                "mock": True,
                "authoritative_time": True,
            },
        )