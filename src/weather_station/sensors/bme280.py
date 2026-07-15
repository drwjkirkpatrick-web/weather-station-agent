"""
BME280 sensor driver — temperature, humidity, pressure.

The BME280 is the younger sibling of the BME680: same T/H/P element
but without the gas sensor.  It is cheaper, draws far less power
(~3.6 µA in forced mode), and is the most popular weather sensor in
the maker community.

WHY include both BME280 and BME680?
  - Redundancy: if the BME680's gas heater causes self-heating that
    biases its temperature reading, the BME280 (which has no heater)
    provides a cleaner baseline temperature.
  - Low-power mode: the BME280 can run on battery for weeks where the
    BME680 would drain it in days.
  - Cost: for stations that only need T/H/P, the BME280 is a better fit.

NOTE: The BME280's pressure readings are used for short-term weather
forecasting.  A drop of >3 hPa over 3 hours often indicates an
approaching low-pressure system (rain/wind).  The alert subsystem
watches for exactly this pattern.

Hardware:
  - I2C address 0x76 (Bosch default) or 0x77.
  - 3.3 V operation — no level shifter needed for Pi Zero 2 W.
  - Library: ``adafruit_bme280`` (install via ``pip install adafruit-circuitpython-bme280``).
"""

from __future__ import annotations

import logging
from typing import Any

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware libraries ─────────────────────────────────────────
# WHY deferred import: the CircuitPython stack (board, busio,
# adafruit_bme280) is only available on a Pi with the kernel I2C
# driver enabled.  On a dev laptop these imports fail, but the module
# must still import cleanly so mock mode works.
try:
    import board  # noqa: F401
    import busio  # noqa: F401
    import adafruit_bme280
    _BME280_AVAILABLE = True
except ImportError:
    _BME280_AVAILABLE = False


class BME280Sensor(SensorBase):
    """Driver for the Bosch BME280 temperature/humidity/pressure sensor.

    Measures three metrics:
      - ``temperature_c`` — air temperature in °C
      - ``humidity_pct``   — relative humidity (0-100 %)
      - ``pressure_hpa``   — barometric pressure in hPa

    This is a simpler driver than the BME680 because there is no gas
    sensor and therefore no IAQ derivation.  The read path is
    straightforward: read three properties and package them.
    """

    # ── Class-level metadata (read by the orchestrator) ─────────────────
    name: str = "bme280"
    metrics: list[str] = [
        "temperature_c",
        "humidity_pct",
        "pressure_hpa",
    ]
    bus_type: str = "i2c"
    description: str = "Bosch BME280 — temperature, humidity, pressure"

    def __init__(
        self,
        i2c_address: int = 0x76,
        mock_mode: bool = False,
    ) -> None:
        """Create a BME280 sensor driver.

        Args:
            i2c_address: I2C bus address.  0x76 is the Bosch default
                (different from BME680's 0x77, so both can coexist on
                the same bus without an address clash).
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
        """Open the I2C bus and initialise the BME280.

        Returns True on success, False if the library is missing or
        the sensor does not respond at the configured address.
        """
        if not _BME280_AVAILABLE:
            logger.error(
                "[bme280] adafruit_bme280 / board / busio not installed — "
                "install with: pip install adafruit-circuitpython-bme280"
            )
            return False

        try:
            self._i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_bme280.Adafruit_BME280_I2C(
                self._i2c, address=self._i2c_address
            )
            logger.info(
                "[bme280] I2C sensor initialised at address 0x%02X", self._i2c_address
            )
            return True
        except Exception as e:
            logger.error("[bme280] hardware init failed: %s", e)
            self._sensor = None
            self._i2c = None
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Take a single reading from the physical BME280.

        Returns a SensorReading with three metrics, or None on error.
        """
        if self._sensor is None:
            logger.warning("[bme280] _read_hardware called but sensor is None")
            return None

        try:
            temperature = self._sensor.temperature
            humidity = self._sensor.humidity
            pressure = self._sensor.pressure

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "temperature_c": round(temperature, 2),
                    "humidity_pct": round(humidity, 2),
                    "pressure_hpa": round(pressure, 2),
                },
                units={
                    "temperature_c": "celsius",
                    "humidity_pct": "percent",
                    "pressure_hpa": "hPa",
                },
                metadata={
                    "i2c_address": f"0x{self._i2c_address:02X}",
                },
            )
        except Exception as e:
            logger.error("[bme280] hardware read failed: %s", e)
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
                "pressure_hpa": round(self._mock.get("pressure_hpa"), 2),
            },
            units={
                "temperature_c": "celsius",
                "humidity_pct": "percent",
                "pressure_hpa": "hPa",
            },
            metadata={
                "mock": True,
            },
        )