# ESP32 Weather Node Setup Guide

This guide walks you through deploying the ESP32 field node that pushes
weather readings to your Raspberry Pi hub running ``weather-station-agent``.

## What You Need

| Item | Purpose | ~Cost |
|------|---------|-------|
| ESP32 Dev Kit (WROOM-32) | Main microcontroller | $5–8 |
| BME280 module | Temp, humidity, pressure | $5 |
| (Optional) DHT22 | Cheaper temp/humidity | $3 |
| (Optional) DS18B20 waterproof | Soil/pond temperature | $3 |
| (Optional) BH1750 | Ambient light | $2 |
| (Optional) Anemometer | Wind speed | $15–30 |
| (Optional) Tipping-bucket rain gauge | Rainfall | $15–30 |
| (Optional) MQ-135 | Air quality (approximate) | $2 |
| 18650 battery + holder | Power | $5 |
| TP4056 charging module | Battery charging | $1 |
| Breadboard + jumper wires | Prototyping | $5 |

## Wiring Diagram

### I2C Bus (shared by BME280 + BH1750)

```
ESP32 GPIO21 (SDA) ─────┬──── BME280 SDA
                        ├──── BH1750 SDA
ESP32 GPIO22 (SCL) ─────┴──── BME280 SCL
                        └──── BH1750 SCL
3.3V ───────────────────┬──── BME280 VCC
                        ├──── BH1750 VCC
GND ────────────────────┴──── BME280 GND
                        └──── BH1750 GND
```

### One-Wire (DHT22 or DS18B20)

```
ESP32 GPIO4 ──────────── DHT22 DATA (or DS18B20 yellow)
3.3V ─────────────────── DHT22 VCC + 4.7kΩ pull-up to DATA
GND ──────────────────── DHT22 GND
```

### GPIO Sensors

```
ESP32 GPIO14 ─────────── Anemometer SIG (hall effect)
ESP32 GPIO12 ─────────── Rain gauge SIG (reed switch)
3.3V ─────────────────── Sensor VCC (both)
GND ────────────────────── Sensor GND (both)
```

### MQ-135 (ADC)

```
ESP32 GPIO34 ──────────── MQ-135 A0
5V ────────────────────── MQ-135 VCC (heater needs 5V)
GND ────────────────────── MQ-135 GND
```

**Note:** MQ-135 output can exceed 3.3V. Use a voltage divider (two
10kΩ resistors) or verify your module has a built-in regulator.

## Software Setup

### 1. Flash MicroPython

```bash
# Download MicroPython ESP32 firmware
wget https://micropython.org/download/ESP32_GENERIC/

# Erase and flash (replace /dev/ttyUSB0 with your port)
esptool.py --chip esp32 --port /dev/ttyUSB0 erase_flash
esptool.py --chip esp32 --port /dev/ttyUSB0 \
  write_flash -z 0x1000 ESP32_GENERIC-20240101-v1.22.0.bin
```

### 2. Copy Project Files

Install ``mpremote`` or ``ampy``:

```bash
pip install mpremote
```

Copy files to the ESP32:

```bash
cd esp32/

# Core files
mpremote cp config.py :
mpremote cp sensor_base.py :
mpremote cp networking.py :
mpremote cp main.py :

# Sensor drivers
mpremote cp sensors/bme280.py :sensors/
mpremote cp sensors/dht22.py :sensors/
mpremote cp sensors/ds18b20.py :sensors/
mpremote cp sensors/anemometer.py :sensors/
mpremote cp sensors/rain_gauge.py :sensors/
mpremote cp sensors/bh1750.py :sensors/
mpremote cp sensors/mq135.py :sensors/

# BME280 library (from https://github.com/catdog2/mpy_bme280_esp8266)
mpremote cp bme280.py :
```

### 3. Edit Config

Edit ``config.py`` on your PC first, then copy:

```python
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"

# Point to your Pi hub IP
MQTT_BROKER = "192.168.1.50"   # your Pi's IP
# or
HTTP_ENDPOINT = "http://192.168.1.50:8080/api/ingest"

STATION_ID = "esp32-01"
```

### 4. Reboot

```bash
mpremote reset
```

The ESP32 will:
1. Connect to WiFi
2. Read all enabled sensors
3. Transmit JSON to the Pi hub
4. Enter deep sleep for 5 minutes
5. Wake and repeat

## Power Budget

| Mode | Current | Duration per cycle |
|------|---------|-------------------|
| Wake + WiFi TX | ~150mA | ~5s |
| Deep sleep | ~0.01mA | ~295s |
| **Per cycle** | | **~0.21mAh** |

With a 2500mAh 18650 battery: ~3–6 months real-world runtime.

## Troubleshooting

### No WiFi connection
- Verify SSID/password in ``config.py``
- Check router accepts new devices
- Increase ``WIFI_TIMEOUT_SECONDS``

### Sensor read failures
- Enable ``VERBOSE = True`` in ``config.py`` to see UART output
- Verify I2C addresses with a scanner script
- Check wiring (SDA/SCL not swapped)

### MQTT not reaching Pi
- Verify Mosquitto is running: ``sudo systemctl status mosquitto``
- Check firewall: ``sudo ufw allow 1883``
- Test manually: ``mosquitto_sub -t 'weather/+/readings'``

### HTTP not reaching Pi
- Verify Pi hub is running: ``weather-station -c config.yaml run``
- Check Pi IP and port 8080
- Test with curl from another machine

### Battery dies quickly
- Enable ``ENABLE_DEEP_SLEEP = True``
- Reduce ``SAMPLE_INTERVAL_SECONDS`` to 600 (10 min) or 900 (15 min)
- Remove unnecessary sensors
- Use a larger battery (2×18650 in parallel)

## Multi-Node Setup

Deploy multiple ESP32 nodes (garden, greenhouse, roof) with unique
``STATION_ID`` values:

| Node | Location | Sensors |
|------|----------|---------|
| esp32-01 | Garden | BME280 + rain + light |
| esp32-02 | Greenhouse | BME280 + DS18B20 soil |
| esp32-03 | Roof | Anemometer + BME280 |

All nodes push to the same Pi hub.  The dashboard shows each node's
readings namespaced as ``esp32-01/bme280``, ``esp32-02/bme280``, etc.
