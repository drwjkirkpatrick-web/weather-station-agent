"""
Data recorder: periodically poll all sensors and store readings to the database.

WHY a dedicated recorder:
  The recorder runs in its own thread, polling sensors at the configured
  interval.  This decouples sensor reads from the web dashboard and alert
  engine, each of which runs independently.

NOTE: The recorder is the system's heartbeat.  If it stops, no new data
enters the database.  The main orchestrator monitors the recorder's
health and restarts it if needed.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from weather_station.core.database import WeatherDatabase
from weather_station.core.sensor_base import SensorBase, SensorReading

logger = logging.getLogger(__name__)


class DataRecorder:
    """Periodically reads all sensors and stores results to the database.

    Runs in a background thread.  Call ``start()`` to begin recording and
    ``stop()`` to shut down cleanly.
    """

    def __init__(
        self,
        db: WeatherDatabase,
        sensors: list[SensorBase],
        station_id: str = "ws01",
        sample_interval: int = 60,
        batch_size: int = 50,
    ) -> None:
        self.db = db
        self.sensors = sensors
        self.station_id = station_id
        self.sample_interval = sample_interval
        self.batch_size = batch_size
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._total_readings: int = 0
        self._total_errors: int = 0
        self._cycle_count: int = 0

    def start(self) -> None:
        """Start the recording thread."""
        if self._running:
            logger.warning("Recorder already running")
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="data-recorder")
        self._thread.start()
        logger.info("Data recorder started (interval=%ds)", self.sample_interval)

    def stop(self) -> None:
        """Signal the recording thread to stop and wait for it."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Data recorder stopped")

    def _run(self) -> None:
        """Main recording loop."""
        while not self._stop_event.is_set():
            self._cycle_count += 1
            try:
                self._record_cycle()
            except Exception as e:
                logger.error("Recording cycle %d failed: %s", self._cycle_count, e)
                self._total_errors += 1
            # Wait for the sample interval, but wake early if stopped
            self._stop_event.wait(self.sample_interval)

    def _record_cycle(self) -> None:
        """Read all sensors and store results in a single batch."""
        readings: list[dict[str, Any]] = []
        timestamp = datetime.now(timezone.utc).isoformat()
        active_sensors = 0

        for sensor in self.sensors:
            reading = sensor.read()
            if reading is None:
                self._total_errors += 1
                continue
            active_sensors += 1
            for metric, value in reading.metrics.items():
                unit = reading.units.get(metric, "")
                readings.append({
                    "timestamp": reading.timestamp,
                    "station_id": self.station_id,
                    "sensor_name": reading.sensor_name,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "metadata": reading.metadata or None,
                })
                self._total_readings += 1

        if readings:
            self.db.insert_readings_batch(readings)

        logger.debug(
            "Cycle %d: %d sensors active, %d readings stored",
            self._cycle_count, active_sensors, len(readings),
        )

    def record_single(self, sensor: SensorBase) -> SensorReading | None:
        """Read a single sensor and store its result immediately.

        Used by the CLI for one-off reads or by the alert engine when it
        needs a fresh reading outside the normal cycle.
        """
        reading = sensor.read()
        if reading is None:
            return None
        for metric, value in reading.metrics.items():
            unit = reading.units.get(metric, "")
            self.db.insert_reading(
                timestamp=reading.timestamp,
                station_id=self.station_id,
                sensor_name=reading.sensor_name,
                metric=metric,
                value=value,
                unit=unit,
                metadata=reading.metadata or None,
            )
        return reading

    def health_check(self) -> dict[str, Any]:
        """Return recorder health status."""
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "total_readings": self._total_readings,
            "total_errors": self._total_errors,
            "sample_interval_s": self.sample_interval,
            "active_sensors": len(self.sensors),
        }