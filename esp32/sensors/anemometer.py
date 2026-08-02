"""ESP32 anemometer (wind speed) driver.

Counts GPIO pulses from a hall-effect anemometer over a sample window.
Uses MicroPython's ``Pin.irq()`` for interrupt-safe counting.

Wiring:
  Anemometer VCC → 3.3V
  Anemometer GND → GND
  Anemometer SIG → GPIO14 (default, configurable — any digital pin)
  Internal pull-up enabled

Calibration:
  Wind speed (m/s) = (pulses / elapsed_s) * (2π * radius_cm / 100) * calibration
  Typical cup anemometer: 1 pulse per revolution.
"""

from sensor_base import SensorBase, SensorReading

try:
    from machine import Pin
except ImportError:
    Pin = None  # type: ignore[misc,assignment]

import time


class AnemometerSensor(SensorBase):
    name = "anemometer"
    metrics = ["wind_speed_mps"]
    bus_type = "gpio"
    description = "Hall-effect cup anemometer"

    def __init__(self, pin, radius_cm=6.0, calibration=1.0, sample_seconds=2.0, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self.radius_cm = radius_cm
        self.calibration = calibration
        self.sample_seconds = sample_seconds
        self._count = 0
        self._pin_obj = None

    def _init_hardware(self):
        try:
            self._pin_obj = Pin(self.pin, Pin.IN, Pin.PULL_UP)
            self._pin_obj.irq(handler=self._irq_handler, trigger=Pin.IRQ_FALLING)
            return True
        except Exception as e:
            print("[anemometer] init failed: {}".format(e))
            return False

    def _irq_handler(self, pin):
        """ISR — keep minimal."""
        self._count += 1

    def _read_hardware(self):
        try:
            # Reset counter and measure over sample window
            self._count = 0
            time.sleep(self.sample_seconds)
            pulses = self._count

            # Circumference in meters
            circumference_m = 2 * 3.1416 * (self.radius_cm / 100.0)
            elapsed_s = max(self.sample_seconds, 0.001)
            rev_per_sec = pulses / elapsed_s
            speed_mps = rev_per_sec * circumference_m * self.calibration

            return SensorReading(
                sensor_name=self.name,
                metrics={"wind_speed_mps": round(speed_mps, 2)},
                units={"wind_speed_mps": "m/s"},
                metadata={"pulses": pulses, "sample_seconds": self.sample_seconds},
            )
        except Exception as e:
            print("[anemometer] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        speed = max(0.0, random.gauss(3.0, 2.0))
        return SensorReading(
            sensor_name=self.name,
            metrics={"wind_speed_mps": round(speed, 2)},
            units={"wind_speed_mps": "m/s"},
            metadata={"source": "mock"},
        )
