"""ESP32 weather station node — main orchestrator (MicroPython).

Wake → read sensors → transmit → deep sleep.

Designed for battery-powered ESP32 field nodes that push data to a
Raspberry Pi hub running the full ``weather-station-agent``.

Flashing:
  1. Install MicroPython firmware on ESP32
  2. Copy these files to the ESP32 root via ampy/rshell/webrepl:
     - config.py
     - sensor_base.py
     - networking.py
     - sensors/*.py
     - main.py (this file)
  3. Edit config.py with your WiFi credentials and Pi hub IP
  4. Reboot the ESP32

Power budget (typical with 5-minute sleep):
  - Wake + WiFi connect + transmit: ~5 seconds at 150mA = 0.21mAh
  - Deep sleep: 295 seconds at 0.01mA = 0.003mAh
  - Total per cycle: ~0.21mAh
  - 18650 battery (2500mAh): ~1 year theoretical (real: 3–6 months)
"""

import config
from sensor_base import SensorBase, SensorReading
from networking import NetworkManager

from machine import I2C, Pin, deepsleep, reset_cause, DEEPSLEEP_RESET
import time


def setup_i2c():
    """Initialize I2C bus."""
    return I2C(
        scl=Pin(config.I2C_SCL_PIN),
        sda=Pin(config.I2C_SDA_PIN),
        freq=config.I2C_FREQ,
    )


def build_sensors(i2c_bus):
    """Build enabled sensor instances from config."""
    sensors = []

    if config.BME280_ENABLED:
        from sensors.bme280 import BME280Sensor
        sensors.append(BME280Sensor(
            i2c_bus=i2c_bus,
            i2c_address=config.BME280_I2C_ADDRESS,
            mock_mode=False,
        ))

    if config.DHT22_ENABLED:
        from sensors.dht22 import DHT22Sensor
        sensors.append(DHT22Sensor(
            pin=config.ONEWIRE_PIN,
            mock_mode=False,
        ))

    if config.DS18B20_ENABLED:
        from sensors.ds18b20 import DS18B20Sensor
        sensors.append(DS18B20Sensor(
            pin=config.ONEWIRE_PIN,
            resolution=config.DS18B20_RESOLUTION,
            mock_mode=False,
        ))

    if config.ANEMOMETER_ENABLED:
        from sensors.anemometer import AnemometerSensor
        sensors.append(AnemometerSensor(
            pin=config.ANEMOMETER_PIN,
            radius_cm=config.ANEMOMETER_RADIUS_CM,
            calibration=config.ANEMOMETER_CALIBRATION,
            mock_mode=False,
        ))

    if config.RAIN_GAUGE_ENABLED:
        from sensors.rain_gauge import RainGaugeSensor
        sensors.append(RainGaugeSensor(
            pin=config.RAIN_GAUGE_PIN,
            bucket_ml=config.RAIN_BUCKET_ML,
            mock_mode=False,
        ))

    if config.BH1750_ENABLED:
        from sensors.bh1750 import BH1750Sensor
        sensors.append(BH1750Sensor(
            i2c_bus=i2c_bus,
            mock_mode=False,
        ))

    if config.MQ135_ENABLED:
        from sensors.mq135 import MQ135Sensor
        sensors.append(MQ135Sensor(
            pin=config.ADC_PIN,
            vin=config.MQ135_VIN,
            rl=config.MQ135_RL,
            r0=config.MQ135_R0,
            mock_mode=False,
        ))

    return sensors


def run_cycle():
    """One full wake-read-transmit-sleep cycle."""
    if config.VERBOSE:
        print("\n[main] Wake cause: {}".format("deep-sleep" if reset_cause() == DEEPSLEEP_RESET else "power-on/reset"))
        print("[main] Station: {}".format(config.STATION_ID))

    # Status LED blink on wake
    led = Pin(config.LED_STATUS_PIN, Pin.OUT)
    led.value(1)
    time.sleep_ms(50)
    led.value(0)

    # Init I2C
    i2c_bus = setup_i2c()

    # Build and init sensors
    sensors = build_sensors(i2c_bus)
    for s in sensors:
        ok = s.initialize()
        if config.VERBOSE:
            print("[main] {} init: {}".format(s.name, "OK" if ok else "FAIL"))

    # Read
    readings = []
    for s in sensors:
        r = s.read()
        if r:
            readings.append(r.to_dict())
            if config.VERBOSE:
                print("[main] {}: {}".format(r.sensor_name, r.metrics))
        else:
            print("[main] {}: READ FAILED".format(s.name))

    if not readings:
        print("[main] No readings obtained — skipping TX and sleeping")
        _go_to_sleep()
        return

    # Package payload
    payload = {
        "station_id": config.STATION_ID,
        "station_name": config.STATION_NAME,
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "elevation_m": config.ELEVATION_M,
        "readings": readings,
        "node_type": "esp32",
    }

    # Blink LED during TX
    led.value(1)

    # Connect and transmit
    net = NetworkManager(config)
    tx_ok = False
    if net.connect_wifi():
        tx_ok = net.transmit(payload)
        net.disconnect_wifi()

    led.value(0)

    # TX status blink pattern
    if tx_ok:
        for _ in range(2):
            led.value(1)
            time.sleep_ms(100)
            led.value(0)
            time.sleep_ms(100)
    else:
        for _ in range(5):
            led.value(1)
            time.sleep_ms(50)
            led.value(0)
            time.sleep_ms(50)

    if config.VERBOSE or not tx_ok:
        print("[main] TX: {}".format("OK" if tx_ok else "FAIL"))

    # Sleep
    _go_to_sleep()


def _go_to_sleep():
    """Enter deep sleep to save power."""
    if not config.ENABLE_DEEP_SLEEP:
        print("[main] Deep sleep disabled — halting (reset to continue)")
        while True:
            time.sleep(1)
    print("[main] Sleeping for {}s".format(config.DEEP_SLEEP_SECONDS))
    time.sleep_ms(100)  # Let serial buffer flush
    deepsleep(config.DEEP_SLEEP_SECONDS * 1000)


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as e:
        print("[main] FATAL: {}".format(e))
        # On fatal error, sleep shorter to retry sooner
        time.sleep_ms(100)
        deepsleep(60 * 1000)  # Retry in 1 minute
