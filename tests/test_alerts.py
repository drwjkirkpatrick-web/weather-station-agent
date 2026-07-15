"""Tests for the alert engine."""

import pytest
from weather_station.core.database import WeatherDatabase
from weather_station.alerts.alert_engine import AlertEngine, AlertRule


class TestAlertRule:
    """Test individual alert rule evaluation."""

    def test_gt_operator(self):
        rule = AlertRule("high_temp", "bme680", "temperature_c", "gt", 35.0)
        assert rule.evaluate(40.0) is True
        assert rule.evaluate(30.0) is False

    def test_lt_operator(self):
        rule = AlertRule("low_temp", "bme680", "temperature_c", "lt", -10.0)
        assert rule.evaluate(-15.0) is True
        assert rule.evaluate(0.0) is False

    def test_gte_operator(self):
        rule = AlertRule("test", "bme680", "temperature_c", "gte", 35.0)
        assert rule.evaluate(35.0) is True
        assert rule.evaluate(34.9) is False

    def test_lte_operator(self):
        rule = AlertRule("test", "bme680", "temperature_c", "lte", 0.0)
        assert rule.evaluate(0.0) is True
        assert rule.evaluate(0.1) is False

    def test_unknown_operator(self):
        rule = AlertRule("test", "bme680", "temperature_c", "unknown", 35.0)
        assert rule.evaluate(40.0) is False

    def test_format_message(self):
        rule = AlertRule("test", "bme680", "temperature_c", "gt", 35.0,
                         message="High temp: {value}C > {threshold}C")
        msg = rule.format_message(40.0)
        assert "40.0" in msg
        assert "35.0" in msg

    def test_format_default_message(self):
        rule = AlertRule("test", "bme680", "temperature_c", "gt", 35.0)
        msg = rule.format_message(40.0)
        assert "40.0" in msg
        assert "35.0" in msg


class TestAlertEngine:
    """Test the alert engine with a real database."""

    def test_check_readings_triggers_alert(self, db):
        """A reading exceeding a threshold should trigger an alert."""
        engine = AlertEngine(db, [
            AlertRule("high_temp", "bme680", "temperature_c", "gt", 35.0,
                      severity="warning", message="High temp: {value}C"),
        ])
        readings = [
            {"sensor_name": "bme680", "metric": "temperature_c", "value": 40.0,
             "station_id": "test01"},
        ]
        triggered = engine.check_readings(readings)
        assert len(triggered) == 1
        assert triggered[0]["rule"] == "high_temp"
        assert triggered[0]["severity"] == "warning"

    def test_check_readings_no_trigger(self, db):
        """A reading below threshold should not trigger."""
        engine = AlertEngine(db, [
            AlertRule("high_temp", "bme680", "temperature_c", "gt", 35.0),
        ])
        readings = [
            {"sensor_name": "bme680", "metric": "temperature_c", "value": 20.0,
             "station_id": "test01"},
        ]
        triggered = engine.check_readings(readings)
        assert len(triggered) == 0

    def test_cooldown_prevents_duplicate(self, db):
        """Repeated triggers within cooldown should be suppressed."""
        engine = AlertEngine(db, [
            AlertRule("high_temp", "bme680", "temperature_c", "gt", 35.0,
                      cooldown_seconds=3600),
        ])
        readings = [
            {"sensor_name": "bme680", "metric": "temperature_c", "value": 40.0,
             "station_id": "test01"},
        ]
        # First trigger should fire
        triggered1 = engine.check_readings(readings)
        assert len(triggered1) == 1
        # Second trigger within cooldown should be suppressed
        triggered2 = engine.check_readings(readings)
        assert len(triggered2) == 0

    def test_alert_stored_in_db(self, db):
        """Triggered alerts should be stored in the database."""
        engine = AlertEngine(db, [
            AlertRule("high_pm25", "pms5003", "pm2_5_ugm3", "gt", 35.0,
                      severity="critical"),
        ])
        readings = [
            {"sensor_name": "pms5003", "metric": "pm2_5_ugm3", "value": 50.0,
             "station_id": "test01"},
        ]
        engine.check_readings(readings)
        alerts = db.get_alerts("test01")
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"

    def test_multiple_rules_evaluated(self, db):
        """Multiple rules for different sensors should all be checked."""
        engine = AlertEngine(db, [
            AlertRule("high_temp", "bme680", "temperature_c", "gt", 35.0),
            AlertRule("high_wind", "anemometer", "wind_speed_mps", "gt", 15.0),
        ])
        readings = [
            {"sensor_name": "bme680", "metric": "temperature_c", "value": 40.0,
             "station_id": "test01"},
            {"sensor_name": "anemometer", "metric": "wind_speed_mps", "value": 20.0,
             "station_id": "test01"},
        ]
        triggered = engine.check_readings(readings)
        assert len(triggered) == 2

    def test_from_config(self, db, default_config):
        """from_config should create an engine with default rules."""
        engine = AlertEngine.from_config(db, default_config.alerts)
        assert len(engine.rules) == 10  # 10 default rules
        rule_names = [r.name for r in engine.rules]
        assert "high_temp" in rule_names
        assert "high_pm25" in rule_names
        assert "heavy_rain" in rule_names

    def test_add_rule(self, db):
        engine = AlertEngine(db)
        assert len(engine.rules) == 0
        engine.add_rule(AlertRule("custom", "bme680", "temperature_c", "gt", 40.0))
        assert len(engine.rules) == 1