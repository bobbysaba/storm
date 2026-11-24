"""ui/dialogs/lidar_scan_dialog.py: a lidar's scans in their own window,
with the field, intensity threshold, colours, readout and zoom."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from core.lidar_scans import LidarScan
from ui.dialogs.lidar_scan_dialog import LidarScanDialog, default_range, field_data, scan_edges, scan_xy

T0 = datetime(2024, 4, 27, 20, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _rays(n_rays=8, gates=5):
    az = np.arange(n_rays) * (360.0 / n_rays)
    velocity = np.tile(np.linspace(-10, 10, gates), (n_rays, 1)).astype(np.float32)
    intensity = np.full((n_rays, gates), 1.02, dtype=np.float32)
    intensity[:, -1] = 1.001                     # the farthest gate is noise
    return SimpleNamespace(
        distance_m=np.arange(1, gates + 1) * 1000.0, azimuth_deg=az, elevation_deg=np.zeros(n_rays),
        latitude=np.full(n_rays, 35.0), longitude=np.full(n_rays, -97.0),
        fields={"velocity": {"data": velocity, "units": "m/s"}, "intensity": {"data": intensity, "units": ""}})


def _scans(rays, count=2):
    idx = np.arange(len(rays.azimuth_deg))
    return [LidarScan(idx, T0 + timedelta(minutes=10 * i), T0 + timedelta(minutes=10 * i, seconds=30), 0.0,
                      source=rays) for i in range(count)]


def test_gates_are_placed_east_and_north_of_the_lidar():
    rays = _rays()
    x, y, h = scan_xy(rays, np.arange(8))
    assert x.shape == (8, 5)
    assert (x[0, 0], y[0, 0]) == pytest.approx((0.0, 1.0))     # due north, 1 km
    assert (x[2, 2], y[2, 2]) == pytest.approx((3.0, 0.0))     # due east, 3 km
    assert np.allclose(h, 0.0)
    ex, ey = scan_edges(rays, np.arange(8))
    assert ex.shape == (9, 6)
    # the first ray's cells start half an azimuth step west of north, half a gate in
    assert (ex[0, 0], ey[0, 0]) == pytest.approx((0.5 * np.sin(np.radians(-22.5)), 0.5 * np.cos(np.radians(-22.5))))


def test_the_threshold_masks_noise_and_velocity_keeps_the_maps_scale():
    rays = _rays()
    data = field_data(rays, np.arange(8), "velocity", 1.005)
    assert data.mask[:, -1].all() and not data.mask[:, 0].any()
    assert default_range("velocity", data) == (-30.0, 30.0)
    assert field_data(rays, np.arange(8), "velocity", None).mask is np.ma.nomask or \
        not field_data(rays, np.arange(8), "velocity", None).mask.any()


def test_the_window_shows_the_clocks_scan_and_reads_out_a_gate(app):
    rays = _rays()
    scans = _scans(rays)
    window = LidarScanDialog()
    shown = []
    window.scan_changed.connect(shown.append)
    window.set_location("Lidar truck · stop 1 of 1", scans, "Heading: recorded in the file",
                        T0 + timedelta(minutes=12))
    assert window.current_scan() is scans[1] and shown[-1] is scans[1]
    assert window._title.text().startswith("Lidar truck · stop 1 of 1  ·  PPI")
    assert "Heading: recorded" in window._subtitle.text()
    text = window.readout_at(0.0, 2.0)                         # 2 km north
    assert "range 2.00 km" in text and "az 0.0°" in text and "velocity -5.00" in text
    assert "intensity 1.0200" in text
    assert window.readout_at(40.0, 40.0) == ""                  # off the scan
    # following the clock, then stepping stops it
    window.set_time(T0 + timedelta(minutes=1))
    assert window.current_scan() is scans[0]
    window._step(1)
    assert window.current_scan() is scans[1] and not window._chk_follow.isChecked()
    window.set_time(T0)
    assert window.current_scan() is scans[1]
    window.close()


def test_threshold_colours_zoom_and_draw_on_map(app):
    rays = _rays()
    window = LidarScanDialog()
    asked = []
    window.min_intensity_changed.connect(lambda v: asked.append(("threshold", v)))
    window.draw_on_map_toggled.connect(lambda on: asked.append(("map", on)))
    window.set_location("CLAMPS1 trailer · NWC", _scans(rays, 1))
    window._intensity_slider.setValue(5)
    window._on_intensity_settled()
    assert window.min_intensity() == pytest.approx(1.005) and asked[-1] == ("threshold", pytest.approx(1.005))
    assert window._data.mask[:, -1].all()
    window.set_colour_range(-5, 5)
    assert window._mesh.get_clim() == (-5, 5)
    window._on_reset_colours()
    assert window._mesh.get_clim() == (-30, 30)
    home = (window._ax.get_xlim(), window._ax.get_ylim())
    window._on_scroll(SimpleNamespace(inaxes=window._ax, xdata=0.0, ydata=0.0, button="up"))
    assert window._ax.get_xlim()[1] < home[0][1]
    window._chk_map.setChecked(True)
    assert asked[-1] == ("map", True)
    window.close()
