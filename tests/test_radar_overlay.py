import io
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from core.level2_radar_scan import Level2RadarScan
from core.radar_scan import RadarScan
from ui.map import radar_overlay as ro


def _code_scan(first_az=0.5, n_az=360, n_gates=400, minutes=0, raw=None):
    """Packed-code reflectivity scan: code c maps to (c - 2) * 0.5 dBZ, 0/1 missing."""
    table = np.full(256, np.nan, dtype=np.float32)
    table[2:] = (np.arange(2, 256) - 2) * 0.5
    if raw is None:
        raw = np.full((n_az, n_gates), 100, dtype=np.uint8)   # 49 dBZ everywhere
    return RadarScan(
        site="KTST",
        product="N0B",
        scan_time=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        raw=raw,
        vmin=-32.0,
        vmax=90.0,
        units="dBZ",
        colormap="nws_ref",
        radar_lat=35.0,
        radar_lon=-97.0,
        azimuths_deg=(first_az + np.arange(n_az) * (360.0 / n_az)) % 360.0,
        first_gate_m=500.0,
        gate_width_m=1000.0,
        value_table=table,
    )


def _rgba(png_bytes):
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))


def _pixel_of(scan, bounds, grid, az_deg, range_m):
    lon, lat = scan.gate_lonlat(az_deg, range_m)
    x, y = ro._lonlat_to_merc(lon, lat)
    x0, y0 = ro._lonlat_to_merc(bounds[0], bounds[1])
    x1, y1 = ro._lonlat_to_merc(bounds[2], bounds[3])
    col = int(round(float((x - x0) / (x1 - x0)) * (grid - 1)))
    row = int(round(float((y1 - y) / (y1 - y0)) * (grid - 1)))
    return row, col


def test_scan_exposes_values_and_grids_without_storing_them():
    scan = _code_scan()
    assert scan.raw.dtype == np.uint8
    assert scan.data.dtype == np.float32
    assert scan.data[0, 0] == 49.0
    assert scan.lats.shape == scan.lons.shape == scan.raw.shape
    assert "lats" not in vars(scan) and "lons" not in vars(scan)


def test_forward_and_inverse_geometry_round_trip():
    for cls_scan in (_code_scan(), _level2_scan()):
        az = np.array([10.0, 123.4, 271.0])
        rng = np.array([5_000.0, 150_000.0, 390_000.0])
        lon, lat = cls_scan.gate_lonlat(az, rng)
        back_rng, back_az = cls_scan.polar_from_lonlat(lon, lat)
        np.testing.assert_allclose(back_rng, rng, rtol=1e-6)
        np.testing.assert_allclose(back_az, az, atol=1e-4)


def _level2_scan():
    return Level2RadarScan(
        site="KTST",
        product="REF",
        scan_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw=np.full((360, 400), 30.0, dtype=np.float16),
        vmin=-32.0,
        vmax=90.0,
        units="dBZ",
        colormap="nws_ref",
        radar_lat=35.0,
        radar_lon=-97.0,
        azimuths_deg=0.5 + np.arange(360.0),
        first_gate_m=500.0,
        gate_width_m=1000.0,
        tilt_deg=0.5,
        available_tilts=[0.5],
        available_products=["reflectivity"],
        pyart_field="reflectivity",
    )


def test_render_places_a_single_radial_at_its_azimuth():
    # one hot radial centered on 90.5 deg; first radial deliberately not at north
    raw = np.zeros((360, 400), dtype=np.uint8)
    first_az = 200.5
    hot_row = int(round((90.5 - first_az) % 360.0))
    raw[hot_row, :] = 100
    scan = _code_scan(first_az=first_az, raw=raw, minutes=1)
    grid = 512

    png, bounds, elapsed_ms = ro.render_scan_to_png(scan, grid)
    img = _rgba(png)

    assert elapsed_ms is not None
    r, c = _pixel_of(scan, bounds, grid, 90.5, 200_000.0)
    assert img[r, c, 3] > 0
    for other_az in (88.0, 93.0, 270.5):
        r, c = _pixel_of(scan, bounds, grid, other_az, 200_000.0)
        assert img[r, c, 3] == 0


def test_render_is_cached_per_scan_and_grid():
    scan = _code_scan(minutes=2)
    assert ro.cached_scan_png(scan, 256) is None

    png, bounds, elapsed_ms = ro.render_scan_to_png(scan, 256)
    again, again_bounds, again_ms = ro.render_scan_to_png(scan, 256)

    assert elapsed_ms is not None and again_ms is None
    assert again is png and again_bounds == bounds
    assert ro.cached_scan_png(scan, 256) == (png, bounds)
    assert ro.cached_scan_png(scan, 128) is None
    assert ro.cached_scan_png(_code_scan(minutes=3), 256) is None


def test_reflectivity_below_threshold_and_missing_codes_are_transparent():
    raw = np.full((360, 400), 100, dtype=np.uint8)
    raw[:90, :] = 10      # 4 dBZ, under the 8 dBZ cut
    raw[90:180, :] = 0    # missing
    scan = _code_scan(raw=raw, minutes=4)
    grid = 256
    png, bounds, _ = ro.render_scan_to_png(scan, grid)
    img = _rgba(png)

    for az, drawn in ((45.0, False), (135.0, False), (225.0, True)):
        r, c = _pixel_of(scan, bounds, grid, az, 200_000.0)
        assert bool(img[r, c, 3] > 0) is drawn


def test_velocity_is_masked_by_reflectivity_scan_with_different_geometry():
    ref_raw = np.zeros((720, 800), dtype=np.uint8)
    ref_raw[:360, :] = 100                      # precipitation in the eastern half only
    ref = _code_scan(first_az=0.25, n_az=720, n_gates=800, raw=ref_raw, minutes=5)
    ref.gate_width_m = 500.0
    ref.first_gate_m = 250.0

    vel = _code_scan(minutes=5)
    vel.product = "N0U"
    vel.colormap = "nws_vel"
    vel.vmin, vel.vmax = -75.0, 75.0
    grid = 256

    png, bounds, _ = ro.render_scan_to_png(vel, grid, mask_scan=ref)
    img = _rgba(png)

    r, c = _pixel_of(vel, bounds, grid, 90.0, 200_000.0)
    assert img[r, c, 3] > 0
    r, c = _pixel_of(vel, bounds, grid, 270.0, 200_000.0)
    assert img[r, c, 3] == 0


def test_azimuth_table_wraps_and_leaves_sector_gaps_empty():
    full = ro._az_row_table(np.array([359.5, 0.5, 1.5] + list(np.arange(2.5, 359.0, 1.0))))
    bins_per_deg = ro._AZ_BINS // 360
    assert full[0] == 1                         # 0.005 deg -> radial centered on 0.5
    assert full[-1] == 0                        # 359.995 deg -> radial centered on 359.5
    assert (full >= 0).all()

    sector = ro._az_row_table(np.arange(0.5, 90.0, 1.0))
    assert (sector[: 90 * bins_per_deg] >= 0).all()
    assert (sector[100 * bins_per_deg: 350 * bins_per_deg] == -1).all()
