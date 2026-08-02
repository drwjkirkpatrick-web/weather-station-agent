"""Ingest subsystem: receive weather data from ESP32 and other remote nodes."""

from weather_station.ingest.api import create_ingest_blueprint

__all__ = ["create_ingest_blueprint"]
