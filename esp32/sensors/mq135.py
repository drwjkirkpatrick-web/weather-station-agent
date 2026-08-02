"""ESP32 MQ-135 air quality sensor driver (ADC).

The MQ-135 is a low-cost analog gas sensor:
- Detects NH3, NOx, alcohol, benzene, smoke, CO2
- Analog output — read via ESP32 ADC
- Needs voltage divider if sensor outputs >3.3V (most modules are fine)
- ~$2–3
- Requires calibration (R0 in clean air) for meaningful PPM values

Wiring:
  MQ-135 VCC  → 5V (sensor heater needs 5V)
  MQ-135 GND  → GND
  MQ-135 A0   → GPIO34 (default, any ADC1 pin on ESP32)
  NOTE: ESP32 ADC is noisy.  For better accuracy, use an external
  ADC (ADS1115 via I2C) or oversample and average.

The PPM calculation here is approximate.  For a research-grade station,
use a BME680 (IAQ) or SGP30 instead.
"""

from sensor_base import SensorBase, SensorReading

try:
    from machine import ADC, Pin
except ImportError:
    ADC = Pin = None  # type: ignore[misc,assignment]

try:
    import time
    _sleep_ms = lambda ms: time.sleep_ms(ms)
except AttributeError:
    import time as _time
    _sleep_ms = lambda ms: _time.sleep(ms / 1000.0)


class MQ135Sensor(SensorBase):
    name = "mq135"
    metrics = ["co2_ppm", "air_quality_index"]
    bus_type = "adc"
    description = "MQ-135 analog air quality sensor"

    def __init__(self, pin, vin=3.3, rl=10000, r0=10000, mock_mode=False):
        super().__init__(mock_mode=mock_mode)
        self.pin = pin
        self.vin = vin
        self.rl = rl          # load resistor (ohms)
        self.r0 = r0          # calibrated resistance in clean air
        self._adc = None

    def _init_hardware(self):
        try:
            self._adc = ADC(Pin(self.pin))
            self._adc.atten(ADC.ATTN_11DB)   # 0–3.3V
            self._adc.width(ADC.WIDTH_12BIT)  # 0–4095
            return True
        except Exception as e:
            print("[mq135] init failed: {}".format(e))
            return False

    def _read_hardware(self):
        try:
            # Oversample for noise reduction
            total = 0
            samples = 10
            for _ in range(samples):
                total += self._adc.read()
                _sleep_ms(5)
            raw = total // samples

            # Convert to voltage
            voltage = (raw / 4095.0) * self.vin

            # Calculate sensor resistance
            # Rs = RL * (Vin / Vout - 1)
            if voltage <= 0.01:
                voltage = 0.01
            rs = self.rl * (self.vin / voltage - 1.0)

            # Approximate CO2 PPM (very rough — needs proper calibration curve)
            # Typical: ratio = Rs/R0, PPM = a * ratio^b
            # Coefficients vary by batch; these are common starting points
            ratio = rs / self.r0
            co2_ppm = 116.6020682 * (ratio ** -2.769034857)
            co2_ppm = max(350.0, min(10000.0, co2_ppm))

            # Simple air quality index 0–500
            aqi = min(500, max(0, int((co2_ppm - 350) / 19.3)))

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "co2_ppm": round(co2_ppm, 1),
                    "air_quality_index": float(aqi),
                },
                units={
                    "co2_ppm": "ppm",
                    "air_quality_index": "index",
                },
                metadata={
                    "raw_adc": raw,
                    "voltage": round(voltage, 3),
                    "rs": round(rs, 1),
                },
            )
        except Exception as e:
            print("[mq135] read failed: {}".format(e))
            return None

    def _read_mock(self):
        import random
        return SensorReading(
            sensor_name=self.name,
            metrics={
                "co2_ppm": round(400.0 + random.uniform(0, 600), 1),
                "air_quality_index": round(random.uniform(0, 50), 1),
            },
            units={
                "co2_ppm": "ppm",
                "air_quality_index": "index",
            },
            metadata={"source": "mock"},
        )
