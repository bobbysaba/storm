import gzip
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

import archive.fetchers.radar_archive_fetcher as raf
from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher

_SCAN_TIME = datetime(2013, 5, 31, 23, 9, 57, tzinfo=timezone.utc)
_PREFIX = "KTLX20130531_230957"


@pytest.fixture
def fetcher():
    f = ArchiveRadarFetcher("KTLX", datetime(2013, 5, 31, tzinfo=timezone.utc))
    yield f
    f.shutdown()


def _response(status, content=b""):
    return SimpleNamespace(status_code=status, content=content)


# A1: dates before about 2016

def test_listing_keeps_each_scans_exact_key(fetcher):
    xml = (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Contents><Key>2013/05/31/KTLX/{_PREFIX}_V06.gz</Key></Contents>"
        "<Contents><Key>2013/05/31/KTLX/KTLX20130531_230000_V06_MDM</Key></Contents>"
        "</ListBucketResult>"
    )
    assert fetcher._parse_s3_listing(xml) == {_SCAN_TIME: f"2013/05/31/KTLX/{_PREFIX}_V06.gz"}


def test_download_uses_the_listed_key_and_gunzips(fetcher, monkeypatch):
    raw = b"AR2V0006" + b"level2 payload"
    fetcher._index_keys = {_SCAN_TIME: f"2013/05/31/KTLX/{_PREFIX}_V06.gz"}
    requested = []

    def fake_get(url, timeout=60, stream=True):
        requested.append(url.split("/")[-1])
        if url.endswith("_V06.gz"):
            return _response(200, gzip.compress(raw))
        return _response(404)

    monkeypatch.setattr(raf.requests, "get", fake_get)
    assert fetcher._download_scan(_SCAN_TIME) == raw
    assert requested == [f"{_PREFIX}_V06.gz"]


def test_download_without_a_listed_key_tries_gz_variants_last(fetcher, monkeypatch):
    requested = []

    def fake_get(url, timeout=60, stream=True):
        requested.append(url.split("/")[-1])
        return _response(404)

    monkeypatch.setattr(raf.requests, "get", fake_get)
    assert fetcher._download_scan(_SCAN_TIME) is None
    assert requested == [
        f"{_PREFIX}_V06", f"{_PREFIX}_V03", _PREFIX,
        f"{_PREFIX}_V06.gz", f"{_PREFIX}_V03.gz", f"{_PREFIX}.gz",
    ]


def test_download_returns_plain_bytes_unchanged(fetcher, monkeypatch):
    monkeypatch.setattr(
        raf.requests, "get",
        lambda url, timeout=60, stream=True: _response(200, b"AR2V0006plain")
        if url.endswith("_V06") else _response(404),
    )
    assert fetcher._download_scan(_SCAN_TIME) == b"AR2V0006plain"


def test_download_uses_the_endpoints_bucket():
    from data import endpoints
    assert raf._S3_BASE == endpoints.NEXRAD_LEVEL2_BUCKET


# A2 and A3: split cuts and spectrum width

def _sweep(moment, value, angle=0.5, azimuths=(0, 90, 180, 270), cut=0):
    return [SimpleNamespace(
        header=SimpleNamespace(el_angle=angle, az_angle=az, el_num=cut),
        vol_consts=SimpleNamespace(lat=35.3, lon=-97.3),
        moments={moment: (SimpleNamespace(first_gate=0.25, gate_width=0.25),
                          np.array([value, value], dtype=np.float32))},
    ) for az in azimuths]


def _split_cut_volume():
    return SimpleNamespace(sweeps=[_sweep(b"REF", 30), _sweep(b"VEL", 10), _sweep(b"SW", 5)])


def test_velocity_uses_the_split_cut_that_has_it(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _split_cut_volume())
    scan = fetcher._decode(_SCAN_TIME, b"", product="velocity", tilt_idx=0)
    assert scan.pyart_field == "velocity"
    assert scan.tilt_index == 1
    assert {"reflectivity", "velocity", "spectrum_width"} <= set(scan.available_products)
    np.testing.assert_allclose(scan.raw.astype(np.float32), 10 * 1.94384, rtol=1e-3)


def test_spectrum_width_is_in_knots(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _split_cut_volume())
    scan = fetcher._decode(_SCAN_TIME, b"", product="spectrum_width", tilt_idx=0)
    np.testing.assert_allclose(scan.raw.astype(np.float32), 5 * 1.94384, rtol=1e-3)


