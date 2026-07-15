"""
Flask web dashboard for real-time weather visualization.

WHY a single-file Flask app:
  The Pi Zero 2 W has 512 MB RAM.  A single-file Flask app with inline
  HTML is the lightest viable web UI.  No build step, no frontend
  framework, no node_modules.  The dashboard polls a JSON API endpoint
  and renders with vanilla JS + CSS Grid.

NOTE: The dashboard is read-only — it queries the database directly.
  It does not interfere with the recording loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, render_string, request

from weather_station.core.database import WeatherDatabase

logger = logging.getLogger(__name__)


def create_app(db: WeatherDatabase, station_id: str = "ws01") -> Flask:
    """Create and return a configured Flask app.

    Args:
        db: WeatherDatabase instance (shared with the recorder)
        station_id: station ID for filtering readings
    """
    app = Flask(__name__)
    app.config["DB"] = db
    app.config["STATION_ID"] = station_id

    # ── API endpoints ──────────────────────────────────────────────

    @app.route("/api/current")
    def api_current() -> str:
        """Return the latest reading for each sensor+metric."""
        readings = db.get_latest_readings(station_id)
        # Group by sensor
        by_sensor: dict[str, dict[str, Any]] = {}
        for r in readings:
            sensor = r["sensor_name"]
            if sensor not in by_sensor:
                by_sensor[sensor] = {"timestamp": r["timestamp"], "metrics": {}}
            by_sensor[sensor]["metrics"][r["metric"]] = {
                "value": r["value"],
                "unit": r["unit"],
            }
        return jsonify({
            "station_id": station_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sensors": by_sensor,
        })

    @app.route("/api/history")
    def api_history() -> str:
        """Return time-series data for a specific sensor+metric.

        Query params: sensor, metric, hours (default 24)
        """
        sensor = request.args.get("sensor", "")
        metric = request.args.get("metric", "")
        hours = int(request.args.get("hours", 24))
        if not sensor or not metric:
            return jsonify({"error": "sensor and metric params required"}), 400
        data = db.get_readings_series(sensor, metric, station_id, hours)
        return jsonify(data)

    @app.route("/api/alerts")
    def api_alerts() -> str:
        """Return recent alerts."""
        limit = int(request.args.get("limit", 50))
        alerts = db.get_alerts(station_id, limit)
        return jsonify(alerts)

    @app.route("/api/health")
    def api_health() -> str:
        """Return database table stats."""
        stats = db.get_table_stats()
        return jsonify({
            "status": "ok",
            "tables": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/api/daily-summary")
    def api_daily_summary() -> str:
        """Return daily summaries."""
        date = request.args.get("date")
        summaries = db.get_daily_summaries(date, station_id)
        return jsonify(summaries)

    # ── Dashboard page ──────────────────────────────────────────────

    @app.route("/")
    def dashboard() -> str:
        return render_string(DASHBOARD_HTML)

    @app.route("/health")
    def health() -> str:
        return jsonify({"status": "ok"})

    return app


# ── Inline HTML dashboard ───────────────────────────────────────────
# NOTE: Kept simple for Pi Zero 2 W — no external dependencies, no CDNs.
#       All rendering happens client-side via vanilla JS polling the API.

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weather Station Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #1a1a2e; color: #e0e0e0; padding: 20px;
  }
  h1 { color: #0f3460; background: #16213e; padding: 15px; border-radius: 8px; margin-bottom: 20px; text-align: center; }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 15px; margin-bottom: 20px;
  }
  .card {
    background: #16213e; border-radius: 8px; padding: 15px; border: 1px solid #0f3460;
  }
  .card h2 { color: #e94560; font-size: 14px; text-transform: uppercase; margin-bottom: 10px; }
  .metric { display: flex; justify-content: space-between; padding: 4px 0; font-size: 14px; }
  .metric .label { color: #a0a0a0; }
  .metric .value { color: #0f3460; font-weight: bold; }
  .metric .value { color: #4ecca3; }
  .timestamp { color: #666; font-size: 11px; text-align: center; margin-top: 10px; }
  .alerts { background: #1a1a2e; border: 1px solid #e94560; border-radius: 8px; padding: 15px; margin-top: 20px; }
  .alert-item { padding: 8px; border-bottom: 1px solid #333; font-size: 13px; }
  .alert-item .sev-critical { color: #e94560; font-weight: bold; }
  .alert-item .sev-warning { color: #f0a500; }
  .alert-item .sev-info { color: #4ecca3; }
  .refresh-info { text-align: center; color: #666; font-size: 12px; margin-bottom: 15px; }
</style>
</head>
<body>
<h1>Weather Station</h1>
<div class="refresh-info">Auto-refresh every 60s | <span id="last-update">--</span></div>
<div class="grid" id="sensor-grid"></div>
<div class="alerts">
  <h2 style="color: #e94560;">Recent Alerts</h2>
  <div id="alert-list"></div>
</div>
<script>
function fetchJSON(url) {
  return fetch(url).then(r => r.json());
}

function fmt(val, unit) {
  if (val === null || val === undefined) return '--';
  return val.toFixed(1) + (unit ? ' ' + unit : '');
}

async function updateDashboard() {
  try {
    const data = await fetchJSON('/api/current');
    const grid = document.getElementById('sensor-grid');
    grid.innerHTML = '';

    const sensors = data.sensors || {};
    for (const [name, info] of Object.entries(sensors)) {
      const card = document.createElement('div');
      card.className = 'card';
      let html = '<h2>' + name + '</h2>';
      for (const [metric, m] of Object.entries(info.metrics)) {
        html += '<div class="metric"><span class="label">' + metric +
                '</span><span class="value">' + fmt(m.value, m.unit) +
                '</span></div>';
      }
      html += '<div class="timestamp">' + info.timestamp + '</div>';
      card.innerHTML = html;
      grid.appendChild(card);
    }

    const alerts = await fetchJSON('/api/alerts?limit=20');
    const alertList = document.getElementById('alert-list');
    alertList.innerHTML = '';
    if (alerts.length === 0) {
      alertList.innerHTML = '<div class="alert-item">No recent alerts</div>';
    } else {
      for (const a of alerts) {
        const div = document.createElement('div');
        div.className = 'alert-item';
        div.innerHTML = '<span class="sev-' + a.severity + '">[' + a.severity.toUpperCase() + ']</span> ' +
                        a.timestamp + ' | ' + a.message;
        alertList.appendChild(div);
      }
    }

    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
  } catch(e) {
    console.error('Dashboard update failed:', e);
  }
}

updateDashboard();
setInterval(updateDashboard, 60000);
</script>
</body>
</html>"""