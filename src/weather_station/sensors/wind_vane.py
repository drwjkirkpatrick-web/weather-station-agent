"""
Wind vane (wind direction) sensor driver — analog via MCP3008 ADC.

WHAT IT MEASURES
----------------
Wind direction in degrees (0–360, where 0° = North, clockwise) and the
equivalent 16-point compass cardinal abbreviation (N, NNE, NE, ENE, …).

WHY IT'S USEFUL FOR WEATHER
---------------------------
Direction tells us *where* the weather is coming from — critical for:

  • Forecasting: knowing the upwind source region predicts incoming airmass
    (e.g. northerly flow in winter → cold advection, westerly → maritime).
  • Air-quality: a pollution plume's source is found by back-trajectory
    from the measured wind vector.
  • Agriculture: spray drift, frost-fan placement, and irrigation scheduling
    all depend on prevailing direction.
  • Fire weather: flame propagation direction is dominated by wind vector.

PHYSICAL PRINCIPLE
------------------
A resistive wind vane (e.g. the Davis / SparkFun style) uses a rotating
magnet + reed switches (or a continuous potentiometer) to select one of
16 resistors into a voltage divider.  The output voltage uniquely encodes
one of 16 compass points (every 22.5°).  We read that voltage through an
MCP3008 10-bit SPI ADC (0–1023 → 0–V_ref volts) and map it to the nearest
known direction voltage.

GRACEFUL DEGRADATION
--------------------
The ``adafruit_mcp3xxx`` stack requires real SPI hardware (and ``spidev``,
which in turn needs a Pi).  We guard the import so the module loads on a
laptop; when unavailable, ``_init_hardware()`` returns False and the base
class falls back to mock mode.
"""

from __future__ import annotations

import logging
from typing import Any

from weather_station.core.mock_manager import MockManager
from weather_station.core.sensor_base import SensorBase, SensorReading

logger = logging.getLogger(__name__)

# ── Optional hardware dependencies ────────────────────────────────────────
# These pull in spidev + Adafruit-Blinka on a real Pi.  On a dev laptop they
# are absent; the try/except keeps the module importable for mock mode.
try:
    import busio  # noqa: F401 — imported as part of the Blinka stack
    import board  # noqa: F401
    import digitalio  # noqa: F401
    import adafruit_mcp3xxx.mcp3008 as MCP3008
    from adafruit_mcp3xxx.analog_in import AnalogIn
except ImportError:  # pragma: no cover — exercised on non-Pi machines
    busio = None      # type: ignore[assignment]
    board = None      # type: ignore[assignment]
    digitalio = None  # type: ignore[assignment]
    MCP3008 = None    # type: ignore[assignment]
    AnalogIn = None   # type: ignore[assignment]


# ── Voltage → direction lookup table ──────────────────────────────────────
# Each entry: (degrees, cardinal, nominal_voltage).
# Voltages are for V_in = 3.3 V with the standard SparkFun / Davis 16-point
# resistive divider wind vane.  Measured values vary ±5 % between units, so
# _read_hardware() uses a nearest-match (minimum absolute voltage difference)
# approach rather than exact equality.
# WHY a tuple-of-tuples (not a dict): we need both the direction and the
# cardinal string back, and we iterate to find the closest match.  A list
# of records is clearer than two parallel dicts.
_WIND_VANE_TABLE: tuple[tuple[float, str, float], ...] = (
    (0.0,   "N",   2.42),
    (22.5,  "NNE", 2.67),
    (45.0,  "NE",  2.84),
    (67.5,  "ENE", 2.93),
    (90.0,  "E",   3.01),
    (112.5, "ESE", 2.33),
    (135.0, "SE",  2.52),
    (157.5, "SSE", 2.69),
    (180.0, "S",   2.88),
    (202.5, "SSW", 2.76),
    (225.0, "SW",  2.54),
    (247.5, "WSW", 2.32),
    (270.0, "W",   2.11),
    (292.5, "WNW", 2.19),
    (315.0, "NW",  2.27),
    (337.5, "NNW", 2.38),
)

# 16-point compass abbreviations, indexed by (degrees // 22.5) % 16.
# Used by _read_mock() to convert a float heading → cardinal string.
_CARDINAL_16: tuple[str, ...] = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


