"""
Command-line interface for the weather station agent.

WHY argparse:
  The Pi Zero 2 W often runs headless.  A CLI lets the user check sensor
  status, trigger a manual read, export data, or generate reports from
  an SSH session without needing the web dashboard.

Commands:
  weather-station run       — start the full agent (recorder + dashboard)
  weather-station status    — show sensor health and DB stats
  weather-station read      — take a single reading from all sensors
  weather-station export    — export data to CSV/JSON
  weather-station report    — generate a daily/weekly summary
  weather-station alert-rules — list configured alert rules
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from weather_station.core.config import Config
from weather_station.core.database import WeatherDatabase
from weather_station.core.mock_manager import MockManager
from weather_station.recording.data_recorder import DataRecorder
from weather_station.recording.exporter import DataExporter
from weather_station.reporting.report_generator import ReportGenerator
from weather_station.alerts.alert_engine import AlertEngine
from weather_station.forwarding.forwarder import DataForwarder

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _build_sensors(config: Config) -> list[Any]:
    """Build sensor instances from config. Returns only enabled sensors."""
    from weather_station.sensors import (
        BME680Sensor, BME280Sensor, SHT31Sensor, PMS5003Sensor,
        SGP30Sensor, VEML7700Sensor, DS3231Sensor,
        AnemometerSensor, WindVaneSensor, RainGaugeSensor, MQ135Sensor,
    )
    sc = config.sensors
    mock = config.mock_mode
    sensors: list[Any] = []

    if sc.bme680_enabled:
        sensors.append(BME680Sensor(
            i2c_address=sc.bme680_i2c_address,
            sea_level_pressure=sc.bme680_sea_level_pressure,
            mock_mode=mock))
    if sc.bme280_enabled:
        sensors.append(BME280Sensor(i2c_address=sc.bme280_i2c_address, mock_mode=mock))
    if sc.sht31_enabled:
        sensors.append(SHT31Sensor(i2c_address=sc.sht31_i2c_address, mock_mode=mock))
    if sc.sgp30_enabled:
        sensors.append(SGP30Sensor(i2c_address=sc.sgp30_i2c_address, mock_mode=mock))
    if sc.veml7700_enabled:
        sensors.append(VEML7700Sensor(i2c_address=sc.veml7700_i2c_address, mock_mode=mock))
    if sc.ds3231_enabled:
        sensors.append(DS3231Sensor(i2c_address=sc.ds3231_i2c_address, mock_mode=mock))
    if sc.pms5003_enabled:
        sensors.append(PMS5003Sensor(
            serial_port=sc.pms5003_serial_port,
            baudrate=sc.pms5003_baudrate,
            mock_mode=mock))
    if sc.anemometer_enabled:
        sensors.append(AnemometerSensor(
            pin=sc.anemometer_pin,
            radius_cm=sc.anemometer_radius_cm,
            calibration_factor=sc.anemometer_calibration_factor,
            mock_mode=mock))
    if sc.wind_vane_enabled:
        sensors.append(WindVaneSensor(
            adc_channel=sc.wind_vane_adc_channel,
            vin=sc.wind_vane_vin,
            mock_mode=mock))
    if sc.rain_gauge_enabled:
        sensors.append(RainGaugeSensor(
            pin=sc.rain_gauge_pin,
            bucket_ml=sc.rain_gauge_bucket_ml,
            mock_mode=mock))
    if sc.mq135_enabled:
        sensors.append(MQ135Sensor(
            adc_channel=sc.mq135_adc_channel,
            vin=sc.mq135_vin,
            mock_mode=mock))

    return sensors


def cmd_run(args: argparse.Namespace) -> int:
    """Start the full weather station agent."""
    config = Config.from_yaml(args.config) if args.config else Config.default()
    if args.mock:
        config = Config(
            station_name=config.station_name,
            station_id=config.station_id,
            latitude=config.latitude,
            longitude=config.longitude,
            mock_mode=True,
            verbose=args.verbose,
            sensors=config.sensors,
            recording=config.recording,
            alerts=config.alerts,
            web=config.web,
            forwarding=config.forwarding,
        )

    _setup_logging(config.verbose)
    logger.info("Starting weather station '%s' (mock=%s)", config.station_name, config.mock_mode)

    db = WeatherDatabase(config.recording.db_path)
    sensors = _build_sensors(config)

    # Initialize sensors
    for s in sensors:
        s.initialize()

    # Set up recorder
    recorder = DataRecorder(
        db=db,
        sensors=sensors,
        station_id=config.station_id,
        sample_interval=config.recording.sample_interval_seconds,
        batch_size=config.recording.batch_size,
    )

    # Set up alert engine
    alert_engine = AlertEngine.from_config(db, config.alerts)

    # Set up data forwarder (optional)
    forwarder = DataForwarder(db=db, config=config, mock_mode=config.mock_mode)

    # Start recorder
    recorder.start()

    # Start forwarder (only if enabled + configured)
    forwarder.start()

    # Start web dashboard
    from weather_station.web.dashboard import create_app
    app = create_app(db, config.station_id)
    logger.info("Dashboard starting on %s:%d", config.web.host, config.web.port)
    try:
        app.run(host=config.web.host, port=config.web.port, debug=config.web.debug)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        recorder.stop()
        forwarder.stop()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show sensor health and DB stats."""
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
            forwarding=config.forwarding,
        )
    _setup_logging(config.verbose)

    db = WeatherDatabase(config.recording.db_path)
    sensors = _build_sensors(config)
    for s in sensors:
        s.initialize()

    print(f"\n{'='*60}")
    print(f"  Weather Station: {config.station_name} ({config.station_id})")
    print(f"  Mock mode: {config.mock_mode}")
    print(f"{'='*60}")

    print(f"\n--- Sensors ({len(sensors)} enabled) ---")
    for s in sensors:
        health = s.health_check()
        status = "OK" if health["initialized"] else "FAIL"
        print(f"  [{status}] {health['name']:20s} bus={health['bus_type']:6s} "
              f"health={health['health_score']:.2f} metrics={len(health['metrics'])}")

    print(f"\n--- Database ---")
    stats = db.get_table_stats()
    for table, count in stats.items():
        print(f"  {table:20s} {count:>8d} rows")

    print()
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    """Take a single reading from all sensors."""
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
            forwarding=config.forwarding,
        )
    _setup_logging(config.verbose)

    sensors = _build_sensors(config)
    for s in sensors:
        s.initialize()

    print(f"\n{'='*60}")
    print(f"  Instant Reading | Station: {config.station_id}")
    print(f"{'='*60}")

    for s in sensors:
        reading = s.read()
        if reading is None:
            print(f"\n  [{s.name}] READ FAILED")
            continue
        print(f"\n  [{s.name}] {reading.timestamp}")
        for metric, value in reading.metrics.items():
            unit = reading.units.get(metric, "")
            print(f"    {metric:30s} = {value:>10.2f} {unit}")

    print()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export weather data to CSV or JSON."""
    config = Config.from_yaml(args.config) if args.config else Config.default()
    _setup_logging(config.verbose)

    db = WeatherDatabase(config.recording.db_path)
    exporter = DataExporter(db)

    fmt = args.format
    output = args.output
    if fmt == "csv":
        count = exporter.export_csv(output, station_id=config.station_id, limit=args.limit)
    else:
        count = exporter.export_json(output, station_id=config.station_id, limit=args.limit)
    print(f"Exported {count} readings to {output}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate a daily or weekly report."""
    config = Config.from_yaml(args.config) if args.config else Config.default()
    _setup_logging(config.verbose)

    db = WeatherDatabase(config.recording.db_path)
    gen = ReportGenerator(db, config.station_id)

    if args.weekly:
        report = gen.generate_weekly_report(args.date)
        print(f"\nWeekly Report ({report['start_date']} to {report['end_date']})")
        for key, stats in report.get("weekly_summary", {}).items():
            print(f"  {key}: min={stats['min']:.1f} max={stats['max']:.1f} avg={stats['avg']:.1f}")
    else:
        report = gen.generate_daily_report(args.date)
        print(gen.format_human_readable(report))
    return 0


