"""Lidar fixed-point stares: reading NSSL's "fp" files
(providers/nssl/archive/lidar_stare_archive_fetcher.py) and the stares
window (ui/dialogs/lidar_stare_dialog.py)."""
from datetime import date, datetime, timezone

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from providers.nssl.archive import lidar_stare_archive_fetcher as stares
from providers.nssl.archive.raw_lidar_archive_fetcher import LidarAsset
from ui.dialogs.lidar_stare_dialog import LidarStareDialog, binned, column_width, ray_spacing, to_num, usable_top_m

T0 = datetime(2022, 5, 13, 14, tzinfo=timezone.utc).timestamp()
TRUCK = next(s for s in stares.KNOWN_STARE_SOURCES if s.platform_id == "DLTRUCK1-DL2-FP")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _write(path, offsets, elevation=None, gates=4):
    """A stare file the way the CLAMPS processing writes one (netCDF4):
    *gates* real gates after the blind ones (a fixed 35 m/s)."""
    import xarray as xr
    n = len(offsets)
    blind = stares.BLIND_GATES
    velocity = np.tile(np.arange(gates, dtype=np.float32), (n, 1)) - 1      # -1, 0, 1, 2 m/s
    intensity = np.full((n, gates), 1.05, dtype=np.float32)
    intensity[:, -1] = 1.001
    backscatter = np.full((n, gates), 1e-5, dtype=np.float32)
    backscatter[:, 0] = -999
    height = np.arange(gates) * 0.03 + 0.015

    def with_blind(real, value):
        return np.hstack([np.full((n, blind), value, dtype=np.float32), real])
    velocity, intensity, backscatter = (with_blind(velocity, 35.0), with_blind(intensity, 1.05),
                                        with_blind(backscatter, 1e-5))
    height = np.concatenate([np.arange(blind) * 0.003, height])
    cbh = np.where(np.arange(n) % 2, 1.5, -1.0).astype(np.float32)
    ds = xr.Dataset(
        {"base_time": ((), np.int64(T0)), "time_offset": (("time",), np.asarray(offsets, dtype=float)),
         "elevation": (("time",), np.asarray(elevation if elevation is not None else [90.0] * n, dtype=np.float32)),
         "velocity": (("time", "height"), velocity, {"units": "m/s", "comment": "Positive values are towards the lidar"}),
         "intensity": (("time", "height"), intensity),
         "backscatter": (("time", "height"), backscatter),
         "cbh": (("time",), cbh, {"units": "km AGL"}),
         "height": (("height",), height.astype(np.float32), {"units": "km AGL"})},
        attrs={"scan_type": "Step-Stare"})
    ds.to_netcdf(path, engine="h5netcdf")
    return path


def test_a_stare_file_reads_as_time_by_height_with_w_up_positive(tmp_path):
    stare = stares.parse_stare(_write(tmp_path / "fp.cdf", [2, 0, 1, 3], elevation=[90, 90, 70, 90]), TRUCK)
    assert (stare.instrument, stare.name) == ("DLTRUCK1", "Lidar truck")
    assert len(stare) == 3 and list(stare.time_epoch - T0) == [0, 2, 3]       # sorted; the 70° ray left out
    assert stare.height_m == pytest.approx([15, 45, 75, 105])                 # the blind gates left out
    assert list(stare.fields) == ["w", "intensity", "backscatter"]
    assert list(stare.fields["w"]["data"][0]) == [-1, 0, 1, 2]                 # velocity as stored: up is +
    assert stare.fields["backscatter"]["data"][0, 1] == pytest.approx(-5.0)    # log10
    assert np.isnan(stare.fields["backscatter"]["data"][0, 0])                # -999
    assert stare.cloud_base_m[0] == pytest.approx(1500) and np.isnan(stare.cloud_base_m[1])   # -1: none


