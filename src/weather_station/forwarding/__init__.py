"""Data forwarding module — push weather data to online services.

WHY a dedicated forwarding module:
  The weather station already records data locally (SQLite), serves a
  dashboard, and triggers alerts.  Forwarding extends the station's
  usefulness by contributing its data to online forecasting and
  citizen-science networks:

  - **Weather Underground PWS** — global PWS network, real-time display
  - **CWOP** — feeds NOAA MADIS → NWS forecast models
  - **WeatherCloud** — social weather network with maps and graphs
  - **OpenWeatherMap** — global weather data API, station API 3.0

  Each service is a pluggable adapter.  The DataForwarder runs in its
  own background thread, reads the latest readings from the database,
  converts to each service's required format/units, and pushes.
"""

from weather_station.forwarding.forwarder import DataForwarder

__all__ = ["DataForwarder"]
