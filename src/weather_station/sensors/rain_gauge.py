"""
Rain gauge (tipping-bucket) sensor driver — GPIO pulse counting.

WHAT IT MEASURES
----------------
  • ``rain_mm`` — accumulated rainfall since the last ``read()`` call, in
    millimetres (mm).  1 mm of rain = 1 litre per square metre.
  • ``rain_rate_mmh`` — instantaneous rainfall rate in mm/hour, computed
    from the number of tips since the last read and the elapsed time.

WHY IT'S USEFUL FOR WEATHER
---------------------------
Rainfall is the single most actionable surface-weather parameter:

  • Irrigation scheduling — only water the garden if recent rain was
    insufficient.
  • Flood / flash-flood early warning — sustained high rain_rate_mmh is
    the leading indicator of urban / creek flooding.
  • Agricultural decision support — cumulative rain_mm over 24 h drives
    spray/fertiliser timing (rain washes it off).
  • Hydrology — tipping-bucket data feeds runoff models and reservoir
    inflow forecasts.

PHYSICAL PRINCIPLE
------------------
A tipping-bucket rain gauge has a seesaw bucket calibrated to tip after
collecting a fixed volume of water (``bucket_ml``, typically 0.2794 ml for
the common SparkFun / Davis style).  Each tip momentarily closes a reed
switch, pulling a GPIO pin low.  We count falling-edge interrupts.

    rain_mm = tips × bucket_ml ÷ collector_area_cm²

The standard collector funnel has a 100 cm² opening (≈11.3 cm diameter),
so rain_mm = tips × bucket_ml / 100.  The rate is:

    rain_rate_mmh = rain_mm ÷ elapsed_hours

We track total tips *since the last read* and reset the counter on each
read, so successive reads give per-interval accumulation — the natural
unit for logging and alerting.

GRACEFUL DEGRADATION
--------------------
``RPi.GPIO`` only importable on real Pi hardware; the try/except keeps
the module loadable anywhere, and ``_init_hardware()`` returns False when
the library is absent, triggering the base-class mock fallback.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

from weather_station.core.mock_manager import MockManager
from weather_station.core.sensor_base import SensorBase, SensorReading

logger = logging.getLogger(__name__)

# ── Optional hardware dependency ──────────────────────────────────────────
try:
    import RPi.GPIO as GPIO
except ImportError:  # pragma: no cover — exercised on non-Pi machines
    GPIO = None  # type: ignore[assignment]


class RainGaugeSensor(SensorBase):
    """Tipping-bucket rain gauge driver using a GPIO-interrupt tip counter.

    Each bucket tip closes a reed switch → GPIO falling edge.  We count
    tips between reads, convert to millimetres, and derive the rain rate
    from the elapsed time.
    """

    name = "rain_gauge"
    metrics = ["rain_mm", "rain_rate_mmh"]
    bus_type = "gpio"
    description = "Tipping-bucket rain gauge (GPIO pulse counting) — rainfall + rate"

    # Standard collector funnel opening area for the SparkFun / Davis style.
    # 100 cm² → rain_mm = tips × bucket_ml / 100.
    COLLECTOR_AREA_CM2: float = 100.0

    # ── Construction ───────────────────────────────────────────────────────

    def __init__(
        self,
        pin: int = 17,
        bucket_ml: float = 0.2794,
        mock_mode: bool = False,
    ) -> None:
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self.bucket_ml = bucket_ml

        # Tip counter — incremented by the GPIO ISR, atomically read+reset
        # inside _read_hardware().  Lock is mandatory: the ISR runs on the
        # BCM driver thread, not the caller's thread.
        self._tip_count: int = 0
        self._count_lock = threading.Lock()

        # Last read timestamp (monotonic) — used to compute elapsed time
        # for the rain-rate calculation.  Initialised to now so the first
        # read has a sensible (non-zero) elapsed window.
        self._last_read_time: float = time.monotonic()

        self._mock = MockManager()

    # ── Hardware lifecycle ─────────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Set up the GPIO pin with a pull-up and a falling-edge interrupt.

        Returns False (→ mock fallback) when RPi.GPIO is unavailable.
        """
        if GPIO is None:
            logger.warning(
                "[rain_gauge] RPi.GPIO not available — cannot init hardware"
            )
            return False

        try:
            # BCM numbering.  Pin 17 is a common choice — no I2C/SPI conflict.
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            # bouncetime=50 ms — slightly longer than the anemometer because
            # the reed switch in a tipping bucket bounces more (it's a dry
            # contact, not a hall sensor).  50 ms → max 20 tips/s; the real
            # gauge tops out near ~1 tip/s in torrential rain.
            GPIO.add_event_detect(
                self.pin,
                GPIO.FALLING,
                callback=self._interrupt_callback,
                bouncetime=50,
            )
            logger.info(
                "[rain_gauge] GPIO pin %d armed for tipping-bucket tips",
                self.pin,
            )
            return True
        except Exception as e:
            logger.error("[rain_gauge] GPIO setup failed: %s", e)
            return False

    # ── Interrupt handling ─────────────────────────────────────────────────

    def _interrupt_callback(self, channel: int) -> None:
        """GPIO falling-edge ISR — runs in the BCM thread; keep it tiny."""
        with self._count_lock:
            self._tip_count += 1

    # ── Reading ────────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Atomically read+reset the tip counter, then compute rain_mm and
        rain_rate_mmh.  Returns None on a hardware read error.
        """
        if GPIO is None:
            logger.error("[rain_gauge] RPi.GPIO missing during read")
            return None

        try:
            now = time.monotonic()

            # ── Atomic read + reset ───────────────────────────────────────
            # Both the count and the timestamp must be captured together so
            # the rate calculation is internally consistent.  We reset the
            # counter to 0 immediately so no tips are lost between this read
            # and the next.
            with self._count_lock:
                tips = self._tip_count
                self._tip_count = 0
                prev_time = self._last_read_time
                self._last_read_time = now

            elapsed_s = max(now - prev_time, 0.001)  # avoid div-by-zero
            elapsed_h = elapsed_s / 3600.0

            # ── Tips → millimetres ────────────────────────────────────────
            rain_mm = tips * self.bucket_ml / self.COLLECTOR_AREA_CM2
            rain_mm = max(0.0, rain_mm)

            # ── Rate ──────────────────────────────────────────────────────
            # mm / hour.  When there were no tips, rate is zero regardless
            # of elapsed time.
            if tips > 0 and elapsed_h > 0:
                rain_rate_mmh = rain_mm / elapsed_h
            else:
                rain_rate_mmh = 0.0

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "rain_mm": round(rain_mm, 3),
                    "rain_rate_mmh": round(rain_rate_mmh, 2),
                },
                units={
                    "rain_mm": "mm",
                    "rain_rate_mmh": "mm/h",
                },
                metadata={
                    "pin": self.pin,
                    "bucket_ml": self.bucket_ml,
                    "collector_area_cm2": self.COLLECTOR_AREA_CM2,
                    "tips_since_last_read": tips,
                    "elapsed_s": round(elapsed_s, 3),
                },
            )
        except Exception as e:
            logger.error("[rain_gauge] read failed: %s", e)
            return None

    # ── Mock ───────────────────────────────────────────────────────────────

    def _read_mock(self) -> SensorReading:
        """Generate a plausible mock rainfall reading.

        Real rain is intermittent: mostly zero, occasionally non-zero.
        We simulate that with a 10 % chance of a rain event, drawing the
        accumulation from MockManager's rain_mm baseline when an event
        occurs, and zero otherwise.
        """
        # 10 % chance of rain this read window — matches the empirical
        # intermittency of tipping-bucket data at typical poll intervals.
        if random.random() < 0.10:
            rain_mm = self._mock.get("rain_mm", jitter=0.30)
            rain_mm = max(0.1, rain_mm)  # an event is at least a trace
            # A plausible rate for a real shower: scale accumulation up
            # to an hourly figure (assume ~2 min between reads → ×30).
            rain_rate_mmh = rain_mm * 30.0
        else:
            rain_mm = 0.0
            rain_rate_mmh = 0.0

        return SensorReading(
            sensor_name=self.name,
            metrics={
                "rain_mm": round(rain_mm, 3),
                "rain_rate_mmh": round(rain_rate_mmh, 2),
            },
            units={
                "rain_mm": "mm",
                "rain_rate_mmh": "mm/h",
            },
            metadata={"source": "mock", "rain_event": rain_mm > 0.0},
        )

    # ── Cleanup ───────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Remove the GPIO event detect so the pin can be reused."""
        if GPIO is not None:
            try:
                GPIO.remove_event_detect(self.pin)
            except Exception:
                pass  # best-effort