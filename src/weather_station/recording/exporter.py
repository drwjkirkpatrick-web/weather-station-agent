"""
Data exporter: export weather readings to CSV and JSON formats.

WHY a dedicated exporter:
  Users will want to pull data out for analysis in spreadsheets, weather
  networks, or archive.  Having a clean export interface keeps format
  logic out of the database layer.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from weather_station.core.database import WeatherDatabase


class DataExporter:
    """Export weather data to CSV or JSON files."""

    def __init__(self, db: WeatherDatabase) -> None:
        self.db = db

    def export_csv(
        self,
        output_path: str | Path,
        sensor_name: str | None = None,
        metric: str | None = None,
        station_id: str = "ws01",
        limit: int = 10000,
    ) -> int:
        """Export readings to a CSV file. Returns the number of rows written.

        Columns: timestamp, station_id, sensor_name, metric, value, unit
        """
        readings = self.db.get_readings(
            sensor_name=sensor_name,
            metric=metric,
            station_id=station_id,
            limit=limit,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "timestamp", "station_id", "sensor_name", "metric", "value", "unit",
            ])
            writer.writeheader()
            for r in readings:
                writer.writerow({
                    "timestamp": r["timestamp"],
                    "station_id": r["station_id"],
                    "sensor_name": r["sensor_name"],
                    "metric": r["metric"],
                    "value": r["value"],
                    "unit": r["unit"],
                })
        return len(readings)

    def export_json(
        self,
        output_path: str | Path,
        sensor_name: str | None = None,
        metric: str | None = None,
        station_id: str = "ws01",
        limit: int = 10000,
    ) -> int:
        """Export readings to a JSON file. Returns count of readings exported."""
        readings = self.db.get_readings(
            sensor_name=sensor_name,
            metric=metric,
            station_id=station_id,
            limit=limit,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(readings, f, indent=2, default=str)
        return len(readings)

    def export_daily_summary_csv(
        self,
        output_path: str | Path,
        station_id: str = "ws01",
    ) -> int:
        """Export daily summaries to CSV. Returns count of rows."""
        summaries = self.db.get_daily_summaries(station_id=station_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "date", "station_id", "metric", "min_value", "max_value", "avg_value", "count",
            ], extrasaction="ignore")
            writer.writeheader()
            for s in summaries:
                writer.writerow(s)
        return len(summaries)