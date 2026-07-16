"""Forwarding service adapters.

Each adapter converts a normalized reading dict (metric names as used
in the local database) into the wire format a specific online service
expects, then sends it.  All adapters inherit from ForwardingServiceBase.
"""

from weather_station.forwarding.services.cwop import CWOPService
from weather_station.forwarding.services.openweathermap import OpenWeatherMapService
from weather_station.forwarding.services.weathercloud import WeathercloudService
from weather_station.forwarding.services.wunderground import WundergroundService

__all__ = [
    "WundergroundService",
    "CWOPService",
    "WeathercloudService",
    "OpenWeatherMapService",
]
