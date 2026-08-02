"""ESP32 BH1750 ambient light sensor driver (I2C).

The BH1750 is a simple digital light sensor:
- I2C interface
- Measures lux directly (no ADC or voltage conversion needed)
- ~$2–3
- Very low power — great for battery nodes

Dependencies:
  No external library needed — the protocol is simple enough to
  implement directly with ``machine.I2C``.

Wiring:
  BH1750 VCC → 3.3V
  BH1750 GND → GND
  BH1750 SDA → GPIO21 (default)
  BH1750 SCL → GPIO22 (default)
  ADDR pin → GND (address 0x23) or VCC (address 0x5C)
"""

from sensor_base import SensorBase, SensorReading


# BH1750 commands
_CMD_POWER_ON = 0x01
_CMD_RESET = 0x07
_CMD_CONT_HRES = 0x10       # Continuous H-resolution mode (1 lx resolution)
_CMD_CONT_HRES2 = 0x11      # Continuous H-resolution mode 2 (0.5 lx)
_CMD_CONT_LRES = 0x13       # Continuous L-resolution mode (4 lx)


class BH1750Sensor(SensorBase):
    name = "bh1750"
    metrics = ["light_lux"]
    bus_type = "i2c"
    description = "BH1750 ambient light sensor"

    def __init__(self, i2c_bus, i2c_address=0x23, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.i2c_bus = i2c_bus
        self.i2c_address = i2c_address

    def _init_hardware(self):
        try:
            self.i2c_bus.writeto(self.i2c_address, bytes([_CMD_POWER_ON]))
            self.i2c_bus.writeto(self.i2c_address, bytes([_CMD_RESET]))
            self.i2c_bus.writeto(self.i2c_address, bytes([_CMD_CONT_HRES]))
            return True
        except Exception as e:
            print("[bh1750] init failed: {}".format(e))
            return False

    def _read_hardware(self):
        try:
            # Wait for measurement (typical 120ms in H-res mode)
            import time
            time.sleep_ms(180)
            data = self.i2c_bus.readfrom(self.i2c_address, 2)
            if len(data) != 2:
                return None
            raw = (data[0] << 8) | data[1]
            lux = raw / 1.2
            return SensorReading(
                sensor_name=self.name,
                metrics={"light_lux": round(lux, 1)},
                units={"light_lux": "lux"},
            )
        except Exception as e:
            print("[bh1750] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        return SensorReading(
            sensor_name=self.name,
            metrics={"light_lux": round(random.uniform(0, 50000), 1)},
            units={"light_lux": "lux"},
            metadata={"source": "mock"},
        )
