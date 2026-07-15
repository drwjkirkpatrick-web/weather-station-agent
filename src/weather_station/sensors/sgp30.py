"""
SGP30 sensor driver — CO₂-equivalent and TVOC air-quality monitoring.

The SGP30 is a digital metal-oxide gas sensor from Sensirion that
measures two derived air-quality signals:
  - ``co2_eq_ppm`` — CO₂-equivalent concentration in ppm (400-6000 range)
  - ``tvoc_ppb``   — Total Volatile Organic Compounds in ppb (0-60000 range)

These are *derived* values, not raw gas concentrations.  The SGP30
has an on-chip algorithm that correlates the hot-plate response to
typical indoor air contaminants and outputs CO₂-eq / TVOC equivalents
that are meaningful for air-quality alerts.

WHY this sensor matters for weather:
  - TVOC is a sensitive indicator of wildfire smoke.  When smoke rolls
    in, TVOC can spike to thousands of ppb even before PM2.5 rises,
    giving an early-warning signal.
  - CO₂-equivalent trends correlate with ventilation quality and
    occupancy, useful for indoor-air-quality monitoring.
  - Outdoors, elevated TVOC can indicate nearby industrial emissions
    or agricultural activity.

WHY SGP30 alongside BME680 (both have gas sensors):
  The BME680 gives a *relative* IAQ trend from raw gas resistance.
  The SGP30 gives *calibrated* CO₂-eq and TVOC values.  Together they
  provide cross-validation: if SGP30 TVOC spikes but BME680 IAQ stays
  flat, one sensor may need recalibration or replacement.

NOTE: The SGP30 requires a baseline calibration step at power-on
(``iaq_init()``).  After init, the on-chip algorithm needs ~15 seconds
to produce valid readings and ~12 hours to fully adapt to ambient
conditions (the "baseline" feature).  For a weather station polling
every 60 s, the first few readings after boot may read 400 ppm / 0 ppb
until the algorithm warms up.

Hardware:
  - I2C address 0x58 (fixed — no alternate address available).
  - 3.3 V operation — direct Pi Zero 2 W compatibility.
  - Library: ``adafruit_sgp30`` (install via ``pip install adafruit-circuitpython-sgp30``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware libraries ─────────────────────────────────────────
# WHY try/except at module level: the CircuitPython packages are only
# installed on the Pi.  We guard the import so the module loads cleanly
# on any machine and mock mode works in dev.
try:
    import board  # noqa: F401
    import busio  # noqa: F401
    import adafruit_sgp30
    _SGP30_AVAILABLE = True
except ImportError:
    _SGP30_AVAILABLE = False


class SGP30Sensor(SensorBase):
    """Driver for the Sensirion SGP30 air-quality sensor over I2C.

    Measures two metrics:
      - ``co2_eq_ppm`` — CO₂-equivalent concentration (400-6000 ppm)
      - ``tvoc_ppb``   — Total VOC concentration (0-60000 ppb)

    The SGP30 has an internal baseline algorithm that must be
    initialised once at startup via ``iaq_init()``.  This is handled in
    ``_init_hardware()``.  After init the algorithm needs a brief
    warm-up period; we track the init timestamp so the metadata can
    report how long the sensor has been running.
    """

    # ── Class-level metadata (read by the orchestrator) ─────────────────
    name: str = "sgp30"
    metrics: list[str] = [
        "co2_eq_ppm",
        "tvoc_ppb",
    ]
    bus_type: str = "i2c"
    description: str = "Sensirion SGP30 — CO₂-eq and TVOC air quality"

    def __init__(
        self,
        i2c_address: int = 0x58,
        mock_mode: bool = False,
    ) -> None:
        """Create an SGP30 sensor driver.

        Args:
            i2c_address: I2C bus address.  The SGP30 has a fixed
                address of 0x58 — no alternate is available.  The
                parameter exists for symmetry with other drivers and
                in case a future breakout uses an I2C multiplexer with
                a remapped address.
            mock_mode: If True, never touch hardware — return mock data.
        """
        super().__init__(mock_mode=mock_mode)

        self._i2c_address: int = i2c_address

        # Hardware handles — populated by _init_hardware().
        self._sensor: Any = None
        self._i2c: Any = None

        # Timestamp of iaq_init() call — used to track warm-up status.
        self._init_time: float = 0.0

        # Lazy-instantiated mock generator.
        self._mock: MockManager | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Open the I2C bus, create the SGP30 object, and run iaq_init().

        Returns True on success, False if the library is missing or the
        sensor does not respond.

        NOTE: ``iaq_init()`` is the critical step — it starts the
        on-chip baseline algorithm.  Without it, reads will fail or
        return 0.  This method also records the init timestamp so the
        orchestrator knows whether the sensor has finished its ~15 s
        warm-up.
        """
        if not _SGP30_AVAILABLE:
            logger.error(
                "[sgp30] adafruit_sgp30 / board / busio not installed — "
                "install with: pip install adafruit-circuitpython-sgp30"
            )
            return False

        try:
            self._i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_sgp30.Adafruit_SGP30(
                self._i2c, address=self._i2c_address
            )

            # ── Baseline calibration ────────────────────────────────────
            # iaq_init() tells the SGP30 to start its internal air-quality
            # algorithm.  After this call, the sensor needs:
            #   - ~1 s before the first valid baseline read
            #   - ~15 s before co2_eq / tvoc are non-zero
            #   - ~12 h to fully adapt to ambient conditions
            # WHY we call it here (not in _read_hardware): it should run
            # exactly once at startup.  Calling it on every read would
            # reset the baseline and corrupt the algorithm's adaptation.
            self._sensor.iaq_init()
            self._init_time = time.time()
            logger.info(
                "[sgp30] I2C sensor initialised at address 0x%02X, iaq_init() done",
                self._i2c_address,
            )
            return True
        except Exception as e:
            logger.error("[sgp30] hardware init failed: %s", e)
            self._sensor = None
            self._i2c = None
            self._init_time = 0.0
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Take a single reading from the physical SGP30.

        Returns a SensorReading with CO₂-eq and TVOC, or None on error.

        NOTE: If the sensor was just initialised (within ~15 s), the
        on-chip algorithm may still return baseline values (400 ppm
        CO₂-eq, 0 ppb TVOC).  We include the elapsed-since-init time in
        metadata so the consumer can decide whether to trust early
        readings.
        """
        if self._sensor is None:
            logger.warning("[sgp30] _read_hardware called but sensor is None")
            return None

        try:
            co2_eq = self._sensor.co2eq
            tvoc = self._sensor.tvoc

            # Elapsed time since iaq_init — useful for warm-up awareness.
            elapsed_since_init = (
                time.time() - self._init_time if self._init_time > 0 else 0.0
            )

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "co2_eq_ppm": round(co2_eq, 1),
                    "tvoc_ppb": round(tvoc, 1),
                },
                units={
                    "co2_eq_ppm": "ppm",
                    "tvoc_ppb": "ppb",
                },
                metadata={
                    "i2c_address": f"0x{self._i2c_address:02X}",
                    "seconds_since_init": round(elapsed_since_init, 1),
                    "warmup_complete": elapsed_since_init >= 15.0,
                },
            )
        except Exception as e:
            logger.error("[sgp30] hardware read failed: %s", e)
            return None

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock readings using MockManager."""
        if self._mock is None:
            self._mock = MockManager()

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "co2_eq_ppm": round(self._mock.get("co2_eq_ppm"), 1),
                "tvoc_ppb": round(self._mock.get("tvoc_ppb"), 1),
            },
            units={
                "co2_eq_ppm": "ppm",
                "tvoc_ppb": "ppb",
            },
            metadata={
                "mock": True,
            },
        )