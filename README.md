# weather-station-agent

<p align="center">
  <strong>v0.1.0</strong> · Comprehensive weather measurement and recording agent for Raspberry Pi Zero 2 W
</p>

An open-source weather station agent that reads 11 different sensor types, records data to SQLite, serves a real-time web dashboard, generates daily/weekly reports, and triggers threshold-based alerts — all on a $15 Raspberry Pi Zero 2 W.

---

## Overview

| Domain | Highlights |
|--------|------------|
| Sensors | 11 supported types (I2C, UART, GPIO, ADC) |
| Measurements | Temperature, humidity, pressure, air quality, particulate matter, wind speed/direction, rainfall, light |
| Recording | SQLite with WAL mode, batch inserts, configurable retention |
| Alerts | 10 default rules (high temp, freeze, high wind, heavy rain, poor air quality, etc.) |
| Reporting | Daily and weekly summaries with min/max/avg |
| Dashboard | Flask web UI, auto-refresh, dark theme, mobile-friendly |
| Forwarding | Push to Wunderground PWS, CWOP, WeatherCloud, OpenWeatherMap |
| CLI | Full-featured CLI for status, reads, exports, reports, forwarding |
| Mock Mode | 100% functional without hardware — develop and test anywhere |
| Tests | 159 tests, all passing |

---

## Supported Sensors

| # | Sensor | Bus | Measures | Cost | Python Library |
|---|--------|-----|----------|------|----------------|
| 1 | **BME680** | I2C | Temp, humidity, pressure, gas/IAQ | $15-20 | `adafruit_bme680` |
| 2 | **BME280** | I2C | Temp, humidity, pressure | $5-10 | `adafruit_bme280` |
| 3 | **SHT31** | I2C | High-accuracy temp + humidity | $10-15 | `adafruit_sht31d` |
| 4 | **SGP30** | I2C | CO2-equivalent, TVOC | $10-15 | `adafruit_sgp30` |
| 5 | **PMS5003** | UART | PM1.0, PM2.5, PM10, particle counts | $15-25 | `pyserial` |
| 6 | **VEML7700** | I2C | Ambient light, UV index | $5-10 | `adafruit_veml7700` |
| 7 | **DS3231** | I2C | RTC (accurate timestamps) + temp | $3-5 | `adafruit_ds3231` |
| 8 | **Anemometer** | GPIO | Wind speed (m/s) | $15-30 | `RPi.GPIO` |
| 9 | **Wind Vane** | ADC | Wind direction (16-point compass) | $15-30 | `adafruit_mcp3xxx` |
| 10 | **Rain Gauge** | GPIO | Rainfall (mm) + rate (mm/h) | $15-30 | `RPi.GPIO` |
| 11 | **MQ-135** | ADC | CO2 estimate, air quality index | $5 | `adafruit_mcp3xxx` |

---

## Quick Start

### Development (no hardware needed)

```bash
git clone https://github.com/drwjkirkpatrick-web/weather-station-agent.git
cd weather-station-agent
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run in mock mode (simulates all sensors)
weather-station --mock run

# Or take a single reading
weather-station --mock read

# Check sensor status
weather-station --mock status
```

### Production (on Raspberry Pi Zero 2 W)

```bash
git clone https://github.com/drwjkirkpatrick-web/weather-station-agent.git
cd weather-station-agent

# Install with hardware dependencies
pip install -e ".[dev,hardware]"

# Copy and edit config
cp config.yaml my-config.yaml
# Edit my-config.yaml to match your wiring

# Run the agent
weather-station -c my-config.yaml run

# Or run directly
python -m weather_station.main -c my-config.yaml
```

### systemd Service

```bash
sudo cp deploy/weather-station.service /etc/systemd/system/
sudo systemctl enable weather-station
sudo systemctl start weather-station
sudo journalctl -u weather-station -f
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `weather-station run` | Start the full agent (recorder + dashboard) |
| `weather-station status` | Show sensor health and database stats |
| `weather-station read` | Take a single reading from all sensors |
| `weather-station export -f csv -o data.csv` | Export readings to CSV |
| `weather-station export -f json -o data.json` | Export readings to JSON |
| `weather-station report` | Generate a daily report |
| `weather-station report -w` | Generate a weekly report |
| `weather-station alert-rules` | List configured alert rules |
| `weather-station forward-status` | Show data forwarding service status |
| `weather-station forward-status -t` | Test-push to enabled forwarding services |

### Global Flags

| Flag | Description |
|------|-------------|
| `-c, --config` | Path to YAML config file |
| `--mock` | Force mock mode (no hardware needed) |
| `-v, --verbose` | Enable debug logging |

---

## Web Dashboard

The dashboard runs on port 8080 by default:

```
http://<pi-ip-address>:8080
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/current` | Latest reading for each sensor |
| `GET /api/history?sensor=bme680&metric=temperature_c&hours=24` | Time-series data |
| `GET /api/alerts?limit=20` | Recent alerts |
| `GET /api/health` | Database table stats |
| `GET /api/daily-summary?date=2024-01-15` | Daily summary |
| `GET /health` | Health check |

---

## Configuration

All configuration is in `config.yaml`. See [config.yaml](config.yaml) for the full reference.

Key sections:

```yaml
station_name: "weather-station-01"
station_id: "ws01"
mock_mode: false