def cmd_alert_rules(args: argparse.Namespace) -> int:
    """List configured alert rules."""
    config = Config.from_yaml(args.config) if args.config else Config.default()
    _setup_logging(config.verbose)

    db = WeatherDatabase(config.recording.db_path)
    engine = AlertEngine.from_config(db, config.alerts)

    print(f"\n{'='*60}")
    print(f"  Alert Rules ({len(engine.rules)} configured)")
    print(f"{'='*60}")
    for rule in engine.rules:
        print(f"  {rule.name:20s} [{rule.severity:8s}] "
              f"{rule.sensor_name}.{rule.metric} {rule.operator} {rule.threshold}")
    print()
    return 0


def cmd_forward_status(args: argparse.Namespace) -> int:
    """Show forwarding service status and optionally trigger a test push."""
    config = Config.from_yaml(args.config) if args.config else Config.default()
    if args.mock:
        config = Config(
            station_name=config.station_name,
            station_id=config.station_id,
            latitude=config.latitude,
            longitude=config.longitude,
            mock_mode=True,
            verbose=args.verbose,
            sensors=config.sensors,
            recording=config.recording,
            alerts=config.alerts,
            web=config.web,
            forwarding=config.forwarding,
        )
    _setup_logging(config.verbose)

    forwarder = DataForwarder(
        db=WeatherDatabase(config.recording.db_path),
        config=config,
        mock_mode=config.mock_mode,
    )

    fc = config.forwarding
    print(f"\n{'='*60}")
    print(f"  Data Forwarding Status | Station: {config.station_id}")
    print(f"  Master switch: {'ON' if fc.enabled else 'OFF'}")
    print(f"  Interval: {fc.forward_interval_seconds}s")
    print(f"  Mock mode: {config.mock_mode}")
    print(f"{'='*60}")

    if not fc.enabled:
        print("\n  Forwarding is disabled. Set 'forwarding.enabled: true' in config.")
        print()
        return 0

    services = forwarder._services
    active = [s for s in services if s.is_enabled()]
    print(f"\n  Active services: {len(active)}")
    print()

    for s in services:
        status = "ENABLED" if s.is_enabled() else "disabled"
        hc = s.health_check()
        last = hc.get("last_result")
        last_str = ""
        if last:
            last_str = f"  last: {'OK' if last['success'] else 'FAIL: ' + last['message']}"
        print(f"  [{status:7s}] {s.name:20s} {s.description}")
        if hc["total_sent"] > 0:
            print(f"           sent={hc['total_sent']} success={hc['total_success']} "
                  f"fail={hc['total_failures']} consec_fail={hc['consecutive_failures']}")
        if last_str:
            print(f"           {last_str}")
        print()

    # Optional: trigger a single test forward
    if args.test:
        print("  Triggering test forward...\n")
        results = forwarder.forward_once()
        if not results:
            print("  No active services or no data to forward.")
        for r in results:
            symbol = "✓" if r.success else "✗"
            print(f"  {symbol} {r.service}: {r.message}")
        print()

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="weather-station",
        description="Weather measurement and recording agent for Raspberry Pi Zero 2 W",
    )
    parser.add_argument("-c", "--config", help="Path to YAML config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--mock", action="store_true", help="Force mock mode (no hardware)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("run", help="Start the full agent (recorder + dashboard)").set_defaults(func=cmd_run)
    subparsers.add_parser("status", help="Show sensor health and DB stats").set_defaults(func=cmd_status)
    subparsers.add_parser("read", help="Take a single reading from all sensors").set_defaults(func=cmd_read)

    export_parser = subparsers.add_parser("export", help="Export data to CSV/JSON")
    export_parser.add_argument("-f", "--format", choices=["csv", "json"], default="csv")
    export_parser.add_argument("-o", "--output", required=True, help="Output file path")
    export_parser.add_argument("-l", "--limit", type=int, default=10000)
    export_parser.set_defaults(func=cmd_export)

    report_parser = subparsers.add_parser("report", help="Generate a daily/weekly report")
    report_parser.add_argument("-d", "--date", help="Date (YYYY-MM-DD), defaults to yesterday")
    report_parser.add_argument("-w", "--weekly", action="store_true", help="Generate weekly report")
    report_parser.set_defaults(func=cmd_report)

    subparsers.add_parser("alert-rules", help="List configured alert rules").set_defaults(func=cmd_alert_rules)

    forward_parser = subparsers.add_parser(
        "forward-status", help="Show data forwarding service status")
    forward_parser.add_argument(
        "-t", "--test", action="store_true",
        help="Trigger a single test forward to enabled services")
    forward_parser.set_defaults(func=cmd_forward_status)

    return parser


def main() -> None:
    """CLI entry point (called by the weather-station console script)."""
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    sys.exit(args.func(args))