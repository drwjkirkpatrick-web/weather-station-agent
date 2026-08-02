"""ESP32 configuration module.

All tunables live here so the user never has to edit main.py.
Copy ``config.py`` to the ESP32 root alongside ``main.py`` after editing.

NOTE: WiFi credentials and MQTT passwords are sensitive.  Store them in
``config.py`` and keep that file out of version control (it is listed in
``.gitignore`` by default).
"""

# ── Station identity ───────────────────────────────────────────
STATION_ID = "esp32-01"
STATION_NAME = "Garden Weather Node"
LATITUDE = 44.0          # decimal degrees
LONGITUDE = -123.0
ELEVATION_M = 150.0

# ── WiFi ─────────────────────────────────────────────────────────
WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"
WIFI_TIMEOUT_SECONDS = 30

# ── Network destination ────────────────────────────────────────
# The ESP32 can push data to the Pi hub via MQTT *or* HTTP.
# Pick one; MQTT is preferred for multiple nodes.

USE_MQTT = True
USE_HTTP = False          # fallback if no MQTT broker

# MQTT broker (usually the Pi hub running Mosquitto)
MQTT_BROKER = "192.168.1.50"
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASSWORD = ""
MQTT_TOPIC_TEMPLATE = "weather/{station_id}/readings"
MQTT_QOS = 1              # at-least-once delivery

# HTTP fallback — POST JSON to the Pi hub ingest endpoint
HTTP_ENDPOINT = "http://192.168.1.50:8080/api/ingest"
HTTP_TIMEOUT_SECONDS = 15

# ── Sampling & power ───────────────────────────────────────────
SAMPLE_INTERVAL_SECONDS = 300   # 5 minutes (battery-friendly)
DEEP_SLEEP_SECONDS = 300        # must match SAMPLE_INTERVAL_SECONDS
ENABLE_DEEP_SLEEP = True        # False for USB-powered bench testing
WAKEUP_SOURCE = "timer"         # "timer" | "ext0" (rain tip wakes early)

# ── Sensor enable flags ────────────────────────────────────────
BME280_ENABLED = True          # I2C — temp, humidity, pressure
DHT22_ENABLED = False            # One-wire — temp, humidity (cheaper)
DS18B20_ENABLED = False          # One-wire — waterproof temp probe
ANEMOMETER_ENABLED = False     # GPIO — wind speed
RAIN_GAUGE_ENABLED = False       # GPIO — rainfall
BH1750_ENABLED = False         # I2C — ambient light
MQ135_ENABLED = False           # ADC — air quality

# ── Pin / bus config ───────────────────────────────────────────
# Default pins for common ESP32 dev boards (e.g. ESP32-WROOM-32)

I2C_SDA_PIN = 21
I2C_SCL_PIN = 22
I2C_FREQ = 100000              # 100 kHz (stable on long wires)

ONEWIRE_PIN = 4                # DHT22 + DS18B20 shareable on one pin

ANEMOMETER_PIN = 14
RAIN_GAUGE_PIN = 12

ADC_PIN = 34                   # MQ-135 via voltage divider
ADC_ATTEN = None               # set in driver: ADC.ATTN_11DB
ADC_WIDTH = None               # set in driver: ADC.WIDTH_12BIT

# ── Sensor calibration ─────────────────────────────────────────
BME280_I2C_ADDRESS = 0x76    # 0x76 or 0x77
DS18B20_RESOLUTION = 12        # 9–12 bits (higher = slower)
ANEMOMETER_RADIUS_CM = 6.0
ANEMOMETER_CALIBRATION = 1.0
RAIN_BUCKET_ML = 0.2794        # ml per tip
MQ135_VIN = 3.3
MQ135_RL = 10000              # load resistor (ohms)
MQ135_R0 = 10000              # calibrated R0 in clean air

# ── Diagnostic ─────────────────────────────────────────────────
VERBOSE = False                # True → print every sensor read to UART
LED_STATUS_PIN = 2             # ESP32 onboard LED (GPIO2) — blink on TX