def test_a_file_cut_short_on_the_server_says_so(tmp_path):
    path = _write(tmp_path / "fp.cdf", list(range(400)), gates=200)
    whole = path.stat().st_size
    with path.open("r+b") as f:
        f.truncate(32768)
    with pytest.raises(Exception) as caught:
        stares.parse_stare(path, TRUCK)
    message = stares.incomplete_file_message(caught.value)
    assert message.startswith("file incomplete on THREDDS (33 KB of") and str(round(whole / 1e3)) in message
    assert stares.incomplete_file_message(ValueError("something else")) is None


def test_only_the_sessions_rays_are_kept(tmp_path):
    stare = stares.parse_stare(_write(tmp_path / "fp.cdf", [0, 1, 2, 3]), TRUCK, span=(T0 + 1, T0 + 3))
    assert list(stare.time_epoch - T0) == [1, 2] and stare.fields["w"]["data"].shape == (2, 4)
    assert stare.fields["w"]["data"].dtype == np.float16 and stare.fields["intensity"]["data"].dtype == np.float32


def test_the_trucks_two_streams_merge_and_another_grid_is_left_out(tmp_path):
    a = stares.parse_stare(_write(tmp_path / "a.cdf", [10, 11]), TRUCK)
    b = stares.parse_stare(_write(tmp_path / "b.cdf", [0, 1]), TRUCK)
    c = stares.parse_stare(_write(tmp_path / "c.cdf", [5], gates=3), TRUCK)
    merged = stares.merge_stares([a, b, c])
    assert list(merged.time_epoch - T0) == [0, 1, 10, 11] and merged.files == ["a.cdf", "b.cdf"]
    assert merged.warnings == ["c.cdf: another height grid, not shown"]
    assert stares.merge_stares([]) is None


def test_every_lidar_has_a_stare_stream_on_the_checker():
    assert {s.datastream for s in stares.KNOWN_STARE_SOURCES} == {
        "dltruckdlfpDL1.b1", "dltruckdlfpDL2.b1", "clampsdlfpC1.b1", "clampsdlfpC2.b1"}
    from providers.nssl.paths import all_folders
    checked = {path for _w, _h, path, _r in all_folders()}
    assert all(s.path in checked for s in stares.KNOWN_STARE_SOURCES)


def test_the_fetcher_finds_by_lidar_and_reports_an_incomplete_file(app, tmp_path, monkeypatch):
    good = _write(tmp_path / "good.cdf", [0, 1, 2])
    listed = {("DLTRUCK1-DL1-FP", date(2022, 5, 13)): ["good.cdf"], ("CLAMPS1-FP", date(2022, 5, 13)): ["cut.cdf"]}

    def discover(source, day):
        if source.platform_id == "CLAMPS2-FP":
            raise OSError("catalog down")
        return [LidarAsset(source, n) for n in listed.get((source.platform_id, day), [])]

    def download(kind, url, cache_dir, filename, cancel=None, progress=None):
        if filename == "cut.cdf":
            raise OSError("Unable to open file (truncated file: eof = 32768, sblock->base_addr = 0, "
                          "stored_eof = 51886740)")
        return good
    monkeypatch.setattr(stares, "discover_raw_lidar", discover)
    monkeypatch.setattr(stares.clamps_common, "download_to_cache", download)
    fetcher = stares.ArchiveLidarStareFetcher(datetime(2022, 5, 13, tzinfo=timezone.utc))
    found, ready, failed = [], [], []
    fetcher.found.connect(found.append)
    fetcher.ready.connect(lambda i, s: ready.append((i, s)))
    fetcher.failed.connect(lambda i, r: failed.append((i, r)))
    fetcher._find()
    assert set(found[0]) == {"DLTRUCK1", "CLAMPS1", "CLAMPS2"} and found[0]["CLAMPS2"] is None
    fetcher._load("DLTRUCK1")
    assert ready[0][0] == "DLTRUCK1" and len(ready[0][1]) == 3
    fetcher.load("DLTRUCK1")                                    # loaded once
    assert len(ready) == 2 and ready[1][1] is ready[0][1]
    fetcher._load("CLAMPS1")
    assert failed == [("CLAMPS1", "cut.cdf: file incomplete on THREDDS (33 KB of 52 MB)")]