def test_missing_product_raises_instead_of_showing_reflectivity(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _split_cut_volume())
    with pytest.raises(RuntimeError, match="No differential_phase data"):
        fetcher._decode(_SCAN_TIME, b"", product="differential_phase", tilt_idx=0)


def test_tilts_use_the_scan_strategy_angles_in_volume_order(fetcher, monkeypatch):
    volume = SimpleNamespace(
        sweeps=[_sweep(b"REF", 30, angle=0.6, cut=1), _sweep(b"VEL", 10, angle=0.4, cut=2),
                _sweep(b"REF", 30, angle=0.9, cut=3), _sweep(b"REF", 30, angle=0.5, cut=4)],
        vcp_info=SimpleNamespace(els=[SimpleNamespace(el_angle=a) for a in (0.48, 0.48, 0.9, 0.48)]),
    )
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: volume)
    scan = fetcher._decode(_SCAN_TIME, b"", product="reflectivity", tilt_idx=3)
    assert scan.available_tilts == [0.5, 0.5, 0.9, 0.5]   # SAILS rescan stays last
    assert scan.tilt_index == 3


def test_product_change_during_parse_keeps_the_requested_result(fetcher, monkeypatch):
    def parse(*args):
        fetcher._product = "reflectivity"
        fetcher._tilt_idx = 2
        return _split_cut_volume()

    monkeypatch.setattr(fetcher, "_get_parsed", parse)
    key = (_SCAN_TIME, "velocity", 0, None)
    fetcher._decode_and_cache(_SCAN_TIME, b"", key)
    assert fetcher._decoded_cache[key].pyart_field == "velocity"



# D26: dealiasing

def _velocity_volume(nyquist=None):
    volume = _split_cut_volume()
    if nyquist is not None:
        for radial in volume.sweeps[1]:
            radial.radial_consts = SimpleNamespace(nyq_vel=nyquist)
    return volume


def test_velocity_options_are_part_of_the_velocity_cache_key_only(fetcher):
    raw_key = fetcher._decode_key(_SCAN_TIME)
    fetcher.set_velocity_options(raf.VelocityOptions(dealias=True))
    assert fetcher._decode_key(_SCAN_TIME) == raw_key            # reflectivity unaffected
    fetcher._product = "velocity"
    assert fetcher._decode_key(_SCAN_TIME)[3] == raf.VelocityOptions(dealias=True)
    fetcher.set_velocity_options(raf.VelocityOptions())
    assert fetcher._decode_key(_SCAN_TIME)[3] is None           # raw velocity key as before


def test_switching_dealias_redisplays_velocity_only(fetcher, monkeypatch):
    seen = []
    monkeypatch.setattr(fetcher, "on_time_changed", seen.append)
    fetcher._current_archive_time = _SCAN_TIME
    fetcher.set_velocity_options(raf.VelocityOptions(dealias=True))
    assert seen == []                                            # reflectivity on screen
    fetcher._product = "velocity"
    fetcher.set_velocity_options(raf.VelocityOptions())
    fetcher.set_velocity_options(raf.VelocityOptions())          # unchanged: no redo
    assert seen == [_SCAN_TIME]


def test_dealiased_scan_is_labeled_and_cached_under_its_options(fetcher, monkeypatch):
    import core.velocity_processing as vp
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _velocity_volume(28.04))
    monkeypatch.setattr(vp, "dealias", lambda data, nyq: data + 2 * nyq)
    key = (_SCAN_TIME, "velocity", 0, raf.VelocityOptions(dealias=True))
    fetcher._decode_and_cache(_SCAN_TIME, b"", key)
    scan = fetcher._decoded_cache[key]
    assert scan.velocity_processing == "dealiased (region-based, Nyquist 28.0 m/s)"
    np.testing.assert_allclose(scan.raw.astype(np.float32), (10 + 56.08) * 1.94384, rtol=1e-3)


def test_dealiasing_without_a_nyquist_says_raw(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _velocity_volume())
    scan = fetcher._decode(_SCAN_TIME, b"", product="velocity", tilt_idx=0,
                           velocity=raf.VelocityOptions(dealias=True))
    assert scan.velocity_processing == "raw: no Nyquist velocity recorded, not dealiased"
    np.testing.assert_allclose(scan.raw.astype(np.float32), 10 * 1.94384, rtol=1e-3)


