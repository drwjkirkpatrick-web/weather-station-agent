"""ESP32 rain gauge (tipping bucket) driver.

Counts GPIO pulses from a tipping-bucket rain gauge.
Each tip = ``bucket_ml`` of water.  Rate is mm/h over the elapsed time.

Uses MicroPython ``Pin.irq()`` with a 50ms debounce (software debounce
via timestamp check — MicroPython Pin.irq has no built-in bouncetime).

Wiring:
  Rain gauge VCC → 3.3V
  Rain gauge GND → GND
  Rain gauge SIG → GPIO12 (default, configurable)
"""

from sensor_base import SensorBase, SensorReading

try:
    from machine import Pin
except ImportError:
    Pin = None  # type: ignore[misc,assignment]

try:
    import time
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:
    # CPython fallback
    import time as _time
    _ticks_ms = lambda: int(_time.time() * 1000)
    _ticks_diff = lambda a, b: a - b


class RainGaugeSensor(SensorBase):
    name = "rain_gauge"
    metrics = ["rain_mm", "rain_rate_mmh"]
    bus_type = "gpio"
    description = "Tipping-bucket rain gauge"

    def __init__(self, pin, bucket_ml=0.2794, collector_area_cm2=100.0, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self.bucket_ml = bucket_ml
        self.collector_area_cm2 = collector_area_cm2
        self._tip_count = 0
        self._last_read_time = _ticks_ms()
        self._pin_obj = None
        self._last_irq_ms = 0

    def _init_hardware(self):
        try:
            self._pin_obj = Pin(self.pin, Pin.IN, Pin.PULL_UP)
            self._pin_obj.irq(handler=self._irq_handler, trigger=Pin.IRQ_FALLING)
            return True
        except Exception as e:
            print("[rain] init failed: {}".format(e))
            return False

    def _irq_handler(self, pin):
        now = _ticks_ms()
        # Software debounce: ignore edges within 50ms
        if _ticks_diff(now, self._last_irq_ms) > 50:
            self._tip_count += 1
            self._last_irq_ms = now

    def _read_hardware(self):
        try:
            now_ms = _ticks_ms()
            elapsed_ms = _ticks_diff(now_ms, self._last_read_time)
            elapsed_h = max(elapsed_ms / 3600000.0, 0.0001)

            tips = self._tip_count
            self._tip_count = 0
            self._last_read_time = now_ms

            # mm = ml / area(cm²) * 10
            rain_mm = tips * self.bucket_ml / self.collector_area_cm2 * 10.0
            rain_rate_mmh = rain_mm / elapsed_h

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "rain_mm": round(rain_mm, 2),
                    "rain_rate_mmh": round(rain_rate_mmh, 2),
                },
                units={
                    "rain_mm": "mm",
                    "rain_rate_mmh": "mm/h",
                },
                metadata={"tips": tips, "elapsed_ms": elapsed_ms},
            )
        except Exception as e:
            print("[rain] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        if random.random() < 0.15:
            rain_mm = round(random.uniform(0.1, 2.0), 2)
            rain_rate_mmh = round(rain_mm * 12.0, 2)
        else:
            rain_mm = 0.0
            rain_rate_mmh = 0.0
        return SensorReading(
            sensor_name=self.name,
            metrics={
                "rain_mm": rain_mm,
                "rain_rate_mmh": rain_rate_mmh,
            },
            units={
                "rain_mm": "mm",
                "rain_rate_mmh": "mm/h",
            },
            metadata={"source": "mock"},
        )