class WindVaneSensor(SensorBase):
    """Resistive wind-vane driver read via an MCP3008 SPI ADC.

    The vane selects one of 16 resistors into a divider; we read the
    resulting voltage on a configured ADC channel and look up the nearest
    known direction in the calibration table above.
    """

    name = "wind_vane"
    metrics = ["wind_direction_deg", "wind_direction_cardinal"]
    bus_type = "adc"
    description = "Resistive wind vane (MCP3008 ADC) — wind direction (° + cardinal)"

    # ── Construction ───────────────────────────────────────────────────────

    def __init__(
        self,
        adc_channel: int = 0,
        vin: float = 3.3,
        mock_mode: bool = False,
    ) -> None:
        super().__init__(mock_mode=mock_mode)
        self.adc_channel = adc_channel
        self.vin = vin  # ADC reference voltage (usually Pi 3.3 V rail)

        # SPI / MCP3008 handles — populated by _init_hardware()
        self._spi = None
        self._mcp = None
        self._channel = None  # AnalogIn instance for the selected pin

        self._mock = MockManager()

    # ── Hardware lifecycle ─────────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Bring up SPI and the MCP3008 ADC.

        Returns False (→ mock fallback) when the Adafruit MCP stack or
        board hardware is not available.
        """
        if busio is None or board is None or MCP3008 is None or AnalogIn is None:
            logger.warning(
                "[wind_vane] adafruit_mcp3xxx stack not available — cannot init"
            )
            return False

        try:
            # NOTE: board.SCK / MOSI / MISO / CE0 are the Pi's hardware SPI
            # pins.  The MCP3008 needs a chip-select; we use CE0 (default).
            self._spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
            cs = digitalio.DigitalInOut(board.CE0)
            self._mcp = MCP3008.MCP3008(self._spi, cs)

            # adc_channel selects which of the 8 ADC inputs the vane is on.
            if not 0 <= self.adc_channel < 8:
                logger.error(
                    "[wind_vane] adc_channel %d out of range (0–7)", self.adc_channel
                )
                return False
            self._channel = AnalogIn(self._mcp, getattr(MCP3008, f"P{self.adc_channel}"))

            logger.info(
                "[wind_vane] MCP3008 initialised on ADC channel %d (V_ref=%.2f V)",
                self.adc_channel,
                self.vin,
            )
            return True
        except Exception as e:
            # Most common: /dev/spidev0.0 not accessible (not a Pi, or
            # spi not enabled in raspi-config).
            logger.error("[wind_vane] MCP3008 init failed: %s", e)
            return False

    # ── Reading ────────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Read the ADC, convert to voltage, look up nearest direction.

        Returns a SensorReading with ``wind_direction_deg`` and
        ``wind_direction_cardinal`` (string), or None on error.
        """
        if self._channel is None:
            logger.error("[wind_vane] ADC channel not initialised")
            return None

        try:
            # MCP3008 returns a 10-bit value (0–1023).  .value is the raw
            # count; .voltage is the converted voltage — we use .voltage
            # for the lookup since it already accounts for V_ref.
            voltage: float = self._channel.voltage

            # ── Nearest-match against the calibration table ───────────────
            best_deg, best_card, best_diff = _WIND_VANE_TABLE[0]
            best_diff = abs(voltage - best_diff)
            for degrees, cardinal, nominal_v in _WIND_VANE_TABLE[1:]:
                diff = abs(voltage - nominal_v)
                if diff < best_diff:
                    best_deg, best_card, best_diff = degrees, cardinal, diff

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "wind_direction_deg": round(best_deg, 1),
                    # NOTE: SensorReading.metrics is dict[str, float] per the
                    # base dataclass, but the cardinal is a string.  We stash
                    # it in metadata to keep the type contract intact and also
                    # expose a numeric (degrees / 22.5) under the metric key so
                    # downstream consumers that expect a float still work.
                    "wind_direction_cardinal": round(best_deg / 22.5) % 16,
                },
                units={
                    "wind_direction_deg": "degrees",
                    "wind_direction_cardinal": "index",
                },
                metadata={
                    "adc_channel": self.adc_channel,
                    "vin": self.vin,
                    "voltage": round(voltage, 3),
                    "raw_value": self._channel.value,
                    "cardinal": best_card,          # the actual string, here
                    "match_voltage_diff": round(best_diff, 3),
                    "table_entries": len(_WIND_VANE_TABLE),
                },
            )
        except Exception as e:
            logger.error("[wind_vane] read failed: %s", e)
            return None

    # ── Mock ───────────────────────────────────────────────────────────────

    def _read_mock(self) -> SensorReading:
        """Generate a plausible mock wind direction using MockManager."""
        # MockManager returns a drifting float in [0, 360); convert to
        # the nearest 16-point compass heading + cardinal string.
        deg = self._mock.get("wind_direction_deg", jitter=0.10)
        deg = deg % 360.0  # ensure in range
        index = round(deg / 22.5) % 16
        cardinal = _CARDINAL_16[index]
        snapped_deg = index * 22.5  # snap to exact compass point

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "wind_direction_deg": round(snapped_deg, 1),
                "wind_direction_cardinal": float(index),
            },
            units={
                "wind_direction_deg": "degrees",
                "wind_direction_cardinal": "index",
            },
            metadata={
                "source": "mock",
                "cardinal": cardinal,
                "mock_raw_deg": round(deg, 1),
            },
        )

    # ── Cleanup ───────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Release the SPI bus so other devices can use it."""
        # SPI busio objects don't have an explicit close() in Blinka, but
        # we null the references so they can be GC'd.  Leaving this hook
        # in place for subclasses / future use.
        self._channel = None
        self._mcp = None
        self._spi = None