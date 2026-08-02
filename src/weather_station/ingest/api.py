"""Ingest API for ESP32 (and other remote) weather nodes.

This module adds a Flask blueprint to the existing dashboard that accepts
POSTed JSON weather readings from ESP32 field nodes, validates them, and
stores them in the same SQLite database as locally-read sensors.

The ESP32 and Pi Zero 2 W sensor names may overlap (e.g. both have a
bme280).  To avoid collisions, remote-node readings are prefixed in the
database: ``esp32-01/bme280`` instead of just ``bme280``.  The dashboard
and API endpoints automatically handle this namespacing.

Endpoints added:
  POST /api/ingest      — receive readings from a remote node
  GET  /api/nodes       — list known remote nodes and last contact
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, request, jsonify

from weather_station.core.database import WeatherDatabase

logger = logging.getLogger(__name__)

# ── Validation schema ──────────────────────────────────────────
# Minimal required fields for a reading payload

_REQUIRED_PAYLOAD_FIELDS = {"station_id", "readings"}
_REQUIRED_READING_FIELDS = {"sensor_name", "timestamp", "metrics"}


def _validate_payload(data: dict) -> tuple[bool, str]:
    """Validate an ingest payload. Returns (ok, error_message)."""
    if not isinstance(data, dict):
        return False, "Payload must be a JSON object"
    missing = _REQUIRED_PAYLOAD_FIELDS - set(data.keys())
    if missing:
        return False, f"Missing fields: {', '.join(missing)}"
    readings = data.get("readings")
    if not isinstance(readings, list) or len(readings) == 0:
        return False, "'readings' must be a non-empty list"
    for i, r in enumerate(readings):
        if not isinstance(r, dict):
            return False, f"reading[{i}] must be an object"
        r_missing = _REQUIRED_READING_FIELDS - set(r.keys())
        if r_missing:
            return False, f"reading[{i}] missing: {', '.join(r_missing)}"
    return True, ""


# ── Blueprint factory ──────────────────────────────────────────

def create_ingest_blueprint(db: WeatherDatabase) -> Blueprint:
    """Create and return the Flask blueprint for remote node ingestion."""
    bp = Blueprint("ingest", __name__, url_prefix="/api")

    @bp.route("/ingest", methods=["POST"])
    def api_ingest() -> tuple[Any, int]:
        """Receive and store readings from a remote weather node."""
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            return jsonify({"success": False, "error": "Invalid JSON"}), 400

        ok, error = _validate_payload(data)
        if not ok:
            return jsonify({"success": False, "error": error}), 400

        station_id = data.get("station_id", "unknown")
        node_type = data.get("node_type", "remote")
        readings = data.get("readings", [])
        inserted = 0

        for r in readings:
            sensor_name = r.get("sensor_name", "unknown")
            # Namespace remote sensors to avoid collisions with local ones
            namespaced_sensor = f"{station_id}/{sensor_name}"
            timestamp = r.get("timestamp")
            metrics = r.get("metrics", {})
            units = r.get("units", {})
            metadata = r.get("metadata", {})

            # Store each metric as a separate row (same schema as local recorder)
            for metric_name, value in metrics.items():
                try:
                    db.insert_reading(
                        sensor_name=namespaced_sensor,
                        metric=metric_name,
                        value=float(value),
                        unit=units.get(metric_name, ""),
                        station_id=station_id,
                        metadata={
                            "node_type": node_type,
                            "original_sensor": sensor_name,
                            **metadata,
                        },
                    )
                    inserted += 1
                except Exception as e:
                    logger.warning("Ingest insert failed for %s/%s: %s",
                                   station_id, sensor_name, e)

        # Update node registry
        db.update_node_last_seen(station_id, node_type, datetime.now(timezone.utc).isoformat())

        logger.info("Ingested %d metrics from node %s", inserted, station_id)
        return jsonify({
            "success": True,
            "inserted": inserted,
            "station_id": station_id,
        }), 200

    @bp.route("/nodes", methods=["GET"])
    def api_nodes() -> Any:
        """List known remote nodes and their last contact time."""
        nodes = db.get_all_nodes() or []
        return jsonify({"nodes": nodes}), 200

    return bp
