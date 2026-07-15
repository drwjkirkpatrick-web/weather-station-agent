"""
BME680 sensor driver — temperature, humidity, pressure, gas resistance, IAQ.

The BME680 is a 4-in-1 digital sensor from Bosch that combines a
temperature/humidity/pressure element with a small metal-oxide gas
sensor.  It is the "flagship" environmental sensor for this weather
station because it captures the most information per I2C address.

WHY this sensor matters for weather:
  - Temperature and humidity are the two core meteorological variables
    that drive every other weather calculation (dew point, heat index,
    forecasting models).
  - Barometric pressure is the single best predictor of short-term
    weather changes — a rapid pressure drop often precedes storms.
  - The gas sensor reacts to a broad mix of VOCs (volatile organic
    compounds).  Outdoors this is a proxy for air quality: high VOC
    levels come from vehicle exhaust, industrial emissions, wildfire
    smoke, or natural sources.  The Bosch-recommended IAQ (Indoor Air
    Quality) index maps the gas resistance to a 0-500 scale where lower
    values mean cleaner air.

NOTE: The gas sensor is heated to ~300 °C inside the chip and takes
roughly 5 ms to settle.  The Adafruit library handles the heater
profile automatically.  In exchange, the BME680 draws ~18 mA during a
gas measurement — significant for a battery-powered Pi Zero 2 W, so
keep the sample interval ≥ 60 s.

Hardware:
  - I2C address 0x77 (Bosch default) or 0x76 (alternate).
  - Operates at 3.3 V — safe for Pi Zero 2 W GPIO (no level shifter).
  - Library: ``adafruit_bme680`` (install via ``pip install adafruit-circuitpython-bme680``).
"""

from __future__ import annotations

import logging
from typing import Any

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware libraries ─────────────────────────────────────────
# WHY deferred import: these packages only exist on a Pi with
# CircuitPython installed.  Importing them at module level would crash
# the entire agent on a development laptop.  We import lazily inside
# _init_hardware() so the module is always importable and mock mode
# works everywhere.
try:
    import board  # noqa: F401  — used inside _init_hardware
    import busio  # noqa: F401
    import adafruit_bme680
    _BME680_AVAILABLE = True
except ImportError:
    _BME680_AVAILABLE = False


