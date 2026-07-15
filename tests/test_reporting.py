"""Tests for the report generator."""

from datetime import datetime, timedelta, timezone

import pytest

from weather_station.core.database import WeatherDatabase
from weather_station.reporting.report_generator import ReportGenerator


class TestReportGenerator:
    """Test daily and weekly report generation."""

    def test_empty_report(self, db):
        """If no readings exist, report should have empty metrics."""
        gen = ReportGenerator(db, "test01")
        report = gen.generate_daily_report("2024-01-15")
        assert report["date"] == "2024-01-15"
        assert report["metrics"] == {}

    def test_daily_report_with_data(self, db):
        """Daily report should aggregate readings into min/max/avg."""
        # Insert readings for a single day
        for i in range(24):
            db.insert_reading(
                timestamp=f"2024-01-15T{i:02d}:00:00+00:00",
                station_id="test01",
                sensor_name="bme680",
                metric="temperature_c",
                value=10.0 + i,
            )
        gen = ReportGenerator(db, "test01")
        report = gen.generate_daily_report("2024-01-15")
        assert report["total_readings"] == 24
        key = "bme680.temperature_c"
        assert key in report["metrics"]
        stats = report["metrics"][key]
        assert stats["min"] == 10.0
        assert stats["max"] == 33.0
        assert stats["count"] == 24

    def test_daily_summary_stored(self, db):
        """Daily summary should be persisted to the database."""
        for i in range(5):
            db.insert_reading(
                timestamp=f"2024-01-15T{10+i}:00:00+00:00",
                station_id="test01",
                sensor_name="bme680",
                metric="temperature_c",
                value=20.0 + i,
            )
        gen = ReportGenerator(db, "test01")
        gen.generate_daily_report("2024-01-15")
        summaries = db.get_daily_summaries(date="2024-01-15", station_id="test01")
        assert len(summaries) >= 1

    def test_weekly_report(self, db):
        """Weekly report should cover 7 days."""
        for day in range(7):
            date_str = f"2024-01-{15+day:02d}"
            for i in range(3):
                db.insert_reading(
                    timestamp=f"{date_str}T10:0{i}:00+00:00",
                    station_id="test01",
                    sensor_name="bme680",
                    metric="temperature_c",
                    value=15.0 + day,
                )
        gen = ReportGenerator(db, "test01")
        report = gen.generate_weekly_report("2024-01-21")
        assert report["start_date"] == "2024-01-15"
        assert report["end_date"] == "2024-01-21"
        assert len(report["daily_reports"]) == 7
        assert "bme680.temperature_c" in report["weekly_summary"]

    def test_format_human_readable(self, db):
        """Human-readable report should contain key sections."""
        db.insert_reading(
            timestamp="2024-01-15T10:00:00+00:00",
            station_id="test01",
            sensor_name="bme680",
            metric="temperature_c",
            value=22.0,
        )
        gen = ReportGenerator(db, "test01")
        report = gen.generate_daily_report("2024-01-15")
        text = gen.format_human_readable(report)
        assert "Weather Report" in text
        assert "bme680" in text
        assert "temperature_c" in text