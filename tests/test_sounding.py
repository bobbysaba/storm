"""Tests for core.sounding — Sounding and SoundingSet data classes."""

import numpy as np
from datetime import datetime, timezone

from core.sounding import Sounding, SoundingSet, PRESSURE_LEVELS, SOUNDING_SLOTS


def _make_sounding(slot_offset=0, n_levels=10):
    """Helper to build a minimal Sounding for testing."""
    return Sounding(
        lat=35.22,
        lon=-97.44,
        valid_time=datetime(2026, 4, 6, 18, 0, tzinfo=timezone.utc),
        slot_offset=slot_offset,
        label=f"F{slot_offset}",
        pressure=np.linspace(1000, 200, n_levels),
        temperature=np.linspace(30, -50, n_levels),
        dewpoint=np.linspace(20, -60, n_levels),
        u_wind=np.full(n_levels, -5.0),
        v_wind=np.full(n_levels, -10.0),
        height=np.linspace(300, 12000, n_levels),
    )


class TestSounding:
    def test_wind_speed(self):
        snd = _make_sounding()
        expected = np.sqrt(25.0 + 100.0)  # sqrt(u^2 + v^2)
        np.testing.assert_allclose(snd.wind_speed, expected)

    def test_wind_direction(self):
        # u=-5, v=-10 → wind from ~207 degrees
        snd = _make_sounding()
        dirs = snd.wind_direction
        assert dirs.shape == snd.pressure.shape
        # all entries should be the same since u/v are constant
        np.testing.assert_allclose(dirs, dirs[0])
        # sanity: direction should be between 0 and 360
        assert np.all(dirs >= 0) and np.all(dirs < 360)

    def test_wind_direction_north(self):
        """Wind from the north: u=0, v=-10 → direction = 0 (or 360)."""
        snd = _make_sounding()
        snd.u_wind = np.zeros(10)
        snd.v_wind = np.full(10, -10.0)
        np.testing.assert_allclose(snd.wind_direction, 0.0, atol=1e-10)

    def test_wind_direction_east(self):
        """Wind from the east: u=-10 (blowing westward), v=0 → direction = 90."""
        snd = _make_sounding()
        snd.u_wind = np.full(10, -10.0)
        snd.v_wind = np.zeros(10)
        np.testing.assert_allclose(snd.wind_direction, 90.0, atol=1e-10)

    def test_wind_direction_west(self):
        """Wind from the west: u=+10 (blowing eastward), v=0 → direction = 270."""
        snd = _make_sounding()
        snd.u_wind = np.full(10, 10.0)
        snd.v_wind = np.zeros(10)
        np.testing.assert_allclose(snd.wind_direction, 270.0, atol=1e-10)

    def test_pressure_levels_sorted(self):
        assert PRESSURE_LEVELS == sorted(PRESSURE_LEVELS, reverse=True)

    def test_sounding_slots_count(self):
        assert len(SOUNDING_SLOTS) == 4


class TestSoundingSet:
    def test_get_existing_slot(self):
        s0 = _make_sounding(slot_offset=0)
        s1 = _make_sounding(slot_offset=1)
        sset = SoundingSet(
            lat=35.22, lon=-97.44, elevation=300.0,
            fetch_time=datetime.now(timezone.utc),
            soundings=[s0, s1],
        )
        assert sset.get(0) is s0
        assert sset.get(1) is s1

    def test_get_missing_slot(self):
        sset = SoundingSet(
            lat=35.22, lon=-97.44, elevation=300.0,
            fetch_time=datetime.now(timezone.utc),
            soundings=[_make_sounding(slot_offset=0)],
        )
        assert sset.get(5) is None

    def test_is_observed(self):
        sset = SoundingSet(
            lat=0, lon=0, elevation=0,
            fetch_time=datetime.now(timezone.utc),
            soundings=[], source="obs",
        )
        assert sset.is_observed is True
        assert sset.is_nssl is False

    def test_is_nssl(self):
        sset = SoundingSet(
            lat=0, lon=0, elevation=0,
            fetch_time=datetime.now(timezone.utc),
            soundings=[], source="nssl",
        )
        assert sset.is_nssl is True
        assert sset.is_observed is False

    def test_default_source_is_hrrr(self):
        sset = SoundingSet(
            lat=0, lon=0, elevation=0,
            fetch_time=datetime.now(timezone.utc),
            soundings=[],
        )
        assert sset.source == "hrrr"
        assert sset.is_observed is False
        assert sset.is_nssl is False
