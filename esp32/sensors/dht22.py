"""ESP32 DHT22 sensor driver (one-wire).

The DHT22 (AM2302) is a cheap digital temperature/humidity sensor.
- One-wire protocol (not Dallas 1-Wire — it's a custom single-bus protocol)
- Built-in pull-up resistor on most breakout boards
- ~$3–5
- Less accurate than BME280/SHT31 but very popular for beginners

Wiring:
  DHT22 VCC  → 3.3V
  DHT22 GND  → GND
  DHT22 DATA → GPIO4 (default, configurable — any digital pin)

MicroPython has a built-in ``dht`` module — no external library needed.
"""

from sensor_base import SensorBase, SensorReading

try:
    from machine import Pin
except ImportError:
    Pin = None  # type: ignore[misc,assignment]

try:
    import dht
except ImportError:
    dht = None


class DHT22Sensor(SensorBase):
    name = "dht22"
    metrics = ["temperature_c", "humidity_pct"]
    bus_type = "onewire"
    description = "DHT22 digital temperature and humidity sensor"

    def __init__(self, pin, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self._driver = None

    def _init_hardware(self):
        if dht is None:
            print("[dht22] dht module not available")
            return False
        try:
            self._driver = dht.DHT22(Pin(self.pin))
            self._driver.measure()
            return True
        except Exception as e:
            print("[dht22] init failed: {}".format(e))
            return False

    def _read_hardware(self):
        try:
            self._driver.measure()
            t = self._driver.temperature()
            h = self._driver.humidity()
            if t is None or h is None:
                return None
            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "temperature_c": round(t, 2),
                    "humidity_pct": round(h, 2),
                },
                units={
                    "temperature_c": "celsius",
                    "humidity_pct": "percent",
                },
            )
        except Exception as e:
            print("[dht22] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        return SensorReading(
            sensor_name=self.name,
            metrics={
                "temperature_c": round(20.0 + random.uniform(-5, 5), 2),
                "humidity_pct": round(50.0 + random.uniform(-15, 15), 2),
            },
            units={
                "temperature_c": "celsius",
                "humidity_pct": "percent",
            },
            metadata={"source": "mock"},
        )
