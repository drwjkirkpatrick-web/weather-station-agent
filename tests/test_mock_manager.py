"""Tests for the MockManager."""

from weather_station.core.mock_manager import MockManager


class TestMockManager:
    """Test mock data generation."""

    def test_get_returns_float(self):
        mm = MockManager(seed=42)
        val = mm.get("temperature_c")
        assert isinstance(val, float)

    def test_seeded_reproducibility(self):
        """Same seed should produce same sequence."""
        mm1 = MockManager(seed=42)
        mm2 = MockManager(seed=42)
        for _ in range(10):
            assert mm1.get("temperature_c") == mm2.get("temperature_c")

    def test_temperature_in_range(self):
        mm = MockManager(seed=42)
        for _ in range(100):
            val = mm.get("temperature_c")
            assert -40 <= val <= 55

    def test_humidity_in_range(self):
        mm = MockManager(seed=42)
        for _ in range(100):
            val = mm.get("humidity_pct")
            assert 0 <= val <= 100

    def test_pressure_in_range(self):
        mm = MockManager(seed=42)
        for _ in range(100):
            val = mm.get("pressure_hpa")
            assert 950 <= val <= 1060

    def test_pm25_in_range(self):
        mm = MockManager(seed=42)
        for _ in range(100):
            val = mm.get("pm2_5_ugm3")
            assert 0 <= val <= 500

    def test_light_in_range(self):
        mm = MockManager(seed=42)
        for _ in range(100):
            val = mm.get("light_lux")
            assert 0 <= val <= 120000

    def test_unknown_metric_returns_zero(self):
        mm = MockManager(seed=42)
        val = mm.get("nonexistent_metric")
        assert val == 0.0

    def test_values_drift_over_time(self):
        """Successive calls should produce different values (random walk)."""
        mm = MockManager(seed=42)
        values = [mm.get("temperature_c") for _ in range(20)]
        # Not all values should be the same
        assert len(set(values)) > 1

    def test_wind_direction_in_range(self):
        mm = MockManager(seed=42)
        for _ in range(100):
            val = mm.get("wind_direction_deg")
            assert 0 <= val <= 360