def test_a_failed_dealias_falls_back_to_raw_and_says_so(fetcher, monkeypatch):
    import core.velocity_processing as vp
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _velocity_volume(28.0))
    monkeypatch.setattr(vp, "dealias", lambda *a: (_ for _ in ()).throw(ValueError("too sparse")))
    scan = fetcher._decode(_SCAN_TIME, b"", product="velocity", tilt_idx=0,
                           velocity=raf.VelocityOptions(dealias=True))
    assert scan.product == "VEL" and scan.velocity_processing == "raw: dealiasing failed"
    np.testing.assert_allclose(scan.raw.astype(np.float32), 10 * 1.94384, rtol=1e-3)


def test_raw_velocity_has_no_processing_note(fetcher, monkeypatch):
    monkeypatch.setattr(fetcher, "_get_parsed", lambda *args: _velocity_volume(28.0))
    scan = fetcher._decode(_SCAN_TIME, b"", product="velocity", tilt_idx=0)
    assert scan.velocity_processing == ""

# A4: station switch

def test_no_signals_after_shutdown_and_files_kept_until_work_ends(fetcher):
    started, release = threading.Event(), threading.Event()
    path = Path(fetcher._tmpdir) / "held.dat"
    path.write_bytes(b"data")
    emitted = []
    fetcher.error.connect(emitted.append)

    def work():
        started.set()
        release.wait(3)
        assert path.read_bytes() == b"data"
        fetcher._emit("error", "obsolete")

    future = fetcher._pool.submit(work)
    assert started.wait(2)
    fetcher.shutdown()
    assert path.exists()
    release.set()
    future.result(timeout=3)
    assert emitted == []
    deadline = time.time() + 3
    while Path(fetcher._tmpdir).exists() and time.time() < deadline:
        time.sleep(0.01)
    assert not Path(fetcher._tmpdir).exists()


def test_shutdown_clears_caches_and_ignores_later_calls(fetcher):
    fetcher._parsed_cache[_SCAN_TIME] = object()
    fetcher._decoded_cache[(_SCAN_TIME, "reflectivity", 0, None)] = object()
    fetcher.shutdown()
    assert not fetcher._parsed_cache and not fetcher._decoded_cache
    fetcher._index = [_SCAN_TIME]
    fetcher.on_time_changed(_SCAN_TIME)
    assert not fetcher._pending_fetches


def test_idle_parse_is_released(fetcher, monkeypatch):
    monkeypatch.setattr(raf, "PARSED_IDLE_S", 0.0)
    fetcher._parsed_cache[_SCAN_TIME] = object()
    fetcher._parsed_used -= 1
    deadline = time.time() + 7
    while fetcher._parsed_cache and time.time() < deadline:
        time.sleep(0.05)
    assert not fetcher._parsed_cache


# A5: seeks

def test_a_seek_drops_stale_queued_work_and_fetches_the_new_scan_first(fetcher, monkeypatch):
    times = [_SCAN_TIME + timedelta(minutes=5 * i) for i in range(40)]
    fetcher._index = list(times)
    fetcher._index_pos = {t: i for i, t in enumerate(times)}
    gate, started, lock = threading.Event(), [], threading.Lock()

    def slow_download(scan_time):
        with lock:
            started.append(scan_time)
        gate.wait(5)
        return b"raw level-2 bytes"

    monkeypatch.setattr(fetcher, "_download_scan", slow_download)
    monkeypatch.setattr(fetcher, "_decode_and_cache", lambda *args: None)

    fetcher.on_time_changed(times[2])        # no neighbours until it is in
    deadline = time.time() + 2
    while not started and time.time() < deadline:
        time.sleep(0.01)
    assert started == [times[2]]
    fetcher.on_time_changed(times[30] + timedelta(seconds=10))
    queued = {key[1] for key in fetcher._queued}
    assert times[30] in queued and queued <= {times[2], times[30]}
    gate.set()
    deadline = time.time() + 5
    while len(started) < 1 + 9 and time.time() < deadline:
        time.sleep(0.01)
    after = started[1:]
    assert after[0] == times[30]
    assert after[1:5] == [times[31], times[29], times[32], times[28]]   # nearest first, ahead first
    assert not set(after) & set(times[:26])


def test_decoded_scans_outside_the_window_are_evicted(fetcher):
    times = [_SCAN_TIME + timedelta(minutes=5 * i) for i in range(40)]
    fetcher._index = list(times)
    fetcher._index_pos = {t: i for i, t in enumerate(times)}
    fetcher._raw_cache[times[20]] = "present"
    fetcher._target_scan = times[20]
    fetcher._ensure_fetched = lambda t: None
    # decoded long ago, raw file already gone
    fetcher._decoded_cache[(times[2], "reflectivity", 0, None)] = object()
    fetcher._decoded_cache[(times[21], "reflectivity", 0, None)] = object()
    fetcher._maintain_buffer(times[20])
    assert list(fetcher._decoded_cache) == [(times[21], "reflectivity", 0, None)]


