"""ESP32 DS18B20 waterproof temperature probe driver (one-wire).

The DS18B20 is a rugged, waterproof temperature sensor in a metal casing.
- Dallas 1-Wire protocol (multiple sensors on one bus)
- ±0.5°C accuracy
- ~$3–5 for the waterproof version
- Ideal for soil temperature, pond monitoring, or outdoor use

Dependencies:
  MicroPython has built-in ``onewire`` and ``ds18x20`` modules.

Wiring:
  DS18B20 VCC (red)    → 3.3V
  DS18B20 GND (black)  → GND
  DS18B20 DATA (yellow) → GPIO4 (default — any digital pin)
  Add 4.7kΩ pull-up resistor between DATA and VCC
"""

from sensor_base import SensorBase, SensorReading

try:
    from machine import Pin
except ImportError:
    Pin = None  # type: ignore[misc,assignment]

try:
    import onewire
    import ds18x20
except ImportError:
    onewire = None
    ds18x20 = None


class DS18B20Sensor(SensorBase):
    name = "ds18b20"
    metrics = ["temperature_c"]
    bus_type = "onewire"
    description = "DS18B20 waterproof temperature probe"

    def __init__(self, pin, resolution=12, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self.resolution = resolution
        self._driver = None
        self._roms = []

    def _init_hardware(self):
        if onewire is None or ds18x20 is None:
            print("[ds18b20] onewire/ds18x20 modules not available")
            return False
        try:
            ow = onewire.OneWire(Pin(self.pin))
            self._driver = ds18x20.DS18X20(ow)
            self._roms = self._driver.scan()
            if not self._roms:
                print("[ds18b20] no devices found on 1-wire bus")
                return False
            return True
        except Exception as e:
            print("[ds18b20] init failed: {}".format(e))
            return False

    def _read_hardware(self):
        try:
            self._driver.convert_temp()
            # Wait for conversion (max 750ms at 12-bit)
            import time
            time.sleep_ms(750)
            # Read first sensor only (multi-sensor: iterate roms)
            temp = self._driver.read_temp(self._roms[0])
            return SensorReading(
                sensor_name=self.name,
                metrics={"temperature_c": round(temp, 2)},
                units={"temperature_c": "celsius"},
                metadata={"device_count": len(self._roms)},
            )
        except Exception as e:
            print("[ds18b20] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        return SensorReading(
            sensor_name=self.name,
            metrics={"temperature_c": round(18.0 + random.uniform(-3, 3), 2)},
            units={"temperature_c": "celsius"},
            metadata={"source": "mock"},
        )
