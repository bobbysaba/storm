import io
from datetime import datetime, timezone

import numpy as np
from PIL import Image

import runtime_flags as rf
from core.radar_scan import RadarScan
from ui.map import radar_gl
from ui.map import radar_overlay as ro


def _scan(colormap="nws_ref", first_az=0.27):
    table = np.full(256, np.nan, dtype=np.float32)
    table[2:] = (np.arange(2, 256) - 2) * 0.5 - 32.0
    raw = np.random.default_rng(0).integers(0, 256, size=(360, 400), dtype=np.uint8)
    return RadarScan(
        site="KTST", product="N0B", scan_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw=raw, vmin=-32.0, vmax=90.0, units="dBZ", colormap=colormap,
        radar_lat=35.0, radar_lon=-97.0,
        azimuths_deg=(first_az + np.arange(360)) % 360.0,
        first_gate_m=500.0, gate_width_m=1000.0, value_table=table,
    )


def _decode(png):
    return np.asarray(Image.open(io.BytesIO(png)))


def setup_function():
    rf.reset_flags()


def test_enabled_only_when_opted_in_and_not_safe_or_lite():
    assert not radar_gl.enabled()
    rf.FLAGS.gpu_radar = True
    assert radar_gl.enabled()
    rf.FLAGS.lite_mode = True
    assert not radar_gl.enabled()
    rf.FLAGS.lite_mode = False
    rf.FLAGS.safe_map_mode = True
    assert not radar_gl.enabled()


def test_supports_live_reflectivity_only():
    assert radar_gl.supports(_scan())
    assert not radar_gl.supports(_scan(colormap="nws_vel"))


def test_payload_textures_are_lossless():
    scan = _scan()
    p = radar_gl.build_payload(scan)

    assert np.array_equal(_decode(p["data_png"]), scan.raw)
    assert np.array_equal(_decode(p["lut_png"])[0], ro._code_lut(scan))

    az = _decode(p["az_png"]).reshape(-1, 4).astype(np.int32)
    rows = ro._az_row_table(scan.azimuths_deg)
    decoded = np.where(az[:, 2] == 255, az[:, 0] + 256 * az[:, 1], -1)
    assert np.array_equal(decoded, rows)
    assert (az[:, 3] == 255).all()

    meta = p["meta"]
    assert (meta["nAz"], meta["nGates"]) == scan.raw.shape
    assert meta["azTexW"] * meta["azTexH"] == ro._AZ_BINS
    assert meta["bounds"] == ro._pixel_index(scan, 64).bounds