class BME680Sensor(SensorBase):
    """Driver for the Bosch BME680 environmental sensor over I2C.

    Measures five metrics:
      - ``temperature_c``   — air temperature in °C
      - ``humidity_pct``    — relative humidity (0-100 %)
      - ``pressure_hpa``    — barometric pressure in hPa
      - ``gas_resistance_ohms`` — raw hot-plate gas sensor resistance
      - ``iaq``             — derived air-quality index (0 = clean, 500 = very polluted)

    The IAQ value is derived from gas resistance using a linear mapping
    (see ``_calculate_iaq`` below) rather than the proprietary Bosch
    algorithm, which requires a closed-source binary blob.  This keeps
    the driver fully open-source while still giving a useful relative
    air-quality trend.
    """

    # ── Class-level metadata (read by the orchestrator) ─────────────────
    name: str = "bme680"
    metrics: list[str] = [
        "temperature_c",
        "humidity_pct",
        "pressure_hpa",
        "gas_resistance_ohms",
        "iaq",
    ]
    bus_type: str = "i2c"
    description: str = "Bosch BME680 — T/H/P + gas/IAQ"

    # ── IAQ mapping constants ───────────────────────────────────────────
    # WHY these ranges: Bosch's own IAQ index runs 0-500.  Empirically,
    # clean outdoor air gives ~50 kΩ gas resistance, while polluted air
    # drops the resistance to ~1 kΩ.  We linearly map between those two
    # physical bounds to the 0-500 quality scale.
    # NOTE: Lower gas resistance = more VOCs = WORSE air quality → higher IAQ.
    _IAQ_GAS_MIN: float = 1_000.0       # ohms — worst air quality
    _IAQ_GAS_MAX: float = 50_000.0     # ohms — best air quality
    _IAQ_SCALE_MIN: float = 0.0        # IAQ when gas = _IAQ_GAS_MAX
    _IAQ_SCALE_MAX: float = 500.0      # IAQ when gas = _IAQ_GAS_MIN

    def __init__(
        self,
        i2c_address: int = 0x77,
        sea_level_pressure: float = 1013.25,
        mock_mode: bool = False,
    ) -> None:
        """Create a BME680 sensor driver.

        Args:
            i2c_address: I2C bus address (0x77 default, 0x76 alternate).
            sea_level_pressure: Reference pressure in hPa.  The sensor
                uses this to compute altitude; we store it so pressure
                readings can be normalised if needed.
            mock_mode: If True, never touch hardware — return mock data.
        """
        super().__init__(mock_mode=mock_mode)

        # Config stored for later use by _init_hardware / _read_hardware.
        self._i2c_address: int = i2c_address
        self._sea_level_pressure: float = sea_level_pressure

        # Hardware handle — populated by _init_hardware(), None until then.
        self._sensor: Any = None
        self._i2c: Any = None

        # MockManager is only instantiated when needed (lazy) to keep the
        # real-hardware path lightweight.
        self._mock: MockManager | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Open the I2C bus and initialise the BME680.

        Returns True on success, False if the library is missing or the
        sensor does not respond at the configured address.

        NOTE: This is only called when ``mock_mode`` is False — the base
        class short-circuits to True for mock mode before calling us.
        """
        if not _BME680_AVAILABLE:
            # WHY we log here rather than at import time: the module must
            # always import cleanly; the failure is only relevant when
            # someone actually tries to use real hardware.
            logger.error(
                "[bme680] adafruit_bme680 / board / busio not installed — "
                "install with: pip install adafruit-circuitpython-bme680"
            )
            return False

        try:
            # board.SCL / board.SDA are the Pi's I2C clock/data pins.
            self._i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_bme680.Adafruit_BME680_I2C(
                self._i2c, address=self._i2c_address
            )
            # Set the sea-level reference so the library can report
            # altitude if we ever want it.  Pressure readings are absolute.
            self._sensor.sea_level_pressure = self._sea_level_pressure
            logger.info(
                "[bme680] I2C sensor initialised at address 0x%02X", self._i2c_address
            )
            return True
        except Exception as e:
            # Most common: ValueError if no device at address, or
            # OSError if the I2C bus is not enabled on the Pi.
            logger.error("[bme680] hardware init failed: %s", e)
            self._sensor = None
            self._i2c = None
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Take a single reading from the physical BME680.

        Returns a SensorReading with all five metrics, or None if any
        hardware error occurs.  We read all values in one shot because
        the BME680 performs a single conversion cycle per ``read()``.
        """
        if self._sensor is None:
            logger.warning("[bme680] _read_hardware called but sensor is None")
            return None

        try:
            temperature = self._sensor.temperature
            humidity = self._sensor.humidity
            pressure = self._sensor.pressure
            gas = self._sensor.gas

            iaq = self._calculate_iaq(gas)

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "temperature_c": round(temperature, 2),
                    "humidity_pct": round(humidity, 2),
                    "pressure_hpa": round(pressure, 2),
                    "gas_resistance_ohms": round(gas, 1),
                    "iaq": round(iaq, 1),
                },
                units={
                    "temperature_c": "celsius",
                    "humidity_pct": "percent",
                    "pressure_hpa": "hPa",
                    "gas_resistance_ohms": "ohms",
                    "iaq": "index",
                },
                metadata={
                    "i2c_address": f"0x{self._i2c_address:02X}",
                    "sea_level_pressure": self._sea_level_pressure,
                    "iaq_source": "linear_gas_resistance",
                },
            )
        except Exception as e:
            logger.error("[bme680] hardware read failed: %s", e)
            return None

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock readings using MockManager.

        MockManager keeps state across calls so values drift gradually
        (random walk) and include a diurnal temperature cycle.  This
        lets us exercise the full pipeline (record → alert → dashboard)
        on a laptop with no hardware.
        """
        if self._mock is None:
            self._mock = MockManager()

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "temperature_c": round(self._mock.get("temperature_c"), 2),
                "humidity_pct": round(self._mock.get("humidity_pct"), 2),
                "pressure_hpa": round(self._mock.get("pressure_hpa"), 2),
                "gas_resistance_ohms": round(self._mock.get("gas_resistance_ohms"), 1),
                "iaq": round(self._mock.get("iaq"), 1),
            },
            units={
                "temperature_c": "celsius",
                "humidity_pct": "percent",
                "pressure_hpa": "hPa",
                "gas_resistance_ohms": "ohms",
                "iaq": "index",
            },
            metadata={
                "mock": True,
                "iaq_source": "mock_manager",
            },
        )

    # ── IAQ derivation ──────────────────────────────────────────────────

    @classmethod
    def _calculate_iaq(cls, gas_resistance_ohms: float) -> float:
        """Convert raw gas resistance to a 0-500 air quality index.

        Mapping (linear, inverted):
            gas ≈ 50 kΩ  →  IAQ ≈ 0   (very clean air)
            gas ≈  1 kΩ  →  IAQ ≈ 500 (very polluted air)

        WHY linear instead of Bosch's proprietary algorithm:
            The official BME680 software uses a closed-source compensation
            binary (BSEC) that requires a per-device license and NDA.
            For an open weather station we prefer a transparent mapping.
            The linear approximation is less accurate in absolute terms
            but preserves the *trend* — which is what matters for
            "is air quality getting better or worse?" alerts.

        NOTE: Gas resistance varies with temperature and humidity, so
        the IAQ is most meaningful as a *relative* indicator over time,
        not as an absolute "this room has 73.2 IAQ" reading.
        """
        gas = max(cls._IAQ_GAS_MIN, min(cls._IAQ_GAS_MAX, gas_resistance_ohms))

        # Inverted linear interpolation: high gas → low IAQ.
        # Fraction of the range from clean (50k) to polluted (1k).
        fraction = (cls._IAQ_GAS_MAX - gas) / (cls._IAQ_GAS_MAX - cls._IAQ_GAS_MIN)
        iaq = cls._IAQ_SCALE_MIN + fraction * (cls._IAQ_SCALE_MAX - cls._IAQ_SCALE_MIN)

        return max(cls._IAQ_SCALE_MIN, min(cls._IAQ_SCALE_MAX, iaq))