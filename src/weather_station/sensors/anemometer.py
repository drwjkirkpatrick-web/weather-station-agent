"""
Anemometer (wind speed) sensor driver — GPIO hall-effect sensor.

WHAT IT MEASURES
----------------
Wind speed in meters per second (m/s), derived from the rotation rate of a
cup anemometer.  A small magnet on the spinning shaft passes a stationary
hall-effect sensor once (or twice) per revolution, generating a clean
digital pulse on a GPIO pin.

WHY IT'S USEFUL FOR WEATHER
---------------------------
Wind speed is one of the four fundamental surface-weather parameters (along
with temperature, humidity, and pressure).  It drives:

  • Evaporative cooling / wind-chill calculations for human-comfort alerts.
  • Fire-weather indices (Rothermel / Haines rely on sustained wind speed).
  • Pollen and air-quality dispersion modelling — high wind disperses PM.
  • Long-term anemometry for siting decisions (e.g. is there enough wind for
    a turbine, should the orchard get wind-break fencing).

PHYSICAL PRINCIPLE
------------------
A cup anemometer turns at a rate proportional to the wind speed passing
through it.  A magnet on the shaft triggers a hall-effect switch; we count
GPIO falling-edge interrupts during a fixed sample window (2 s here).  For a
typical cup anemometer the sensor produces **2 pulses per full rotation**,
so:

    rotations     = pulse_count / 2
    circumference = 2 * π * radius_cm           # distance one cup edge travels
    wind_speed    = rotations * circumference / 100   # cm/s → m/s

The ``calibration_factor`` lets us compensate for bearing friction and cup
geometry differences against a known reference (e.g. a pitot-tube anemometer).

GRACEFUL DEGRADATION
--------------------
``RPi.GPIO`` only importable on actual Pi hardware.  We wrap the import in
try/except so the module loads anywhere; when the library is missing,
``_init_hardware()`` returns False and the base class falls back to mock mode.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from weather_station.core.mock_manager import MockManager
from weather_station.core.sensor_base import SensorBase, SensorReading

logger = logging.getLogger(__name__)

# ── Optional hardware dependency ──────────────────────────────────────────
# RPi.GPIO is only available on real Raspberry Pi hardware.  Importing it
# on a dev laptop would crash, so we guard it and degrade to mock mode.
try:
    import RPi.GPIO as GPIO
except ImportError:  # pragma: no cover — exercised on non-Pi machines
    GPIO = None  # type: ignore[assignment]


class AnemometerSensor(SensorBase):
    """Cup anemometer driver using a GPIO-interrupt pulse counter.

    The hall-effect sensor pulls the GPIO pin low each time the shaft
    magnet passes, so we count falling-edge interrupts over a fixed
    sample window and convert pulse rate → rotations → wind speed.
    """

    name = "anemometer"
    metrics = ["wind_speed_mps"]
    bus_type = "gpio"
    description = "Cup anemometer (hall-effect, GPIO pulse counting) — wind speed"

    # ── Construction ───────────────────────────────────────────────────────

    def __init__(
        self,
        pin: int = 4,
        radius_cm: float = 6.0,
        calibration_factor: float = 1.0,
        mock_mode: bool = False,
    ) -> None:
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self.radius_cm = radius_cm
        self.calibration_factor = calibration_factor

        # Interrupt counter — incremented by the GPIO callback, read/reset
        # inside _read_hardware().  Protected by a Lock because the callback
        # fires on a separate kernel/BCM thread.
        self._pulse_count: int = 0
        self._count_lock = threading.Lock()

        # MockManager lives on the instance so each sensor owns its own
        # random-walk state (avoids cross-sensor coupling in the mock layer).
        self._mock = MockManager()

    # ── Hardware lifecycle ─────────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Set up the GPIO pin with a pull-up and a falling-edge interrupt.

        Returns False (→ mock fallback) when RPi.GPIO is not available.
        """
        if GPIO is None:
            logger.warning(
                "[anemometer] RPi.GPIO not available — cannot init hardware"
            )
            return False

        try:
            # NOTE: BCM pin numbering — the physical pin number differs.
            # Pin 4 (BCM) is a safe choice (no special I2C/SPI conflict).
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            # bouncetime=20 ms debounces the reed/hall switch so a single
            # pass of the magnet isn't counted twice.  20 ms → max ~50 Hz
            # counting rate, far above any realistic anemometer rotation.
            GPIO.add_event_detect(
                self.pin,
                GPIO.FALLING,
                callback=self._interrupt_callback,
                bouncetime=20,
            )
            logger.info(
                "[anemometer] GPIO pin %d armed for falling-edge pulses",
                self.pin,
            )
            return True
        except Exception as e:
            # e.g. permission denied (not root / not in gpio group)
            logger.error("[anemometer] GPIO setup failed: %s", e)
            return False

    # ── Interrupt handling ─────────────────────────────────────────────────

    def _interrupt_callback(self, channel: int) -> None:
        """GPIO falling-edge ISR.  Runs in the BCM driver thread — must be
        as short as possible.  We only bump a counter under a lock.
        """
        # NOTE: the lock keeps the increment atomic relative to _read_hardware's
        # read+reset, which would otherwise race and drop or double-count pulses.
        with self._count_lock:
            self._pulse_count += 1

    # ── Reading ────────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Count pulses over a 2-second sample window → wind speed.

        Returns a SensorReading with ``wind_speed_mps`` (m/s), or None on
        a hardware read error.
        """
        if GPIO is None:
            logger.error("[anemometer] RPi.GPIO missing during read")
            return None

        try:
            # Reset the counter, wait for the sample window, then read.
            # WHY a lock around reset+wait:  the callback may fire at any
            # moment; we want a clean count for exactly *this* window.
            with self._count_lock:
                self._pulse_count = 0

            sample_window = 2.0  # seconds — long enough for low-wind resolution
            time.sleep(sample_window)

            with self._count_lock:
                count = self._pulse_count

            # ── Pulse count → wind speed ───────────────────────────────────
            # 2 pulses per rotation is typical for dual-magnet cup anemometers.
            # If your hardware emits 1 pulse/rev, halve this constant.
            rotations = count / 2.0

            circumference_cm = 2.0 * math.pi * self.radius_cm

            # cm/s → m/s: divide by 100.  calibration_factor corrects for
            # bearing drag and cup geometry against a reference instrument.
            speed_mps = (rotations * circumference_cm / 100.0) * self.calibration_factor

            # Clamp at 0 — wind speed is non-negative.
            speed_mps = max(0.0, speed_mps)

            return SensorReading(
                sensor_name=self.name,
                metrics={"wind_speed_mps": round(speed_mps, 3)},
                units={"wind_speed_mps": "m/s"},
                metadata={
                    "pin": self.pin,
                    "radius_cm": self.radius_cm,
                    "calibration_factor": self.calibration_factor,
                    "sample_window_s": sample_window,
                    "pulse_count": count,
                },
            )
        except Exception as e:
            logger.error("[anemometer] read failed: %s", e)
            return None

    # ── Mock ───────────────────────────────────────────────────────────────

    def _read_mock(self) -> SensorReading:
        """Generate a plausible mock wind speed using MockManager."""
        speed = self._mock.get("wind_speed_mps", jitter=0.15)
        # NOTE: higher jitter for wind — gusts are inherently noisy.
        return SensorReading(
            sensor_name=self.name,
            metrics={"wind_speed_mps": round(max(0.0, speed), 3)},
            units={"wind_speed_mps": "m/s"},
            metadata={"source": "mock", "mock_jitter": 0.15},
        )

    # ── Cleanup ───────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Remove the GPIO event detect so the pin can be reused."""
        if GPIO is not None:
            try:
                GPIO.remove_event_detect(self.pin)
            except Exception:
                pass  # best-effort; pin may not have been armed