# tilt picker

def test_tilt_list_shows_each_angle_once_and_answers_with_sweep_indices(monkeypatch):
    app = QApplication.instance() or QApplication([])  # noqa: F841 (kept alive)
    from ui.controls.radar_controls import RadarControls
    monkeypatch.setattr(RadarControls, "_refresh_product_availability", lambda self, site: None)
    controls = RadarControls()
    controls.configure_for_archive(True)
    angles = [0.5, 0.5, 0.9, 0.9, 1.3, 1.3, 1.8, 2.4, 3.1, 0.5]   # VCP 12 split cuts + SAILS
    controls.set_archive_tilts(angles, current_index=1)
    shown = [controls._tilt_combo.itemText(i) for i in range(controls._tilt_combo.count())]
    assert shown == ["0.5°", "0.9°", "1.3°", "1.8°", "2.4°", "3.1°"]
    assert controls.current_tilt_index() == 0
    picked = []
    controls.tilt_changed.connect(picked.append)
    controls._tilt_combo.setCurrentIndex(2)          # 1.3°
    assert picked == [4]
    controls.select_tilt(3)                          # sweep 3 is 0.9°
    assert controls._tilt_combo.currentText() == "0.9°" and picked[-1] == 2


def test_next_scan_after_returns_the_first_later_scan(fetcher):
    at = lambda h, m: datetime(2013, 5, 31, h, m, tzinfo=timezone.utc)
    fetcher._index = [at(22, 0), at(22, 5), at(22, 10)]
    assert fetcher.next_scan_after(at(21, 0)) == at(22, 0)
    assert fetcher.next_scan_after(at(22, 5)) == at(22, 10)    # strictly after
    assert fetcher.next_scan_after(at(22, 10)) is None


# turned off (RADAR's "show on map")

def test_turned_off_fetches_nothing_and_on_catches_up(fetcher, monkeypatch):
    times = [_SCAN_TIME + timedelta(minutes=5 * i) for i in range(10)]
    fetcher._index = list(times)
    fetcher._index_pos = {t: i for i, t in enumerate(times)}
    asked = []
    monkeypatch.setattr(fetcher, "_ensure_fetched", asked.append)
    monkeypatch.setattr(fetcher, "_maintain_buffer", lambda t: None)
    fetcher.set_active(False)
    fetcher.on_time_changed(times[3])
    assert asked == []
    fetcher.set_active(True)
    assert asked == [times[3]]


def test_turning_off_drops_queued_work_and_the_kept_parse(fetcher):
    future = SimpleNamespace(cancel=lambda: True)
    fetcher._queued[("fetch", _SCAN_TIME)] = future
    fetcher._pending_fetches.add(_SCAN_TIME)
    fetcher._raw_cache[_SCAN_TIME] = None
    fetcher._parsed_cache = {_SCAN_TIME: object()}
    fetcher.set_active(False)
    assert not fetcher._queued and not fetcher._pending_fetches and not fetcher._parsed_cache


def test_turned_on_again_shows_the_cached_scan_again(fetcher):
    fetcher._index = [_SCAN_TIME]
    fetcher._index_pos = {_SCAN_TIME: 0}
    fetcher._decoded_cache[fetcher._decode_key(_SCAN_TIME)] = scan = object()
    fetcher._raw_cache[_SCAN_TIME] = "present"
    shown = []
    fetcher.scan_ready.connect(shown.append)
    fetcher.on_time_changed(_SCAN_TIME)
    fetcher.set_active(False)
    fetcher.set_active(True)
    QApplication.processEvents()
    assert shown == [scan, scan]


def test_archive_show_on_map_starts_on_and_never_drives_the_live_radar(monkeypatch):
    app = QApplication.instance() or QApplication([])  # noqa: F841 (kept alive)
    from ui.controls.radar_controls import RadarControls
    monkeypatch.setattr(RadarControls, "_refresh_product_availability", lambda self, site: None)
    controls = RadarControls()
    controls.configure_for_archive(True)
    assert controls.is_data_enabled() and controls._chk_show_data.isVisibleTo(controls)
    live, archive = [], []
    controls.radar_toggled.connect(live.append)
    controls.archive_data_toggled.connect(archive.append)
    controls._chk_show_data.setChecked(False)
    controls._chk_show_data.setChecked(True)
    assert archive == [False, True] and live == []
