"""
Base class for all weather station sensors.

WHY a common base:
  Every sensor driver inherits from SensorBase so the orchestrator can
  treat them uniformly — call ``read()`` and get back a dict of metrics.
  This also gives us a consistent health/status interface for the
  dashboard and self-test subsystem.

Design decisions:
  - ``read()`` returns a ``SensorReading`` dataclass (see below).
  - Each sensor declares its ``name``, ``metrics`` list, and ``bus_type``
    so the orchestrator can log wiring info and detect conflicts.
  - The ``_read_hardware()`` method is the only thing subclasses need to
    implement — everything else (error handling, health tracking, mock
    fallback) is handled here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SensorReading:
    """A single timestamped reading from a sensor.

    Attributes:
        sensor_name: e.g. 'bme680', 'pms5003'
        timestamp: ISO 8601 UTC string
        metrics: dict of metric_name -> value (e.g. {'temperature_c': 22.5})
        units: dict of metric_name -> unit string (e.g. {'temperature_c': 'celsius'})
        metadata: optional extra data (e.g. calibration info, raw bytes)
    """

    sensor_name: str
    timestamp: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class SensorBase:
    """Abstract base for all sensor drivers.

    Subclasses must implement ``_read_hardware()`` and set the class
    attributes ``name``, ``metrics``, and ``bus_type``.
    """

    # ── Subclass must override these ───────────────────────────────────
    name: str = "base"
    metrics: list[str] = []           # e.g. ['temperature_c', 'humidity_pct', ...]
    bus_type: str = "unknown"          # 'i2c', 'serial', 'gpio', 'adc'
    description: str = "Generic sensor"

    def __init__(self, mock_mode: bool = False) -> None:
        self.mock_mode = mock_mode
        self._health_score: float = 1.0   # 1.0 = healthy, 0.0 = dead
        self._consecutive_failures: int = 0
        self._last_reading: SensorReading | None = None
        self._last_read_time: float = 0.0
        self._initialized: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Initialize the sensor hardware. Override in subclasses.

        Returns True if initialization succeeded (or mock mode), False
        if the sensor could not be reached.
        """
        if self.mock_mode:
            self._initialized = True
            logger.info("[%s] initialized in mock mode", self.name)
            return True
        try:
            result = self._init_hardware()
            self._initialized = result
            if result:
                logger.info("[%s] initialized successfully", self.name)
            else:
                logger.warning("[%s] hardware initialization failed", self.name)
            return result
        except Exception as e:
            logger.error("[%s] init error: %s", self.name, e)
            self._initialized = False
            return False

    def _init_hardware(self) -> bool:
        """Subclass hardware init. Default: assume success."""
        return True

    # ── Reading ────────────────────────────────────────────────────────

    def read(self) -> SensorReading | None:
        """Take a reading. Returns SensorReading or None on failure.

        This method handles error wrapping, health tracking, and mock
        fallback.  Subclasses only implement ``_read_hardware()``.
        """
        if not self._initialized and not self.mock_mode:
            logger.warning("[%s] not initialized, skipping read", self.name)
            return None

        try:
            if self.mock_mode:
                reading = self._read_mock()
            else:
                reading = self._read_hardware()

            if reading is None:
                self._record_failure()
                return None

            self._record_success(reading)
            return reading

        except Exception as e:
            logger.error("[%s] read error: %s", self.name, e)
            self._record_failure()
            return None

    def _read_hardware(self) -> SensorReading | None:
        """Subclass implements actual hardware read. Must return SensorReading or None."""
        raise NotImplementedError

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock data. Override in subclasses for realism.

        The default implementation returns zeros for all declared metrics.
        Subclasses should override with more realistic values.
        """
        return SensorReading(
            sensor_name=self.name,
            metrics={m: 0.0 for m in self.metrics},
            units={m: "" for m in self.metrics},
        )

    # ── Health tracking ────────────────────────────────────────────────

    def _record_success(self, reading: SensorReading) -> None:
        self._consecutive_failures = 0
        self._health_score = min(1.0, self._health_score + 0.1)
        self._last_reading = reading
        self._last_read_time = time.time()

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        self._health_score = max(0.0, self._health_score - 0.2)

    # ── Status ──────────────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Return a health status dict for the dashboard."""
        return {
            "name": self.name,
            "description": self.description,
            "bus_type": self.bus_type,
            "initialized": self._initialized,
            "mock_mode": self.mock_mode,
            "health_score": round(self._health_score, 2),
            "consecutive_failures": self._consecutive_failures,
            "last_read_time": (
                datetime.fromtimestamp(self._last_read_time, tz=timezone.utc).isoformat()
                if self._last_read_time > 0 else None
            ),
            "metrics": list(self.metrics),
        }

    @property
    def is_healthy(self) -> bool:
        """True if the sensor is considered operational."""
        return self._health_score > 0.3 and self._initialized