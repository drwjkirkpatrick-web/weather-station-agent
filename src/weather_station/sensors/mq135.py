"""
MQ-135 air quality sensor driver (via MCP3008 ADC).

The MQ-135 is a low-cost metal-oxide (MOS) gas sensor sensitive to NH₃,
NOx, benzene, smoke, CO₂, and other harmful gases.  It outputs an analog
voltage proportional to gas concentration.  Because the Raspberry Pi
has no onboard ADC, we read the analog signal through an MCP3008 SPI ADC.

WHY this matters for weather:
  CO₂ concentration is a key indicator of air quality and ventilation.
  Outdoor CO₂ sits at ~415 ppm; indoor levels above 1000 ppm cause
  drowsiness and above 2000 ppm impair cognition.  For a weather station,
  tracking CO₂ trends helps identify temperature inversions (which trap
  pollutants near the ground) and wildfire smoke events (which elevate
  CO₂ and CO).  The air-quality index (0–500) maps to the EPA AQI scale:
  0–50 good, 51–100 moderate, 101–150 unhealthy for sensitive groups,
  151–200 unhealthy, 201–300 very unhealthy, 301+ hazardous.

CO₂ estimation:
  The MQ-135 is not a true NDIR CO₂ sensor — it responds to a broad mix
  of reducing gases.  We apply a *simplified* characteristic curve to
  convert its voltage output to an estimated CO₂ ppm:

      co2_ppm = (voltage / vin) * 1000 + 400

  Where:
    - voltage = ADC reading × (vin / 1023) for the 10-bit MCP3008
    - vin = supply voltage (3.3 V on the Pi)
    - 400 ppm = atmospheric CO₂ baseline (added so clean air reads ~415)

  This is a rough estimate.  For accurate CO₂, use an SCD30 or SCD41
  NDIR sensor.  The MQ-135 value is best used as a relative air-quality
  trend indicator, not an absolute measurement.

Air-quality index derivation:
  We map the CO₂ estimate to a 0–500 index roughly following the AQI
  scale for CO₂:
    - < 600 ppm → 0–50   (good)
    - 600–1000  → 51–100 (moderate)
    - 1000–1500 → 101–200 (unhealthy for sensitive)
    - 1500–2000 → 201–300 (unhealthy)
    - > 2000    → 301–500 (very unhealthy / hazardous)
  This is a simplified mapping for trend visualization.

NOTE: adafruit_mcp3xxx depends on adafruit_bus_device and Blinka, both
      of which require a real SPI bus.  The import is guarded so mock
      mode works on any machine.

NOTE: The MQ-135 requires a 24–48 hour preheat (burn-in) period for
      stable readings.  During burn-in, values will drift and should
      not be trusted.  This driver does not handle burn-in logic —
      callers should discard readings for the first 24 hours after
      power-on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware library ────────────────────────────────────────────
# adafruit_mcp3xxx provides the MCP3008 SPI ADC wrapper.  It transitively
# pulls in adafruit_bus_device and Blinka (Pi GPIO/SPI shim).
try:
    import board  # type: ignore[import-not-found]
    import busio  # type: ignore[import-not-found]
    import digitalio  # type: ignore[import-not-found]
    import adafruit_mcp3xxx.mcp3008 as MCP3008  # type: ignore[import-not-found]
    from adafruit_mcp3xxx.analog_in import AnalogIn  # type: ignore[import-not-found]
    _HAS_MCP3XXX = True
except ImportError:
    _HAS_MCP3XXX = False
    logger.debug("adafruit_mcp3xxx not available — MQ-135 will require mock mode")


# ── Constants ────────────────────────────────────────────────────────────
_MCP3008_RESOLUTION = 1023      # 10-bit ADC (0–1023)
_MCP3008_SPI_CLOCK = 100000      # 100 kHz SPI clock (MCP3008 supports up to ~3.5 MHz)
_MCP3008_CS_PIN = "D5"           # GPIO5 (board.D5) as chip-select

# CO₂ estimation constants
_CO2_BASELINE_PPM = 400.0       # atmospheric CO₂ baseline
_CO2_SCALE_FACTOR = 1000.0      # voltage-ratio → ppm scaling


@dataclass(frozen=True)
class MQ135Config:
    """Immutable configuration for the MQ-135 driver.

    WHY frozen: prevents accidental reconfiguration of ADC parameters
    mid-stream, which would produce inconsistent readings.
    """

    adc_channel: int = 1         # MCP3008 channel the MQ-135 is wired to
    vin: float = 3.3              # supply / reference voltage (Pi 3.3 V rail)


class MQ135Sensor(SensorBase):
    """Driver for the MQ-135 air quality sensor via MCP3008 ADC.

    Metrics produced:
      co2_ppm     — estimated CO₂ concentration (ppm)
      air_quality — air quality index (0–500, EPA AQI-style scale)

    Both values are estimates derived from the MQ-135's analog voltage.
    See module docstring for the conversion formulas and caveats.
    """

    name = "mq135"
    bus_type = "adc"
    description = "MQ-135 air quality sensor (estimated CO₂ ppm + AQI via MCP3008 ADC)"
    metrics = [
        "co2_ppm",
        "air_quality",
    ]

    def __init__(
        self,
        adc_channel: int = 1,
        vin: float = 3.3,
        mock_mode: bool = False,
    ) -> None:
        self._config = MQ135Config(adc_channel=adc_channel, vin=vin)
        self._spi = None           # busio.SPI bus handle
        self._cs = None             # digitalio.DigitalInOut chip-select
        self._mcp = None            # MCP3008 ADC object
        self._adc_channel = None    # AnalogIn channel object
        self._mock: MockManager | None = None
        super().__init__(mock_mode=mock_mode)

    # ── Hardware lifecycle ──────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Set up SPI bus, chip-select, and MCP3008 ADC.

        Return False if the library is missing or SPI is unavailable.
        """
        if not _HAS_MCP3XXX:
            logger.error(
                "[mq135] adafruit_mcp3xxx not installed — cannot use hardware mode"
            )
            return False

        try:
            # ── SPI bus: board.SCK, MISO, MOSI are the Pi's SPI0 pins ──
            self._spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)

            # ── Chip-select on GPIO5 (board.D5) ────────────────────────
            # Any free GPIO works; D5 is the convention for this project.
            self._cs = digitalio.DigitalInOut(getattr(board, _MCP3008_CS_PIN))

            # ── Create MCP3008 ADC object ─────────────────────────────
            self._mcp = MCP3008.MCP3008(
                self._spi,
                self._cs,
            )

            # ── Create AnalogIn for the configured channel ────────────
            # MCP3008 has 8 channels (0–7).  We read the one the MQ-135
            # is wired to.
            self._adc_channel = AnalogIn(self._mcp, self._config.adc_channel)

            logger.info(
                "[mq135] MCP3008 initialized on SPI, ADC channel %d, vin=%.2f V",
                self._config.adc_channel,
                self._config.vin,
            )
            return True
        except Exception as e:
            # SPI init can fail if the bus is not enabled (dtoverlay=spi0-1cs
            # missing from /boot/config.txt) or if the CS pin is in use.
            logger.error("[mq135] hardware init failed: %s", e)
            self._cleanup()
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Read the MQ-135 analog voltage via MCP3008 and estimate CO₂.

        Steps:
          1. Read raw ADC value (0–1023) and voltage from the channel.
          2. Estimate CO₂ ppm using the simplified characteristic curve.
          3. Map CO₂ ppm to a 0–500 air-quality index.
        """
        if self._adc_channel is None:
            logger.warning("[mq135] no ADC channel — call initialize() first")
            return None

        try:
            # ── Read analog value ──────────────────────────────────────
            # AnalogIn provides .value (raw 0–1023) and .voltage (float V).
            # We use .voltage directly as the Adafruit library handles the
            # vin → voltage conversion based on the MCP3008 VREF.
            raw_value = self._adc_channel.value
            voltage = float(self._adc_channel.voltage)

            # ── Estimate CO₂ ppm ──────────────────────────────────────
            # Simplified MQ-135 characteristic curve:
            #   co2_ppm = (voltage / vin) * 1000 + 400
            # See module docstring for rationale and caveats.
            voltage_ratio = voltage / self._config.vin if self._config.vin > 0 else 0.0
            co2_ppm = (voltage_ratio * _CO2_SCALE_FACTOR) + _CO2_BASELINE_PPM

            # ── Derive air-quality index (0–500) ───────────────────────
            air_quality = self._co2_to_aqi(co2_ppm)

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "co2_ppm": co2_ppm,
                    "air_quality": air_quality,
                },
                units={
                    "co2_ppm": "ppm",
                    "air_quality": "index",
                },
                metadata={
                    "raw_adc": raw_value,
                    "voltage": voltage,
                    "vin": self._config.vin,
                    "adc_channel": self._config.adc_channel,
                    "estimated": True,  # flag that CO₂ is an estimate
                },
            )
        except OSError as e:
            # SPI read failure — bus error or ADC disconnect.
            logger.error("[mq135] SPI read error: %s", e)
            return None
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[mq135] unexpected read error: %s", e)
            return None

    def _co2_to_aqi(self, co2_ppm: float) -> float:
        """Map estimated CO₂ ppm to a 0–500 air-quality index.

        This is a simplified mapping for trend visualization, loosely
        following the EPA AQI scale concept:
            < 600 ppm   → 0–50   (good)
            600–1000    → 51–100 (moderate)
            1000–1500   → 101–200 (unhealthy for sensitive groups)
            1500–2000   → 201–300 (unhealthy)
            > 2000      → 301–500 (very unhealthy / hazardous)
        """
        if co2_ppm <= 600:
            # Linear 0–50 for 400–600 ppm
            return max(0.0, (co2_ppm - 400) / 200 * 50)
        elif co2_ppm <= 1000:
            # Linear 51–100 for 600–1000 ppm
            return 51 + (co2_ppm - 600) / 400 * 49
        elif co2_ppm <= 1500:
            # Linear 101–200 for 1000–1500 ppm
            return 101 + (co2_ppm - 1000) / 500 * 99
        elif co2_ppm <= 2000:
            # Linear 201–300 for 1500–2000 ppm
            return 201 + (co2_ppm - 1500) / 500 * 99
        else:
            # Linear 301–500 for 2000+ ppm, capped at 500
            return min(500.0, 301 + (co2_ppm - 2000) / 1000 * 199)

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock air-quality data via MockManager.

        MockManager provides a random-walk ``co2_ppm`` (baseline 420,
        clamped 350–5000) and ``air_quality`` (baseline 50, clamped
        0–500) with appropriate jitter.
        """
        if self._mock is None:
            self._mock = MockManager()
        m = self._mock
        co2_ppm = m.get("co2_ppm")
        air_quality = m.get("air_quality")

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "co2_ppm": co2_ppm,
                "air_quality": air_quality,
            },
            units={
                "co2_ppm": "ppm",
                "air_quality": "index",
            },
            metadata={
                "mock": True,
                "estimated": True,
            },
        )

    # ── Cleanup ─────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        """Release SPI and GPIO resources on error / teardown."""
        self._adc_channel = None
        self._mcp = None
        if self._cs is not None:
            try:
                self._cs.deinit()
            except Exception:  # pragma: no cover — best-effort
                pass
            self._cs = None
        if self._spi is not None:
            try:
                self._spi.deinit()
            except Exception:  # pragma: no cover — best-effort
                pass
            self._spi = None

    def __del__(self) -> None:  # noqa: B003 — intentional cleanup
        """Release SPI/GPIO resources when the driver is GC'd."""
        self._cleanup()