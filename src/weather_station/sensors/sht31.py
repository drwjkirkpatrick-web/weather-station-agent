"""
SHT31 sensor driver — high-accuracy temperature and humidity.

The SHT31 is a precision temperature/humidity sensor from Sensirion.
Unlike the Bosch sensors, it has no pressure element — but it
significantly outperforms both the BME280 and BME680 in accuracy:
±0.2 °C and ±1.5 %RH versus the Bosch parts' ±1 °C and ±3 %RH.

WHY this sensor matters for weather:
  - Dew point and heat index calculations are only as good as the
    temperature/humidity inputs.  When the station needs to report
    "feels-like" temperature or predict frost (dew point ≤ 0 °C), the
    SHT31's accuracy matters.
  - The SHT31 has a built-in heater for condensation removal, useful
    in high-humidity outdoor enclosures where the BME680 might read
    100 % RH due to surface moisture rather than true atmospheric
    humidity.
  - Fast response time (~2 s) makes it ideal for monitoring rapid
    changes during weather fronts.

NOTE: The SHT31 is often used as a "reference" sensor in this station
to validate the BME680/BME280 readings.  If all three disagree on
temperature by >2 °C, the orchestrator can flag a sensor fault.

Hardware:
  - I2C address 0x44 (default) or 0x45 (alternate pin strap).
  - 3.3 V operation — directly compatible with Pi Zero 2 W GPIO.
  - Library: ``adafruit_sht31d`` (install via ``pip install adafruit-circuitpython-sht31d``).
"""

from __future__ import annotations

import logging
from typing import Any

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware libraries ─────────────────────────────────────────
# WHY try/except: same rationale as the other I2C drivers — the
# CircuitPython packages are Pi-only; on a laptop the module must
# still import so mock mode is usable.
try:
    import board  # noqa: F401
    import busio  # noqa: F401
    import adafruit_sht31d
    _SHT31_AVAILABLE = True
except ImportError:
    _SHT31_AVAILABLE = False


class SHT31Sensor(SensorBase):
    """Driver for the Sensirion SHT31 high-accuracy T/H sensor over I2C.

    Measures two metrics:
      - ``temperature_c`` — air temperature in °C (±0.2 °C accuracy)
      - ``humidity_pct``   — relative humidity in % (±1.5 %RH accuracy)

    No pressure, no gas — just the two metrics where precision matters
    most.  This is the simplest driver in the station.
    """

    # ── Class-level metadata (read by the orchestrator) ─────────────────
    name: str = "sht31"
    metrics: list[str] = [
        "temperature_c",
        "humidity_pct",
    ]
    bus_type: str = "i2c"
    description: str = "Sensirion SHT31 — high-accuracy temperature & humidity"

    def __init__(
        self,
        i2c_address: int = 0x44,
        mock_mode: bool = False,
    ) -> None:
        """Create an SHT31 sensor driver.

        Args:
            i2c_address: I2C bus address.  0x44 is the Sensirion default;
                0x45 is the alternate (selected by pulling the ADDR pin
                high on the breakout board).
            mock_mode: If True, never touch hardware — return mock data.
        """
        super().__init__(mock_mode=mock_mode)

        self._i2c_address: int = i2c_address

        # Hardware handles — populated by _init_hardware().
        self._sensor: Any = None
        self._i2c: Any = None

        # Lazy-instantiated mock generator.
        self._mock: MockManager | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Open the I2C bus and initialise the SHT31.

        Returns True on success, False if the library is missing or
        the sensor does not respond at the configured address.
        """
        if not _SHT31_AVAILABLE:
            logger.error(
                "[sht31] adafruit_sht31d / board / busio not installed — "
                "install with: pip install adafruit-circuitpython-sht31d"
            )
            return False

        try:
            self._i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_sht31d.SHT31_I2C(
                self._i2c, address=self._i2c_address
            )
            logger.info(
                "[sht31] I2C sensor initialised at address 0x%02X", self._i2c_address
            )
            return True
        except Exception as e:
            logger.error("[sht31] hardware init failed: %s", e)
            self._sensor = None
            self._i2c = None
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Take a single reading from the physical SHT31.

        Returns a SensorReading with two metrics, or None on error.

        NOTE: The SHT31 measurement takes ~15 ms.  The Adafruit library
        blocks until the reading is ready, so no manual sleep is needed.
        """
        if self._sensor is None:
            logger.warning("[sht31] _read_hardware called but sensor is None")
            return None

        try:
            temperature = self._sensor.temperature
            humidity = self._sensor.relative_humidity

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "temperature_c": round(temperature, 2),
                    "humidity_pct": round(humidity, 2),
                },
                units={
                    "temperature_c": "celsius",
                    "humidity_pct": "percent",
                },
                metadata={
                    "i2c_address": f"0x{self._i2c_address:02X}",
                },
            )
        except Exception as e:
            logger.error("[sht31] hardware read failed: %s", e)
            return None

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock readings using MockManager."""
        if self._mock is None:
            self._mock = MockManager()

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "temperature_c": round(self._mock.get("temperature_c"), 2),
                "humidity_pct": round(self._mock.get("humidity_pct"), 2),
            },
            units={
                "temperature_c": "celsius",
                "humidity_pct": "percent",
            },
            metadata={
                "mock": True,
            },
        )