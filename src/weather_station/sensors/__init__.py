"""Sensor driver modules for the weather station.

Each module wraps a single physical sensor behind a unified SensorBase
interface so the orchestrator can poll them interchangeably.

NOTE: Every driver degrades gracefully to mock mode when its hardware
library is not importable (e.g. developing on a non-Pi machine).  This
is controlled by MockManager in core/mock_manager.py.
"""

# Import sensor classes for convenient `from weather_station.sensors import ...`
# These imports are safe — each module handles its own optional deps.
from weather_station.sensors.bme680 import BME680Sensor
from weather_station.sensors.bme280 import BME280Sensor
from weather_station.sensors.sht31 import SHT31Sensor
from weather_station.sensors.pms5003 import PMS5003Sensor
from weather_station.sensors.sgp30 import SGP30Sensor
from weather_station.sensors.veml7700 import VEML7700Sensor
from weather_station.sensors.ds3231 import DS3231Sensor
from weather_station.sensors.anemometer import AnemometerSensor
from weather_station.sensors.wind_vane import WindVaneSensor
from weather_station.sensors.rain_gauge import RainGaugeSensor
from weather_station.sensors.mq135 import MQ135Sensor

__all__ = [
    "BME680Sensor",
    "BME280Sensor",
    "SHT31Sensor",
    "PMS5003Sensor",
    "SGP30Sensor",
    "VEML7700Sensor",
    "DS3231Sensor",
    "AnemometerSensor",
    "WindVaneSensor",
    "RainGaugeSensor",
    "MQ135Sensor",
]