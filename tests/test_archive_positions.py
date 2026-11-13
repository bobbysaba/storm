"""Fixes looked up by time (archive/positions.py), and the truck compass read
from the one-second FOFS files."""
from datetime import datetime, timedelta, timezone

import numpy as np

from archive.positions import PositionTrack
from core.observation import Observation

T0 = datetime(2023, 6, 9, 20, 0, tzinfo=timezone.utc)


def _obs(seconds, lat=35.0, lon=-97.0, heading=None):
    return Observation("lid1", lat, lon, T0 + timedelta(seconds=seconds), heading_deg=heading)


def test_nearest_fix_within_a_minute_ties_take_the_older():
    track = PositionTrack([_obs(20, lat=35.2), _obs(0, lat=35.0), _obs(10, lat=35.1)])
    assert track.nearest(T0 + timedelta(seconds=5)).lat == 35.0        # tie: the older fix
    assert track.nearest(T0 + timedelta(seconds=14)).lat == 35.1
    assert track.nearest(T0 + timedelta(seconds=80)).lat == 35.2       # 60 s after the last
    assert track.nearest(T0 + timedelta(seconds=81)) is None
    assert track.nearest(T0 - timedelta(seconds=61)) is None


def test_bad_fixes_are_left_out():
    track = PositionTrack([_obs(0, lat=0.0, lon=0.0), _obs(1, lat=float("nan")), _obs(2, lat=95.0),
                           _obs(3, lat=None), _obs(4)])
    assert len(track) == 1 and track.nearest(T0).timestamp == T0 + timedelta(seconds=4)


def test_many_times_at_once_match_one_by_one():
    track = PositionTrack([_obs(s, lat=35 + s / 1000) for s in range(0, 600, 7)])
    epochs = T0.timestamp() + np.array([-100, -3, 0, 3.5, 250, 595, 700, np.nan])
    lat, lon, when = track.positions_at(epochs)
    for e, la in zip(epochs, lat):
        fix = track.nearest(e) if np.isfinite(e) else None
        assert (np.isnan(la) and fix is None) or la == fix.lat
    assert np.isnan(when[0]) and when[2] == T0.timestamp()


def test_compass_headings_come_only_from_fixes_with_one():
    track = PositionTrack([_obs(0, heading=90.0), _obs(30), _obs(100, heading=270.0)])
    np.testing.assert_allclose(track.headings_at(T0.timestamp() + np.array([0, 30, 45, 61, 200])),
                               [90.0, 90.0, 90.0, 270.0, np.nan])
    assert np.isnan(PositionTrack([]).headings_at([T0.timestamp()])).all()


def test_fofs_compass_dir_is_read_as_the_heading():
    from archive.fetchers.vehicle_obs_archive_fetcher import _measurement
    assert _measurement({"compass_dir": "123.4"}, "compass_dir") == 123.4
    assert _measurement({"compass_dir": "361.0"}, "compass_dir") == 1.0
    assert _measurement({"compass_dir": "-999"}, "compass_dir") is None
