"""
Main orchestrator for the weather station agent.

WHY an orchestrator:
  The weather station has multiple concurrent subsystems (recorder,
  alert engine, dashboard, report generator) that need lifecycle
  management.  The orchestrator wires them together, starts them in
  the right order, and handles graceful shutdown.

NOTE: On the Pi Zero 2 W, this module is typically run as a systemd
  service.  See deploy/weather-station.service for the unit file.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from weather_station.core.config import Config
from weather_station.core.database import WeatherDatabase
from weather_station.recording.data_recorder import DataRecorder
from weather_station.alerts.alert_engine import AlertEngine
from weather_station.reporting.report_generator import ReportGenerator

logger = logging.getLogger(__name__)


class WeatherStationAgent:
    """Top-level orchestrator for the weather station.

    Wires together: sensors → recorder → database, alert engine,
    optional web dashboard, and report generator.

    Usage:
        agent = WeatherStationAgent(config)
        agent.start()  # blocks until stopped
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.default()
        self._setup_logging()
        self.db = WeatherDatabase(self.config.recording.db_path)
        self.sensors: list[Any] = []
        self.recorder: DataRecorder | None = None
        self.alert_engine: AlertEngine | None = None
        self.report_generator = ReportGenerator(self.db, self.config.station_id)
        self._running = False
        self._dashboard_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

    def _setup_logging(self) -> None:
        level = logging.DEBUG if self.config.verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def _build_sensors(self) -> list[Any]:
        """Build sensor instances from config."""
        from weather_station.cli import _build_sensors
        return _build_sensors(self.config)

    def initialize(self) -> None:
        """Initialize all subsystems (sensors, recorder, alert engine)."""
        logger.info("Initializing weather station '%s' (mock=%s)",
                     self.config.station_name, self.config.mock_mode)

        # Build and initialize sensors
        self.sensors = self._build_sensors()
        for s in self.sensors:
            s.initialize()

        # Set up recorder
        self.recorder = DataRecorder(
            db=self.db,
            sensors=self.sensors,
            station_id=self.config.station_id,
            sample_interval=self.config.recording.sample_interval_seconds,
            batch_size=self.config.recording.batch_size,
        )

        # Set up alert engine
        self.alert_engine = AlertEngine.from_config(self.db, self.config.alerts)

        logger.info("Initialized %d sensors", len(self.sensors))

    def start(self) -> None:
        """Start all subsystems and block until shutdown."""
        if not self.sensors:
            self.initialize()

        self._running = True
        self._shutdown_event.clear()

        # Start recorder
        if self.recorder:
            self.recorder.start()

        # Start dashboard in a thread
        self._start_dashboard()

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        logger.info("Weather station agent started. Press Ctrl+C to stop.")

        # Block until shutdown
        try:
            while self._running:
                self._shutdown_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass

        self.shutdown()

    def _start_dashboard(self) -> None:
        """Start the Flask dashboard in a background thread."""
        from weather_station.web.dashboard import create_app

        app = create_app(self.db, self.config.station_id)

        # NOTE: Flask's dev server is fine for Pi Zero 2 W on a local
        # network.  For production, use gunicorn (see deploy/README.md).
        self._dashboard_thread = threading.Thread(
            target=lambda: app.run(
                host=self.config.web.host,
                port=self.config.web.port,
                debug=self.config.web.debug,
                use_reloader=False,  # reloader spawns a second process
            ),
            daemon=True,
            name="dashboard",
        )
        self._dashboard_thread.start()
        logger.info("Dashboard started on %s:%d", self.config.web.host, self.config.web.port)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False
        self._shutdown_event.set()

    def shutdown(self) -> None:
        """Stop all subsystems cleanly."""
        logger.info("Shutting down weather station agent...")

        if self.recorder:
            self.recorder.stop()

        # Prune old data on shutdown
        try:
            deleted = self.db.prune_old_data(self.config.recording.retention_days)
            if deleted:
                logger.info("Pruned %d old readings", deleted)
        except Exception as e:
            logger.error("Failed to prune old data: %s", e)

        logger.info("Shutdown complete.")

    def health_check(self) -> dict[str, Any]:
        """Return overall system health."""
        sensor_health = [s.health_check() for s in self.sensors]
        db_stats = self.db.get_table_stats()
        recorder_health = self.recorder.health_check() if self.recorder else {}

        return {
            "station_name": self.config.station_name,
            "station_id": self.config.station_id,
            "running": self._running,
            "mock_mode": self.config.mock_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sensors": sensor_health,
            "database": db_stats,
            "recorder": recorder_health,
        }


def main() -> None:
    """Entry point for direct python -m weather_station.main execution."""
    import argparse

    parser = argparse.ArgumentParser(description="Weather Station Agent")
    parser.add_argument("-c", "--config", help="Path to YAML config file")
    parser.add_argument("--mock", action="store_true", help="Force mock mode")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    config = Config.from_yaml(args.config) if args.config else Config.default()
    if args.mock:
        config = Config(
            station_name=config.station_name,
            station_id=config.station_id,
            mock_mode=True,
            verbose=args.verbose,
            sensors=config.sensors,
            recording=config.recording,
            alerts=config.alerts,
            web=config.web,
        )
    elif args.verbose:
        config = Config(
            station_name=config.station_name,
            station_id=config.station_id,
            mock_mode=config.mock_mode,
            verbose=True,
            sensors=config.sensors,
            recording=config.recording,
            alerts=config.alerts,
            web=config.web,
        )

    agent = WeatherStationAgent(config)
    agent.start()


if __name__ == "__main__":
    main()