"""
VEML7700 ambient light sensor driver.

The Vishay VEML7700 is a digital ambient light sensor with I²C interface
that reports both ambient light (lux) and "white" channel lux.  It has a
wide dynamic range (0.0036 to 120,000 lux) thanks to built-in gain and
integration-time auto-ranging, making it suitable from starlight to
direct sunlight.

WHY this matters for weather:
  Ambient light level is a direct proxy for cloud cover and solar
  irradiance.  Tracking lux over the day lets us:
    - Detect clear vs. overcast conditions (clear noon ≈ 100,000 lux;
      overcast noon ≈ 10,000–20,000 lux).
    - Estimate sunrise/sunset times for the station location.
    - Compute a rough UV index (see below) for sun-safety alerts.
  The "white" channel gives the broad-spectrum visible component,
  useful for distinguishing full-spectrum daylight from artificial light.

UV approximation:
  The VEML7700 does not measure UV directly — it measures visible light
  (the sensor peak sensitivity is ~570 nm, well into visible).  However,
  UV correlates strongly with visible irradiance during daytime, so we
  derive a rough UV index: ``uv = lux / 250``, capped at 11 (the max on
  the WHO UV index scale).  This is a *rough* approximation suitable for
  trend monitoring, not for regulatory UV reporting.

NOTE: adafruit_veml7700 depends on adafruit_bus_device and Blinka, both
      of which require a real I²C bus.  The import is guarded so mock
      mode works on any machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware library ────────────────────────────────────────────
# adafruit_veml7700 wraps the I²C register reads and auto-ranging logic.
# It transitively pulls in adafruit_bus_device and Blinka (Pi GPIO shim).
try:
    import board  # type: ignore[import-not-found]
    import busio  # type: ignore[import-not-found]
    import adafruit_veml7700  # type: ignore[import-not-found]
    _HAS_VEML7700 = True
except ImportError:
    _HAS_VEML7700 = False
    logger.debug("adafruit_veml7700 not available — VEML7700 will require mock mode")


# ── Constants ────────────────────────────────────────────────────────────
_VEML7700_DEFAULT_ADDR = 0x10    # VEML7700 default I²C address (ADDR pin low)
_UV_INDEX_MAX = 11.0            # WHO UV index scale maximum
_UV_LUX_DIVISOR = 250.0          # rough lux→UV conversion factor
# NOTE: 250 lux ≈ 1 UV index unit is a heuristic for clear-sky daylight.
#       It overestimates UV under artificial light and underestimates
#       near dusk/dawn, but is adequate for trend tracking.


@dataclass(frozen=True)
class VEML7700Config:
    """Immutable configuration for the VEML7700 driver."""

    i2c_address: int = _VEML7700_DEFAULT_ADDR


class VEML7700Sensor(SensorBase):
    """Driver for the VEML7700 ambient light sensor.

    Metrics produced:
      light_lux  — ambient light level (lux)
      white_lux  — white channel light level (lux)
      uv_index   — approximate UV index (derived from lux, 0–11)

    The uv_index is computed, not measured — see module docstring.
    """

    name = "veml7700"
    bus_type = "i2c"
    description = "VEML7700 ambient light sensor (lux, white, approx UV index)"
    metrics = [
        "light_lux",
        "white_lux",
        "uv_index",
    ]

    def __init__(
        self,
        i2c_address: int = _VEML7700_DEFAULT_ADDR,
        mock_mode: bool = False,
    ) -> None:
        self._config = VEML7700Config(i2c_address=i2c_address)
        self._i2c = None           # busio.I2C bus handle
        self._sensor = None        # adafruit_veml7700.VEML7700 object
        self._mock: MockManager | None = None
        super().__init__(mock_mode=mock_mode)

    # ── Hardware lifecycle ──────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Set up the I²C bus and VEML7700 sensor object.

        Return False if the library is missing or the sensor is not
        responding at the configured address.
        """
        if not _HAS_VEML7700:
            logger.error(
                "[veml7700] adafruit_veml7700 not installed — cannot use hardware mode"
            )
            return False

        try:
            # board.SCL / board.SDA are the Pi's I²C bus 1 pins.
            self._i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_veml7700.VEML7700(
                self._i2c,
                address=self._config.i2c_address,
            )
            logger.info(
                "[veml7700] initialized at I²C address 0x%02x",
                self._config.i2c_address,
            )
            return True
        except ValueError as e:
            # adafruit_veml7700 raises ValueError if no device ACKs at
            # the given address — the sensor is not wired or wrong addr.
            logger.error("[veml7700] sensor not found at 0x%02x: %s",
                         self._config.i2c_address, e)
            self._sensor = None
            self._i2c = None
            return False
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[veml7700] unexpected init error: %s", e)
            self._sensor = None
            self._i2c = None
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Read ambient and white lux from the VEML7700, derive UV index."""
        if self._sensor is None:
            logger.warning("[veml7700] no sensor object — call initialize() first")
            return None

        try:
            # The Adafruit library handles gain/integration-time auto-
            # ranging internally, so .light and .white just return lux.
            light_lux = float(self._sensor.light)
            white_lux = float(self._sensor.white)

            # ── Derive approximate UV index ────────────────────────────
            # See module docstring: rough heuristic, capped at WHO max.
            uv_index = min(light_lux / _UV_LUX_DIVISOR, _UV_INDEX_MAX)
            # UV can't be negative; clamp at 0 (nighttime).
            uv_index = max(0.0, uv_index)

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "light_lux": light_lux,
                    "white_lux": white_lux,
                    "uv_index": uv_index,
                },
                units={
                    "light_lux": "lux",
                    "white_lux": "lux",
                    "uv_index": "index",
                },
                metadata={
                    "i2c_address": self._config.i2c_address,
                    "uv_approx": True,  # flag that UV is derived, not measured
                },
            )
        except OSError as e:
            # I²C read failure — bus error, sensor disconnected, etc.
            logger.error("[veml7700] I²C read error: %s", e)
            return None
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[veml7700] unexpected read error: %s", e)
            return None

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock light data via MockManager.

        MockManager already simulates a diurnal light cycle (0 lux at
        night, peaking at ~5000 lux at noon) for the ``light_lux``
        metric, and a corresponding ``uv_index`` cycle.  We derive white
        lux as a fraction of ambient (white ≈ 0.8 × ambient, a rough
        ratio for daylight conditions).
        """
        if self._mock is None:
            self._mock = MockManager()
        m = self._mock
        light_lux = m.get("light_lux")
        white_lux = light_lux * 0.8
        # Use MockManager's dedicated uv_index baseline (diurnal cycle)
        uv_index = m.get("uv_index")

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "light_lux": light_lux,
                "white_lux": white_lux,
                "uv_index": uv_index,
            },
            units={
                "light_lux": "lux",
                "white_lux": "lux",
                "uv_index": "index",
            },
            metadata={"mock": True, "uv_approx": True},
        )