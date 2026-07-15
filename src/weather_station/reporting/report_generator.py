"""
Report generator: daily and weekly weather summaries.

WHY summaries:
  Raw readings are great for charts, but humans want digestible
  summaries: "Today's high was 28°C, low 12°C, average 20°C. 3.2mm of
  rain."  The report generator aggregates raw readings into daily
  stats and stores them in the daily_summaries table.

NOTE: Reports are generated on-demand or via a daily cron job.  They
do not block the recording loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from weather_station.core.database import WeatherDatabase

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate daily and weekly weather summaries from raw readings."""

    def __init__(self, db: WeatherDatabase, station_id: str = "ws01") -> None:
        self.db = db
        self.station_id = station_id

    def generate_daily_report(self, date: str | None = None) -> dict[str, Any]:
        """Generate a daily summary for the given date (YYYY-MM-DD).

        If date is None, uses yesterday's date (so the full day is complete).
        Stores results in daily_summaries and returns the report dict.
        """
        if date is None:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            date = yesterday.strftime("%Y-%m-%d")

        # Query all readings for this date
        start_ts = f"{date}T00:00:00"
        end_ts = f"{date}T23:59:59"
        readings = self.db.get_readings(
            station_id=self.station_id,
            start_time=start_ts,
            end_time=end_ts,
            limit=100000,
        )

        if not readings:
            logger.info("No readings found for %s", date)
            return {"date": date, "station_id": self.station_id, "metrics": {}, "total_readings": 0}

        # Group by (sensor_name, metric) and compute stats
        groups: dict[str, list[float]] = {}
        for r in readings:
            key = f"{r['sensor_name']}.{r['metric']}"
            val = r.get("value")
            if val is not None:
                groups.setdefault(key, []).append(val)

        summaries: dict[str, dict[str, float]] = {}
        for key, values in groups.items():
            stats = {
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "count": len(values),
            }
            summaries[key] = stats
            # Store in database
            sensor_name, metric = key.split(".", 1)
            self.db.upsert_daily_summary(
                date=date,
                station_id=self.station_id,
                metric=key,
                min_value=stats["min"],
                max_value=stats["max"],
                avg_value=stats["avg"],
                count=stats["count"],
            )

        report = {
            "date": date,
            "station_id": self.station_id,
            "metrics": summaries,
            "total_readings": len(readings),
        }
        logger.info("Daily report for %s: %d metrics summarized", date, len(summaries))
        return report

    def generate_weekly_report(self, end_date: str | None = None) -> dict[str, Any]:
        """Generate a weekly summary ending on the given date."""
        if end_date is None:
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        end = datetime.strptime(end_date, "%Y-%m-%d")
        start = end - timedelta(days=6)
        start_str = start.strftime("%Y-%m-%d")

        daily_reports: list[dict[str, Any]] = []
        for i in range(7):
            day = start + timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            report = self.generate_daily_report(day_str)
            daily_reports.append(report)

        # Aggregate weekly stats from daily summaries
        weekly: dict[str, dict[str, float]] = {}
        for report in daily_reports:
            for key, stats in report.get("metrics", {}).items():
                if key not in weekly:
                    weekly[key] = {"min": stats["min"], "max": stats["max"],
                                   "sum": 0.0, "count": 0}
                weekly[key]["min"] = min(weekly[key]["min"], stats["min"])
                weekly[key]["max"] = max(weekly[key]["max"], stats["max"])
                weekly[key]["sum"] += stats["avg"] * stats["count"]
                weekly[key]["count"] += stats["count"]

        for key in weekly:
            weekly[key]["avg"] = weekly[key]["sum"] / max(1, weekly[key]["count"])
            del weekly[key]["sum"]

        return {
            "start_date": start_str,
            "end_date": end_date,
            "station_id": self.station_id,
            "daily_reports": daily_reports,
            "weekly_summary": weekly,
        }

    def format_human_readable(self, report: dict[str, Any]) -> str:
        """Format a daily report as human-readable text (for CLI/SMS output)."""
        lines: list[str] = []
        date = report.get("date", "unknown")
        lines.append(f"=== Weather Report for {date} ===")
        lines.append(f"Station: {report.get('station_id', 'unknown')}")
        lines.append(f"Total readings: {report.get('total_readings', 0)}")
        lines.append("")

        metrics = report.get("metrics", {})
        # Group by sensor for readability
        by_sensor: dict[str, list[str]] = {}
        for key, stats in sorted(metrics.items()):
            sensor, metric = key.split(".", 1)
            by_sensor.setdefault(sensor, []).append(
                f"  {metric}: min={stats['min']:.1f}  max={stats['max']:.1f}  "
                f"avg={stats['avg']:.1f}  (n={stats['count']})"
            )

        for sensor in sorted(by_sensor):
            lines.append(f"[{sensor}]")
            lines.extend(by_sensor[sensor])
            lines.append("")

        return "\n".join(lines)