sensors:
  bme680_enabled: true
  pms5003_enabled: true
  # ... (enable/disable each sensor)

recording:
  sample_interval_seconds: 60
  retention_days: 365

alerts:
  high_temp_c: 35.0
  low_temp_c: -10.0
  high_wind_mps: 15.0
  heavy_rain_mmh: 25.0
  high_pm25_ugm3: 35.0
```

---

## Architecture

```
                    +-----------+
                    |   CLI     | <-- weather-station command
                    +-----------+
                         |
                    +-----------+
                    |  Main     | <-- orchestrator (main.py)
                    |  Agent    |
                    +-----------+
                    /    |    \    \
              +------+   |   +------+  +--------+
              |Recorder|  |   |Dashboard| |Forwarder|
              +------+   |   +------+  +--------+
                    /    |      |        /     \
              +------+   |   +------+ +---+ +---+
              |Sensors|  |   |SQLite | |WU | |CWOP|
              +------+   |   +------+ +---+ +---+
                    |    |      |        /     \
              +------+   |   +------+ +---+ +---+
              |Alerts |  |   |Reports| |WC | |OWM |
              +------+  |   +------+ +---+ +---+
                        |
                   +---------+
                   |DataExporter|
                   +---------+
```

### Project Structure

```
weather-station-agent/
|-- pyproject.toml           # Package metadata, deps, tool config
|-- config.yaml              # Sample configuration
|-- src/weather_station/
|   |-- __init__.py
|   |-- cli.py               # Command-line interface
|   |-- main.py              # Main orchestrator
|   |-- core/
|   |   |-- config.py        # Configuration (frozen dataclasses)
|   |   |-- database.py      # SQLite layer (WAL, thread-safe)
|   |   |-- sensor_base.py   # Abstract sensor base class
|   |   |-- mock_manager.py  # Mock data generator
|   |-- sensors/
|   |   |-- bme680.py        # BME680 (temp/humidity/pressure/gas)
|   |   |-- bme280.py        # BME280 (temp/humidity/pressure)
|   |   |-- sht31.py         # SHT31 (high-accuracy temp/humidity)
|   |   |-- sgp30.py         # SGP30 (CO2-eq/TVOC)
|   |   |-- pms5003.py       # PMS5003 (particulate matter)
|   |   |-- veml7700.py      # VEML7700 (light/UV)
|   |   |-- ds3231.py        # DS3231 (RTC + temperature)
|   |   |-- anemometer.py    # Wind speed (GPIO)
|   |   |-- wind_vane.py     # Wind direction (ADC)
|   |   |-- rain_gauge.py    # Rainfall (GPIO)
|   |   |-- mq135.py         # Air quality (ADC)
|   |-- recording/
|   |   |-- data_recorder.py  # Periodic sensor polling + storage
|   |   |-- exporter.py       # CSV/JSON export
|   |-- alerts/
|   |   |-- alert_engine.py   # Threshold-based alerting
|   |-- reporting/
|   |   |-- report_generator.py # Daily/weekly summaries
|   |-- forwarding/
|   |   |-- forwarder.py      # Background thread — pushes to services
|   |   |-- service_base.py   # ForwardingServiceBase (like SensorBase)
|   |   |-- services/
|   |       |-- wunderground.py   # Weather Underground PWS (HTTP GET)
|   |       |-- cwop.py           # CWOP / NOAA MADIS (TCP APRS)
|   |       |-- weathercloud.py  # WeatherCloud (HTTP GET, ×10 metric)
|   |       |-- openweathermap.py # OpenWeatherMap 3.0 (HTTP POST JSON)
|   |-- web/
|       |-- dashboard.py      # Flask web dashboard
|-- tests/                    # 159 tests
|-- deploy/
    |-- weather-station.service  # systemd unit file
