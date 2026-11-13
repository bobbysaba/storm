"""One lidar sweep as a map image (ui/map/lidar_overlay.py)."""
from datetime import datetime, timedelta, timezone
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from archive.fetchers.raw_lidar_archive_fetcher import RawLidarRays, RawLidarSource
from ui.map.lidar_overlay import render_lidar_to_png

_WHEN = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


def _clamps_source(mobile=False):
    return RawLidarSource(
        platform_id="CLAMPS1-PPI", instrument="CLAMPS1",
        platform_dir="clamps/clamps1", datastream="clampsdlppiC1.b1",
        product="ppi", mobile=mobile,
    )


def _ppi_rays(source=None, n_rays=121, field="velocity", units="m/s"):
    source = source or _clamps_source()
    n_gates = 5
    az = np.linspace(240, 300, n_rays)
    # rays_at() only looks back from `when`: every ray at or before it
    times = np.array([(_WHEN - timedelta(seconds=(n_rays - 1 - i) * 0.5)).timestamp()
                      for i in range(n_rays)])
    data = np.ma.masked_invalid(np.tile(np.linspace(-15, 15, n_gates), (n_rays, 1)).astype(np.float32))
    return RawLidarRays(
        source=source,
        time_epoch=times,
        distance_m=np.array([75.0, 150.0, 225.0, 300.0, 375.0]),
        azimuth_deg=az,
        elevation_deg=np.full(n_rays, 2.0),
        latitude=np.full(n_rays, 36.0),
        longitude=np.full(n_rays, -97.0),
        altitude_m=np.full(n_rays, 300.0),
        heading_deg=np.full(n_rays, np.nan),
        scan_number=np.ones(n_rays),
        fields={field: {"data": data, "units": units}},
        coordinate_source=np.full(n_rays, "file site attributes"),
        provenance={"north_referenced": True},
        warnings=[],
    )


def _render(rays, when=_WHEN):
    png, bounds, metadata = render_lidar_to_png(rays, when, grid_size=256)
    return np.asarray(Image.open(BytesIO(png))), bounds, metadata


def test_north_referenced_sector_maps_westward():
    pixels, bounds, meta = _render(_ppi_rays(_clamps_source(mobile=True)))
    assert bounds[2] < -97  # every gate is west of the instrument
    assert bounds[0] > -97.01
    assert 0.2 < (pixels[:, :, 3] > 0).mean() < 0.8
    assert meta['rays'] == 121
    assert meta['units'] == 'm/s'
    assert meta['vmin'] == -meta['vmax']


def test_unknown_truck_orientation_is_refused():
    rays = _ppi_rays(_clamps_source(mobile=True))
    rays.provenance = {"north_referenced": False, "azimuth_reference": "Truck heading not recorded"}
    with pytest.raises(ValueError, match='Truck heading not recorded'):
        _render(rays)


def test_single_pointing_is_a_line_not_a_filled_sector():
    rays = _ppi_rays()
    rays.azimuth_deg[:] = 0
    pixels, bounds, meta = _render(rays)
    assert bounds[1] > 36  # north starts at the first range gate
    assert 0 < (pixels[:, :, 3] > 0).mean() < .02


def test_latest_scan_and_clock_limit_acquisition():
    rays = _ppi_rays()
    rays.scan_number[-10:] = 2
    _, _, meta = _render(rays, _WHEN - timedelta(seconds=2))
    assert meta['rays'] == 6
    assert datetime.fromisoformat(meta['end']) == _WHEN - timedelta(seconds=2)


def test_large_time_gap_does_not_join_scans_with_same_number():
    rays = _ppi_rays()
    rays.time_epoch[:-1] -= 30
    _, _, meta = _render(rays)
    assert meta['rays'] == 1


def test_the_scans_rays_are_drawn_when_given():
    rays = _ppi_rays()
    _, _, meta = render_lidar_to_png(rays, _WHEN, grid_size=128, ray_indices=np.arange(10, 40))
    assert meta['rays'] == 30


def test_masked_gates_are_transparent():
    rays = _ppi_rays()
    rays.fields['velocity']['data'][:] = np.ma.masked
    pixels, _, _ = _render(rays)
    assert not pixels[:, :, 3].any()


@pytest.mark.parametrize('change,match', [('stale', 'recent'), ('field', 'No velocity')])
def test_invalid_inputs(change, match):
    rays = _ppi_rays()
    if change == 'stale':
        rays.time_epoch -= 3600
    else:
        rays.fields = {}
    with pytest.raises(ValueError, match=match):
        _render(rays)


def test_north_crossing_stays_north_and_does_not_fill_south():
    rays = _ppi_rays()
    rays.azimuth_deg = np.linspace(350, 370, 121) % 360
    pixels, bounds, _ = _render(rays)
    assert bounds[1] > 36
    assert 0 < (pixels[:, :, 3] > 0).mean() < .5


def test_moving_origins_move_geographic_coverage():
    rays = _ppi_rays()
    _, original, _ = _render(rays)
    rays.longitude += .1
    _, moved, _ = _render(rays)
    assert moved[0] - original[0] == pytest.approx(.1, abs=1e-6)
    assert moved[2] - original[2] == pytest.approx(.1, abs=1e-6)


def test_sector_and_lidar_projection_share_north_clockwise_bearings():
    from core.scan_sector import _project
    from pyproj import Geod
    geod = Geod(ellps='WGS84')
    for az in (240, 270, 300):
        lat, lon = _project(38.94633, -97.214, az, 8000)
        bearing, _, distance = geod.inv(-97.214, 38.94633, lon, lat)
        assert bearing % 360 == pytest.approx(az, abs=.2)
        assert distance == pytest.approx(8000, abs=25)


def test_all_native_fields_render_with_their_own_units():
    rays = _ppi_rays(field='backscatter', units='km^-1 sr^-1')
    _, _, metadata = render_lidar_to_png(rays, _WHEN, 'backscatter', 256)
    assert metadata['field'] == 'backscatter'
    assert metadata['units'] == 'km^-1 sr^-1'
    assert metadata['vmin'] < metadata['vmax']


def test_min_intensity_hides_cells_below_the_threshold():
    rays = _ppi_rays()
    n_rays = len(rays.time_epoch)
    # intensity rises with range: 1.000 at the first gate (noise) to 1.040 at the last
    rays.fields["intensity"] = {"data": np.tile(np.linspace(1.0, 1.04, 5), (n_rays, 1)).astype(np.float32),
                                "units": ""}

    def drawn(**kw):
        png = render_lidar_to_png(rays, _WHEN, grid_size=256, **kw)[0]
        return (np.asarray(Image.open(BytesIO(png)))[:, :, 3] > 0).sum()

    every, filtered = drawn(), drawn(min_intensity=1.015)
    assert 0 < filtered < every                       # the near (noise) gates are gone, the rest drawn
    assert drawn(min_intensity=1.05) == 0             # above every cell: nothing left
    _, _, metadata = render_lidar_to_png(rays, _WHEN, grid_size=256, min_intensity=1.015)
    assert metadata["min_intensity"] == 1.015


def test_min_intensity_is_ignored_without_an_intensity_field():
    rays = _ppi_rays()
    plain = render_lidar_to_png(rays, _WHEN, grid_size=256)
    with_threshold = render_lidar_to_png(rays, _WHEN, grid_size=256, min_intensity=1.02)
    assert plain[0] == with_threshold[0] and with_threshold[2]["min_intensity"] is None
