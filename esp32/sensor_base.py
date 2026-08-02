"""ESP32/MicroPython sensor base class.

Lightweight version of the Pi-side SensorBase, adapted for MicroPython
constraints (no dataclasses, no type hints that MicroPython ignores,
minimal imports).

The ESP32 runs as a battery-powered field node.  It:
1. Reads sensors on wake
2. Transmits data via MQTT or HTTP
3. Deep-sleeps until the next interval

No local SQLite, no web dashboard, no alerts — those live on the Pi hub.
"""

import time

try:
    from machine import Pin, I2C, ADC, reset_cause, DEEPSLEEP_RESET
except ImportError:
    # CPython / dev machine fallback
    Pin = I2C = ADC = reset_cause = DEEPSLEEP_RESET = None  # type: ignore[misc,assignment]

# ── SensorReading ──────────────────────────────────────────────
# MicroPython has no dataclasses — use a plain class.

class SensorReading:
    def __init__(self, sensor_name, metrics=None, units=None, metadata=None):
        self.sensor_name = sensor_name
        self.timestamp = self._iso_now()
        self.metrics = metrics or {}
        self.units = units or {}
        self.metadata = metadata or {}

    @staticmethod
    def _iso_now():
        # MicroPython udatetime isn't always available
        try:
            import udatetime as dt
            return dt.datetime.now().isoformat()
        except ImportError:
            # Fallback: build ISO string from localtime
            t = time.localtime()
            return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(
                t[0], t[1], t[2], t[3], t[4], t[5]
            )

    def to_dict(self):
        return {
            "sensor_name": self.sensor_name,
            "timestamp": self.timestamp,
            "metrics": self.metrics,
            "units": self.units,
            "metadata": self.metadata,
        }


# ── SensorBase ─────────────────────────────────────────────────

class SensorBase:
    """Abstract base for all ESP32 sensor drivers.

    Subclasses set ``name``, ``metrics``, ``bus_type`` and implement
    ``_read_hardware()``.
    """

    name = "base"
    metrics = []
    bus_type = "unknown"          # 'i2c', 'onewire', 'gpio', 'adc'
    description = "Generic sensor"

    def __init__(self, mock_mode=False):
        self.mock_mode = mock_mode
        self._initialized = False
        self._health_score = 1.0
        self._consecutive_failures = 0
        self._last_reading = None

    def initialize(self):
        """Initialize hardware. Returns True on success or mock mode."""
        if self.mock_mode:
            self._initialized = True
            return True
        try:
            result = self._init_hardware()
            self._initialized = result
            return result
        except Exception as e:
            print("[{}] init error: {}".format(self.name, e))
            self._initialized = False
            return False

    def _init_hardware(self):
        """Subclass hook. Default: assume success."""
        return True

    def read(self):
        """Take a reading. Returns SensorReading or None on failure."""
        if not self._initialized and not self.mock_mode:
            print("[{}] not initialized, skipping read".format(self.name))
            return None
        try:
            if self.mock_mode:
                reading = self._read_mock()
            else:
                reading = self._read_hardware()
            if reading is None:
                self._record_failure()
                return None
            self._record_success(reading)
            return reading
        except Exception as e:
            print("[{}] read error: {}".format(self.name, e))
            self._record_failure()
            return None

    def _read_hardware(self):
        raise NotImplementedError

    def _read_mock(self):
        """Default mock: zeros for all declared metrics."""
        return SensorReading(
            sensor_name=self.name,
            metrics={m: 0.0 for m in self.metrics},
            units={m: "" for m in self.metrics},
        )

    def _record_success(self, reading):
        self._consecutive_failures = 0
        self._health_score = min(1.0, self._health_score + 0.1)
        self._last_reading = reading

    def _record_failure(self):
        self._consecutive_failures += 1
        self._health_score = max(0.0, self._health_score - 0.2)

    def health_check(self):
        return {
            "name": self.name,
            "description": self.description,
            "bus_type": self.bus_type,
            "initialized": self._initialized,
            "mock_mode": self.mock_mode,
            "health_score": round(self._health_score, 2),
            "consecutive_failures": self._consecutive_failures,
            "metrics": list(self.metrics),
        }

    @property
    def is_healthy(self):
        return self._health_score > 0.3 and self._initialized