```

---

## Wiring Guide (Pi Zero 2 W)

### I2C Sensors (bus 1)

| Sensor | I2C Address | Pi Pins |
|--------|------------|---------|
| BME680 | 0x77 | SDA=GPIO2 (pin 3), SCL=GPIO3 (pin 5) |
| BME280 | 0x76 | Same bus |
| SHT31 | 0x44 | Same bus |
| SGP30 | 0x58 | Same bus |
| VEML7700 | 0x10 | Same bus |
| DS3231 | 0x68 | Same bus |

### Serial Sensor

| Sensor | Pi Pins |
|--------|---------|
| PMS5003 | TX=GPIO14 (pin 8), RX=GPIO15 (pin 10) |

### GPIO Sensors

| Sensor | GPIO Pin | Pi Pin |
|--------|----------|--------|
| Anemometer | GPIO4 | Pin 7 |
| Rain Gauge | GPIO17 | Pin 11 |

### ADC Sensors (via MCP3008)

| Sensor | ADC Channel | SPI |
|--------|-------------|-----|
| Wind Vane | CH0 | MOSI=GPIO10, MISO=GPIO9, SCLK=GPIO11, CS=GPIO8 |
| MQ-135 | CH1 | Same SPI bus |

---

## Mock Mode

The agent runs fully in mock mode without any hardware. This is useful for:

- Development on a non-Pi machine
- Testing the full pipeline (sensors -> recording -> alerts -> dashboard)
- CI/CD pipelines

```bash
# Run everything in mock mode
weather-station --mock run

# The mock data includes:
# - Diurnal temperature cycle (cooler at night, warmer at day)
# - Humidity inversely correlated with temperature
# - Light follows daylight cycle
# - Random-walk variation for all metrics
```

---

## Data Forwarding (Optional)

The station can optionally push weather data to online forecasting and citizen-science networks. This extends the station's usefulness beyond local recording — your data contributes to global weather models and public displays.

All forwarding is **opt-in** and **off by default**. No external Python dependencies (uses only stdlib `urllib` and `socket`).

### Supported Services

| Service | Protocol | Units | Impact |
|---------|----------|-------|--------|
| **Weather Underground PWS** | HTTP GET | Imperial (°F, mph, inHg) | Global PWS network, real-time display |
| **CWOP** | TCP APRS | Mixed (°F, mph, tenths-mb) | Feeds NOAA MADIS → NWS forecast models |
| **WeatherCloud** | HTTP GET | Metric ×10 | Social weather network, maps & graphs |
| **OpenWeatherMap** | HTTP POST JSON | Metric | Global weather data API, Station API 3.0 |

### Setup

1. **Register** your station with the service(s) you want to use:
   - Wunderground: https://www.wunderground.com/pws/
   - CWOP: http://wxqa.com/
   - WeatherCloud: https://weathercloud.net/
   - OpenWeatherMap: https://openweathermap.org/stations

2. **Edit** `config.yaml` — enable the `forwarding` section and fill in your credentials:

```yaml
forwarding:
  enabled: true                    # master switch
  forward_interval_seconds: 300    # push every 5 min (CWOP minimum)
  timeout_seconds: 30

  # Enable only the services you've registered with:
  wunderground_enabled: true
  wunderground_station_id: "KCASANFR5"
  wunderground_password: "your-station-key"

  cwop_enabled: true
  cwop_station_id: "EW9876"
  # cwop_server: "cwop.aprs.net"   # default
  # cwop_port: 14580                # default
```

3. **Test** your configuration in mock mode (no hardware needed):

```bash
# Check forwarding status
weather-station --mock forward-status

# Trigger a test push to all enabled services
weather-station --mock forward-status -t
```

4. **Deploy** — the forwarder starts automatically with the agent. It runs in its own background thread, reading the latest readings from the database and pushing to each enabled service at the configured interval.

### Architecture

The forwarding module follows the same pluggable-adapter pattern as the sensor drivers:

- **`ForwardingServiceBase`** — abstract base class (like `SensorBase`)
- **`DataForwarder`** — background thread orchestrator (like `DataRecorder`)
- **Service adapters** — one per service, each handles:
  - `format_payload()` — convert readings to the service's wire format + units
  - `send()` — transmit via HTTP GET, HTTP POST JSON, or raw TCP

The forwarder reads the latest readings from the SQLite database (not directly from sensors), so it's fully decoupled from the recording loop. If a remote service is down or slow, local recording continues without interruption.

### Adding a New Service

To add a new forwarding target:

1. Create `src/weather_station/forwarding/services/yourservice.py`
2. Subclass `ForwardingServiceBase` — implement `is_enabled()`, `format_payload()`, `send()`
3. Add config fields to `ForwardingConfig` in `core/config.py`
4. Register the adapter in `forwarding/services/__init__.py`
5. Add it to `_build_services()` in `forwarder.py`
6. Write tests in `tests/test_forwarding.py`

---

## License

MIT

---

## Author

Walker Kirkpatrick
GitHub: [drwjkirkpatrick-web](https://github.com/drwjkirkpatrick-web)