def _stare(times, gates=4):
    n = len(times)
    w = np.tile(np.arange(gates, dtype=np.float16), (n, 1))
    intensity = np.full((n, gates), 1.05, dtype=np.float32)
    intensity[:, -1] = 1.001
    return stares.LidarStare("DLTRUCK1", "Lidar truck", T0 + np.asarray(times, dtype=float),
                             np.arange(gates) * 30.0 + 15, {"w": {"data": w, "units": "m/s", "label": "Vertical velocity (up +)"},
                                                            "intensity": {"data": intensity, "units": "SNR + 1"}},
                             np.where(np.arange(n) % 2, 600.0, np.nan))


def test_columns_average_rays_leave_gaps_blank_and_threshold_first():
    stare = _stare([0, 1, 2, 3, 100, 101])
    assert ray_spacing(stare.time_epoch) == 1.0
    assert column_width(stare.time_epoch, T0, T0 + 10) == 2.0
    assert column_width(stare.time_epoch, T0, T0 + 86400) == pytest.approx(86400 / 1500)
    edges, data = binned(stare, "w", 1.01, T0, T0 + 120, 10)
    assert len(edges) == 13 and data.shape == (12, 4)
    assert list(data[0, :3]) == [0, 1, 2] and np.ma.is_masked(data[0, 3])      # the noisy top gate
    assert data.mask[1:10].all() and not data.mask[10, 0]                       # nothing between 4 s and 100 s
    _, unthresholded = binned(stare, "w", None, T0, T0 + 120, 10)
    assert unthresholded[0, 3] == 3


def test_the_view_starts_at_the_top_of_the_usable_signal():
    stare = _stare(range(20), gates=60)
    stare.fields["intensity"]["data"][:, 30:] = 1.0
    assert usable_top_m(stare) == pytest.approx(max(1000.0, (29 * 30 + 15) * 1.2))


def test_the_window_loads_the_picked_lidar_and_reads_out_a_gate(app):
    window = LidarStareDialog()
    asked = []
    window.lidar_picked.connect(asked.append)
    window.set_lidars([("DLTRUCK1", "Lidar truck"), ("CLAMPS1", "CLAMPS1 trailer")], current="CLAMPS1")
    assert asked == ["CLAMPS1"] and "Loading" in window._ax.texts[0].get_text()
    window.set_failed("CLAMPS1", "file incomplete on THREDDS (33 KB of 52 MB)")
    assert "incomplete" in window._ax.texts[0].get_text()
    window._lidar_combo.setCurrentIndex(0)
    assert asked == ["CLAMPS1", "DLTRUCK1"]
    stare = _stare([0, 1, 2, 3, 600, 601])
    window.set_stare("CLAMPS1", stare)                           # not the lidar picked now: ignored
    assert window._stare is None
    window.set_stare("DLTRUCK1", stare)
    assert window._title.text().startswith("Lidar truck · stares · 2022-05-13")
    assert window.min_intensity() == pytest.approx(1.010)
    text = window.readout_at(float(to_num(T0 + 1)), 0.045)
    assert text.startswith("14:00:01Z  height 45 m  vertical velocity (up +) 1.00 m/s") and "cloud base 0.60 km" in text
    assert "masked" in window.readout_at(float(to_num(T0 + 1)), 0.105)
    assert window.readout_at(float(to_num(T0 + 300)), 0.045) == ""            # between the stares
    window.set_time(datetime.fromtimestamp(T0 + 2, timezone.utc))
    assert window._clock_line.get_xdata()[0] == pytest.approx(to_num(T0 + 2))
    window._field_combo.setCurrentIndex(window._field_combo.findData("intensity"))
    assert window._colorbar.ax.get_ylabel().startswith("intensity")
    window.set_colour_range(1.0, 1.1)
    assert window._image.get_clim() == (1.0, 1.1)
    window.close()
