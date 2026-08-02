"""ESP32 BME280 sensor driver (I2C).

The BME280 is the most popular weather sensor for ESP32 projects:
- Measures temperature, humidity, and pressure
- I2C interface — only 2 wires needed
- ~$5 from common vendors
- Works with MicroPython's ``bme280`` library (no C extensions)

Dependencies (MicroPython):
  ``bme280.py`` from https://github.com/catdog2/mpy_bme280_esp8266
  Place ``bme280.py`` in the ESP32 root directory alongside ``main.py``.

Wiring:
  BME280 VCC → 3.3V
  BME280 GND → GND
  BME280 SDA → GPIO21 (default, configurable)
  BME280 SCL → GPIO22 (default, configurable)
"""

from sensor_base import SensorBase, SensorReading

try:
    import bme280
except ImportError:
    bme280 = None


class BME280Sensor(SensorBase):
    name = "bme280"
    metrics = ["temperature_c", "humidity_pct", "pressure_hpa"]
    bus_type = "i2c"
    description = "Bosch BME280 temperature, humidity, and pressure sensor"

    def __init__(self, i2c_bus, i2c_address=0x76, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.i2c_bus = i2c_bus
        self.i2c_address = i2c_address
        self._driver = None

    def _init_hardware(self):
        if bme280 is None:
            print("[bme280] bme280.py library not found")
            return False
        try:
            self._driver = bme280.BME280(
                i2c=self.i2c_bus,
                address=self.i2c_address,
            )
            # Test read
            _ = self._driver.values
            return True
        except Exception as e:
            print("[bme280] init failed: {}".format(e))
            return False

    def _read_hardware(self):
        try:
            t, p, h = self._driver.values
            # The bme280 library returns strings like "23.45C", "1013.25hPa", "45.6%"
            # Parse out the numeric part
            t_val = float(t.replace("C", "").replace(" ", ""))
            p_val = float(p.replace("hPa", "").replace(" ", ""))
            h_val = float(h.replace("%", "").replace(" ", ""))
            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "temperature_c": round(t_val, 2),
                    "humidity_pct": round(h_val, 2),
                    "pressure_hpa": round(p_val, 2),
                },
                units={
                    "temperature_c": "celsius",
                    "humidity_pct": "percent",
                    "pressure_hpa": "hPa",
                },
            )
        except Exception as e:
            print("[bme280] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        return SensorReading(
            sensor_name=self.name,
            metrics={
                "temperature_c": round(20.0 + random.uniform(-5, 5), 2),
                "humidity_pct": round(50.0 + random.uniform(-15, 15), 2),
                "pressure_hpa": round(1013.25 + random.uniform(-10, 10), 2),
            },
            units={
                "temperature_c": "celsius",
                "humidity_pct": "percent",
                "pressure_hpa": "hPa",
            },
            metadata={"source": "mock"},
        )
