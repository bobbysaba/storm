from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from core.observation import Observation
from ui.layers import station_plot_layer as spl
from ui.layers.station_plot_layer import StationPlotLayer
from ui.layers.surface_plot_layer import SurfacePlotLayer, _surface_obs_fingerprint


@pytest.fixture
def renders():
    spl._png_cache.clear()
    with mock.patch.object(spl, "_render_key", return_value=b"png") as render:
        yield render
    spl._png_cache.clear()


def _obs(lat=35.0, lon=-97.0, age_min=0.0, temp=20.0, wind=5.0):
    return Observation(
        vehicle_id="V1", lat=lat, lon=lon,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=age_min),
        temperature_c=temp, dewpoint_c=10.0, wind_speed_ms=wind,
        wind_dir_deg=180.0, pressure_mb=960.0,
    )


def test_moving_vehicle_with_steady_values_never_renders(renders):
    map_widget = mock.Mock()
    layer = StationPlotLayer(map_widget)
    layer.update("V1", 35.0, -97.0, _obs())
    for i in range(1, 50):
        lat = 35.0 + i * 1e-3
        layer.update("V1", lat, -97.0, _obs(lat=lat, temp=20.01))
    assert renders.call_count == 1
    assert map_widget.add_station_plot.call_count == 1
    assert map_widget.move_station_plot.call_count == 49


def test_changed_value_rerenders(renders):
    layer = StationPlotLayer(mock.Mock())
    layer.update("V1", 35.0, -97.0, _obs(temp=20.0))
    layer.update("V1", 35.0, -97.0, _obs(temp=21.0))
    assert renders.call_count == 2


def test_cache_shared_across_vehicles(renders):
    layer = StationPlotLayer(mock.Mock())
    layer.update("V1", 35.0, -97.0, _obs())
    layer.update("V2", 36.0, -98.0, _obs(lat=36.0, lon=-98.0))
    assert renders.call_count == 1


def test_calm_wind_has_no_barb_key():
    assert spl._display_key(_obs(wind=0.5))[3] is None


def test_surface_fingerprint_ignores_timestamp_but_tracks_age_bucket():
    sid = "surface:ok:NRMN"
    fresh_a = _surface_obs_fingerprint(_obs(age_min=1), sid)
    fresh_b = _surface_obs_fingerprint(_obs(age_min=3), sid)
    stale = _surface_obs_fingerprint(_obs(age_min=7), sid)
    dead = _surface_obs_fingerprint(_obs(age_min=20), sid)
    assert fresh_a == fresh_b
    assert len({fresh_a, stale, dead}) == 3


def test_unchanged_surface_cycle_never_renders(renders):
    map_widget = mock.Mock()
    layer = SurfacePlotLayer(map_widget)
    for _ in range(3):
        layer.update("surface:ok:NRMN", 35.0, -97.0, _obs(age_min=1))
    assert renders.call_count == 1
    assert map_widget.add_surface_station_plot.call_count == 1


def test_png_cache_is_bounded(renders):
    for t in range(spl._PNG_CACHE_SIZE + 50):
        spl.render_cached((t, None, None, None))
    assert len(spl._png_cache) == spl._PNG_CACHE_SIZE
