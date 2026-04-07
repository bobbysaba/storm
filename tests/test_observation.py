"""Tests for core.observation and core.vehicle."""

from datetime import datetime, timezone

from core.observation import Observation
from core.vehicle import Vehicle


class TestObservation:
    def test_constructor(self):
        ts = datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc)
        obs = Observation(
            vehicle_id="truck1",
            lat=35.22,
            lon=-97.44,
            timestamp=ts,
            temperature_c=25.0,
            dewpoint_c=18.0,
            wind_speed_ms=5.0,
            wind_dir_deg=210.0,
            pressure_mb=1013.0,
        )
        assert obs.vehicle_id == "truck1"
        assert obs.lat == 35.22
        assert obs.temperature_c == 25.0
        assert obs.icon_type is None

    def test_new_factory(self):
        obs = Observation.new(
            "drone1", 35.0, -97.0,
            temperature_c=30.0,
            icon_type="drone",
        )
        assert obs.vehicle_id == "drone1"
        assert obs.temperature_c == 30.0
        assert obs.icon_type == "drone"
        assert obs.timestamp.tzinfo == timezone.utc
        assert obs.dewpoint_c is None

    def test_optional_fields_default_none(self):
        obs = Observation("v1", 0, 0, datetime.now(timezone.utc))
        assert obs.temperature_c is None
        assert obs.dewpoint_c is None
        assert obs.wind_speed_ms is None
        assert obs.wind_dir_deg is None
        assert obs.pressure_mb is None
        assert obs.icon_type is None


class TestVehicle:
    def test_constructor(self):
        v = Vehicle("truck1", 35.0, -97.0)
        assert v.id == "truck1"
        assert v.icon_type == "car"
        assert v.latest_obs is None

    def test_custom_icon(self):
        v = Vehicle("lidar1", 35.0, -97.0, icon_type="lidar")
        assert v.icon_type == "lidar"

    def test_with_observation(self):
        obs = Observation.new("truck1", 35.0, -97.0, temperature_c=25.0)
        v = Vehicle("truck1", 35.0, -97.0, latest_obs=obs)
        assert v.latest_obs is obs
        assert v.latest_obs.temperature_c == 25.0
