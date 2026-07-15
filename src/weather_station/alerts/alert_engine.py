"""
Alert engine: threshold-based weather alerting.

WHY rule-based alerting:
  For a weather station, simple thresholds cover 95% of useful alerts:
  freeze warning, heat advisory, high wind, heavy rain, poor air quality.
  ML-based anomaly detection can be layered on later, but the base layer
  should be deterministic and explainable.

NOTE: The alert engine evaluates rules against every batch of readings
from the recorder.  Alerts are deduplicated: if the same rule fires
repeatedly within a cooldown window, only the first triggers a record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from weather_station.core.database import WeatherDatabase

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    """A single alert rule.

    Attributes:
        name: human-readable rule name
        sensor_name: which sensor to watch
        metric: which metric to watch
        operator: comparison operator ('gt', 'lt', 'gte', 'lte')
        threshold: the threshold value
        severity: 'info', 'warning', or 'critical'
        message: alert message template ({value} and {threshold} are substituted)
        cooldown_seconds: minimum time between repeated alerts (default 300s)
    """

    name: str
    sensor_name: str
    metric: str
    operator: str           # 'gt', 'lt', 'gte', 'lte'
    threshold: float
    severity: str = "warning"
    message: str = ""
    cooldown_seconds: int = 300

    def evaluate(self, value: float) -> bool:
        """Return True if the value triggers this rule."""
        ops: dict[str, Callable[[float, float], bool]] = {
            "gt": lambda v, t: v > t,
            "lt": lambda v, t: v < t,
            "gte": lambda v, t: v >= t,
            "lte": lambda v, t: v <= t,
        }
        op = ops.get(self.operator)
        if op is None:
            logger.warning("Unknown operator '%s' for rule '%s'", self.operator, self.name)
            return False
        return op(value, self.threshold)

    def format_message(self, value: float) -> str:
        """Format the alert message, substituting value and threshold."""
        if self.message:
            return self.message.format(value=value, threshold=self.threshold)
        return f"{self.metric} = {value} (threshold: {self.operator} {self.threshold})"


class AlertEngine:
    """Evaluate alert rules against incoming sensor readings.

    Usage:
        engine = AlertEngine(db, rules)
        engine.check_readings(readings)  # called by recorder after each cycle
    """

    def __init__(self, db: WeatherDatabase, rules: list[AlertRule] | None = None) -> None:
        self.db = db
        self.rules = rules or []
        # Track last-fired timestamp per rule name for cooldown
        self._last_fired: dict[str, float] = {}

    def add_rule(self, rule: AlertRule) -> None:
        """Add a new alert rule."""
        self.rules.append(rule)
        logger.info("Alert rule added: %s", rule.name)

    def check_readings(self, readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate all rules against a batch of readings.

        Returns a list of triggered alerts (also stored in the database).
        """
        triggered: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for reading in readings:
            sensor = reading.get("sensor_name", "")
            metric = reading.get("metric", "")
            value = reading.get("value")
            if value is None:
                continue

            for rule in self.rules:
                if rule.sensor_name != sensor or rule.metric != metric:
                    continue
                if not rule.evaluate(value):
                    continue

                # Check cooldown
                key = rule.name
                last = self._last_fired.get(key, 0)
                elapsed = now.timestamp() - last
                if elapsed < rule.cooldown_seconds:
                    continue  # suppress duplicate alert within cooldown

                # Fire alert
                msg = rule.format_message(value)
                alert_id = self.db.insert_alert(
                    timestamp=now.isoformat(),
                    station_id=reading.get("station_id", "ws01"),
                    sensor_name=sensor,
                    metric=metric,
                    value=value,
                    threshold=rule.threshold,
                    operator=rule.operator,
                    severity=rule.severity,
                    message=msg,
                )
                self._last_fired[key] = now.timestamp()
                triggered.append({
                    "id": alert_id,
                    "rule": rule.name,
                    "sensor": sensor,
                    "metric": metric,
                    "value": value,
                    "threshold": rule.threshold,
                    "severity": rule.severity,
                    "message": msg,
                })
                logger.warning("ALERT [%s] %s: %s", rule.severity, rule.name, msg)

        return triggered

    @classmethod
    def from_config(cls, db: WeatherDatabase, alert_config: Any) -> AlertEngine:
        """Build an AlertEngine with default rules from AlertConfig."""
        rules = [
            AlertRule("high_temp", "bme680", "temperature_c", "gt",
                      alert_config.high_temp_c, "warning",
                      "High temperature: {value}°C (threshold: >{threshold}°C)"),
            AlertRule("low_temp", "bme680", "temperature_c", "lt",
                      alert_config.low_temp_c, "warning",
                      "Low temperature: {value}°C (threshold: <{threshold}°C)"),
            AlertRule("high_humidity", "bme680", "humidity_pct", "gt",
                      alert_config.high_humidity_pct, "info",
                      "High humidity: {value}% (threshold: >{threshold}%)"),
            AlertRule("low_humidity", "bme680", "humidity_pct", "lt",
                      alert_config.low_humidity_pct, "info",
                      "Low humidity: {value}% (threshold: <{threshold}%)"),
            AlertRule("high_pressure", "bme680", "pressure_hpa", "gt",
                      alert_config.high_pressure_hpa, "info",
                      "High pressure: {value} hPa (threshold: >{threshold} hPa)"),
            AlertRule("low_pressure", "bme680", "pressure_hpa", "lt",
                      alert_config.low_pressure_hpa, "warning",
                      "Low pressure: {value} hPa (threshold: <{threshold} hPa)"),
            AlertRule("high_pm25", "pms5003", "pm2_5_ugm3", "gt",
                      alert_config.high_pm25_ugm3, "critical",
                      "High PM2.5: {value} μg/m³ (threshold: >{threshold} μg/m³)"),
            AlertRule("high_tvoc", "sgp30", "tvoc_ppb", "gt",
                      alert_config.high_tvoc_ppb, "warning",
                      "High TVOC: {value} ppb (threshold: >{threshold} ppb)"),
            AlertRule("high_wind", "anemometer", "wind_speed_mps", "gt",
                      alert_config.high_wind_mps, "warning",
                      "High wind: {value} m/s (threshold: >{threshold} m/s)"),
            AlertRule("heavy_rain", "rain_gauge", "rain_rate_mmh", "gt",
                      alert_config.heavy_rain_mmh, "warning",
                      "Heavy rain: {value} mm/h (threshold: >{threshold} mm/h)"),
        ]
        return cls(db, rules)