"""
Mock manager for sensor simulation.

WHY a dedicated mock manager:
  The Pi Zero 2 W is a development target as much as a deployment target.
  We want to develop and test the full agent pipeline (read → record →
  alert → dashboard) on a laptop with no hardware attached.  MockManager
  generates realistic time-varying mock data so the entire system is
  exercisable without sensors.

Design:
  - Each sensor's ``_read_mock()`` method uses MockManager to get
    plausible values.
  - Values drift over time using a random walk so charts look alive.
  - Diurnal cycles (temperature rises during day, falls at night) are
    simulated using the current hour.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone


class MockManager:
    """Generate realistic mock sensor data with time-based variation."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        # Persistent state for random-walk metrics
        self._state: dict[str, float] = {}
        # Baseline values for each metric
        self._baselines: dict[str, float] = {
            "temperature_c": 18.0,
            "humidity_pct": 50.0,
            "pressure_hpa": 1013.0,
            "gas_resistance_ohms": 5000.0,
            "iaq": 50.0,
            "co2_eq_ppm": 450.0,
            "tvoc_ppb": 100.0,
            "pm1_0_ugm3": 5.0,
            "pm2_5_ugm3": 8.0,
            "pm10_ugm3": 12.0,
            "pm_n_0_3um": 500.0,
            "pm_n_0_5um": 300.0,
            "pm_n_1_0um": 200.0,
            "pm_n_2_5um": 50.0,
            "pm_n_5_0um": 20.0,
            "pm_n_10um": 10.0,
            "light_lux": 200.0,
            "uv_index": 1.0,
            "wind_speed_mps": 3.0,
            "wind_direction_deg": 180.0,
            "wind_direction_cardinal": 180.0,
            "rain_mm": 0.0,
            "rain_rate_mmh": 0.0,
            "co2_ppm": 420.0,
            "air_quality": 50.0,
        }

    def get(self, metric: str, jitter: float = 0.05) -> float:
        """Return a plausible mock value for the given metric.

        The value drifts via random walk and includes a diurnal cycle
        for temperature, humidity, and light.

        Args:
            metric: metric key (e.g. 'temperature_c')
            jitter: fraction of baseline to use as random-walk step size
        """
        baseline = self._baselines.get(metric, 0.0)
        current = self._state.get(metric, baseline)
        now = datetime.now(timezone.utc)

        # Apply diurnal cycle for temperature (-3C at midnight, +3C at noon)
        if metric == "temperature_c":
            hour = now.hour + now.minute / 60.0
            diurnal = 3.0 * math.sin(2 * math.pi * (hour - 6) / 24.0)
            baseline += diurnal

        # Humidity inversely correlated with temperature
        elif metric == "humidity_pct":
            hour = now.hour + now.minute / 60.0
            diurnal = -8.0 * math.sin(2 * math.pi * (hour - 6) / 24.0)
            baseline = max(10.0, min(99.0, baseline + diurnal))

        # Light follows daylight (0 at night, peaks at noon)
        elif metric == "light_lux":
            hour = now.hour + now.minute / 60.0
            if 6 <= hour <= 18:
                baseline = 5000.0 * math.sin(math.pi * (hour - 6) / 12.0)
            else:
                baseline = 0.5  # moonlight
            baseline = max(0.0, baseline)

        elif metric == "uv_index":
            hour = now.hour + now.minute / 60.0
            if 6 <= hour <= 18:
                baseline = 8.0 * math.sin(math.pi * (hour - 6) / 12.0)
            else:
                baseline = 0.0
            baseline = max(0.0, baseline)

        # Random walk: drift toward baseline with jitter
        step = (baseline - current) * 0.1 + self._rng.gauss(0, abs(baseline) * jitter)
        new_value = current + step

        # Clamp to plausible ranges
        new_value = self._clamp(metric, new_value)
        self._state[metric] = new_value
        return new_value

    def _clamp(self, metric: str, value: float) -> float:
        """Clamp a mock value to plausible physical ranges."""
        ranges = {
            "temperature_c": (-40, 55),
            "humidity_pct": (0, 100),
            "pressure_hpa": (950, 1060),
            "gas_resistance_ohms": (1000, 50000),
            "iaq": (0, 500),
            "co2_eq_ppm": (350, 5000),
            "tvoc_ppb": (0, 60000),
            "pm1_0_ugm3": (0, 500),
            "pm2_5_ugm3": (0, 500),
            "pm10_ugm3": (0, 500),
            "light_lux": (0, 120000),
            "uv_index": (0, 11),
            "wind_speed_mps": (0, 60),
            "wind_direction_deg": (0, 360),
            "rain_mm": (0, 100),
            "rain_rate_mmh": (0, 200),
            "co2_ppm": (350, 5000),
            "air_quality": (0, 500),
        }
        lo, hi = ranges.get(metric, (float("-inf"), float("inf")))
        return max(lo, min(hi, value))