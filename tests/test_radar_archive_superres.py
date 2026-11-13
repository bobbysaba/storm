"""Archive mode's sharp radar render of a paused frame (session 7, B16)."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from core.level2_radar_scan import Level2RadarScan
from ui.app.main_window_archive import MainWindowArchiveMixin
from ui.app.radar_superres_cache import RadarSuperresCache
from ui.map import radar_overlay as ro

T0 = datetime(2013, 5, 31, 23, 0, tzinfo=timezone.utc)


def _scan(field="reflectivity", colormap="nws_ref", seed=1, minutes=0, lat=35.333, lon=-97.278):
    """360 radials x 200 gates of 250 m, random values with gaps."""
    raw = np.random.default_rng(seed).uniform(-20, 60, size=(360, 200)).astype(np.float16)
    raw[::7, ::5] = np.nan
    return Level2RadarScan(
        site="KTLX", product="X", scan_time=T0 + timedelta(minutes=minutes), raw=raw,
        vmin=-32.0, vmax=90.0, units="", colormap=colormap, radar_lat=lat, radar_lon=lon,
        azimuths_deg=0.5 + np.arange(360.0), first_gate_m=2125.0, gate_width_m=250.0,
        tilt_deg=0.5, available_tilts=[0.5], available_products=[field], pyart_field=field,
    )


@pytest.fixture
def fresh_caches(monkeypatch):
    monkeypatch.setattr(ro, "_pixel_index_cache", OrderedDict())
    monkeypatch.setattr(ro, "_png_cache", OrderedDict())
    monkeypatch.setattr(ro, "_png_cache_bytes", 0)


# --- render_scan_to_png(crop_radius_m=) ---------------------------------------


def test_crop_is_a_square_centered_on_the_radar(fresh_caches):
    scan = _scan()
    radius_m = 50_000.0
    png, bounds, elapsed_ms = ro.render_scan_to_png(scan, 64, crop_radius_m=radius_m)
    assert isinstance(png, bytes) and png and elapsed_ms is not None

    cx, cy = ro._lonlat_to_merc(scan.radar_lon, scan.radar_lat)
    x_min, y_min = ro._lonlat_to_merc(bounds[0], bounds[1])
    x_max, y_max = ro._lonlat_to_merc(bounds[2], bounds[3])
    assert x_min == pytest.approx(cx - radius_m, abs=1.0)
    assert x_max == pytest.approx(cx + radius_m, abs=1.0)
    assert y_min == pytest.approx(cy - radius_m, abs=1.0)
    assert y_max == pytest.approx(cy + radius_m, abs=1.0)


def test_without_a_crop_the_image_covers_the_footprint(fresh_caches):
    scan = _scan()
    _, bounds, _ = ro.render_scan_to_png(scan, 64)
    assert bounds == ro.footprint_bounds(scan)
    _, tight, _ = ro.render_scan_to_png(scan, 64, crop_radius_m=200.0)
    assert (tight[2] - tight[0]) < (bounds[2] - bounds[0])


def test_cropped_renders_stay_out_of_the_frame_cache(fresh_caches):
    scan = _scan()
    _, _, first_ms = ro.render_scan_to_png(scan, 64, crop_radius_m=60_000.0)
    _, _, again_ms = ro.render_scan_to_png(scan, 64, crop_radius_m=60_000.0)
    assert first_ms is not None and again_ms is not None
    assert ro.cached_scan_png(scan, 64) is None
    assert not ro._png_cache


@pytest.mark.parametrize("crop", [None, 60_000.0])
@pytest.mark.parametrize("colormap,mask", [("nws_ref", False), ("nws_vel", True)])
def test_rendering_in_strips_gives_the_same_image(monkeypatch, fresh_caches, colormap, mask, crop):
    scan = _scan(colormap=colormap, field="velocity" if mask else "reflectivity")
    mask_scan = _scan(seed=2) if mask else None
    monkeypatch.setattr(ro, "RENDER_STRIP_PIXELS", 10_000_000)
    whole = ro.render_scan_to_png(scan, 300, mask_scan=mask_scan, crop_radius_m=crop)
    monkeypatch.setattr(ro, "_pixel_index_cache", OrderedDict())
    monkeypatch.setattr(ro, "_png_cache", OrderedDict())
    monkeypatch.setattr(ro, "RENDER_STRIP_PIXELS", 37 * 300)
    strips = ro.render_scan_to_png(scan, 300, mask_scan=mask_scan, crop_radius_m=crop)
    assert strips[0] == whole[0] and strips[1] == whole[1]


# --- pixel index cache ---------------------------------------------------------


def test_crop_radius_is_part_of_the_index_key(fresh_caches):
    scan = _scan()
    near = ro._pixel_index(scan, 64, 50_000.0)
    far = ro._pixel_index(scan, 64, 100_000.0)
    full = ro._pixel_index(scan, 64)
    assert len(ro._pixel_index_cache) == 3
    assert near.bounds != far.bounds != full.bounds
    assert ro._pixel_index(scan, 64, 50_000.0) is near


def test_a_cropped_index_is_shared_by_products_of_any_reach(fresh_caches):
    ref = _scan()
    vel = _scan(colormap="nws_vel", field="velocity")
    vel.raw = vel.raw[:, :120]        # shorter reach, like velocity's gates
    assert ro._pixel_index(ref, 64, 60_000.0) is ro._pixel_index(vel, 64, 60_000.0)
    assert ro._pixel_index(ref, 64) is not ro._pixel_index(vel, 64)


def test_the_sharp_crop_holds_reflectivitys_whole_reach():
    scan = _scan()
    radius = ro.superres_crop_radius_m(scan)
    ring_lon, ring_lat = scan.gate_lonlat(np.arange(0.0, 360.0, 0.25), ro.ARCHIVE_SUPERRES_RANGE_M)
    x_min, y_min, x_max, y_max = ro._crop_merc(scan, radius)
    x, y = ro._lonlat_to_merc(ring_lon, ring_lat)
    assert (x_min <= x.min() and x.max() <= x_max and y_min <= y.min() and y.max() <= y_max)
    assert radius > ro.ARCHIVE_SUPERRES_RANGE_M          # Mercator stretches it


def test_the_sharp_crop_depends_on_the_station_alone():
    ref = _scan()
    vel = _scan(colormap="nws_vel", field="velocity")
    vel.raw = vel.raw[:, :120]
    assert ro.superres_crop_radius_m(ref) == ro.superres_crop_radius_m(vel)


def test_only_one_sharp_size_index_is_kept(monkeypatch, fresh_caches):
    # a 4096 px index is 67 MB; small grids stand in for the sharp sizes here
    monkeypatch.setattr(ro, "_SHARP_INDEX_MIN_GRID", 96)
    scan = _scan()
    normal = [ro._pixel_index(scan, g) for g in (16, 24, 32, 40)]
    ro._pixel_index(scan, 128, 60_000.0)
    latest = ro._pixel_index(scan, 96, 60_000.0)
    sizes = sorted(k[4] for k in ro._pixel_index_cache)
    assert sizes == [16, 24, 32, 40, 96]                  # the older sharp one went
    assert ro._pixel_index(scan, 96, 60_000.0) is latest
    assert all(ro._pixel_index(scan, g) is i for g, i in zip((16, 24, 32, 40), normal))


def test_normal_indexes_are_still_capped(fresh_caches):
    scan = _scan()
    for g in (16, 24, 32, 40, 48, 56):
        ro._pixel_index(scan, g)
    assert sorted(k[4] for k in ro._pixel_index_cache) == [32, 40, 48, 56]


# --- size step-down and memory guard ----------------------------------------------


def test_sharp_render_steps_down_a_size_when_slow():
    assert ro.next_superres_size(4096, 1.8) == 4096                         # fast machine: unchanged
    assert ro.next_superres_size(4096, ro.ARCHIVE_SUPERRES_SLOW_S + 1) == 3072
    assert ro.next_superres_size(3072, ro.ARCHIVE_SUPERRES_SLOW_S + 1) == 2048
    assert ro.next_superres_size(2048, 30.0) == 2048                         # never below 2048


@pytest.mark.parametrize("memory,ok", [
    ((8192.0, 500.0), False),     # low: skip this render
    ((8192.0, 4096.0), True),
    ((-1.0, -1.0), True),         # unknown
    ((16384.0, -1.0), True),      # macOS: total only
])
def test_memory_guard_reads_available_system_memory(monkeypatch, memory, ok):
    import core.mem_probe
    monkeypatch.setattr(core.mem_probe, "system_memory_mb", lambda: memory)
    assert ro.sharp_render_memory_ok() is ok


# --- RadarSuperresCache ---------------------------------------------------------


def _key(tilt=0.5, station="KTLX", processing=""):
    return RadarSuperresCache.make_key(station, "reflectivity", tilt, T0, processing)


def test_cache_hit_and_miss():
    cache = RadarSuperresCache(capacity=3)
    assert cache.get(_key()) is None
    cache.put(_key(), b"png-bytes", [1, 2, 3, 4])
    assert cache.get(_key()) == (b"png-bytes", [1, 2, 3, 4])


def test_cache_evicts_oldest_at_capacity():
    cache = RadarSuperresCache(capacity=2)
    cache.put(_key(0.5), b"a", [])
    cache.put(_key(1.5), b"b", [])
    cache.put(_key(2.5), b"c", [])
    assert len(cache) == 2
    assert cache.get(_key(0.5)) is None
    assert cache.get(_key(1.5)) is not None and cache.get(_key(2.5)) is not None


def test_cache_get_marks_entry_most_recently_used():
    cache = RadarSuperresCache(capacity=2)
    cache.put(_key(0.5), b"a", [])
    cache.put(_key(1.5), b"b", [])
    cache.get(_key(0.5))
    cache.put(_key(2.5), b"c", [])
    assert cache.get(_key(1.5)) is None
    assert cache.get(_key(0.5)) is not None


def test_cache_keys_separate_stations_and_processing():
    assert _key(station="KTLX") != _key(station="KOUN")
    cache = RadarSuperresCache(capacity=4)
    cache.put(_key(), b"raw", [])
    assert cache.get(_key(processing="dealiased")) is None


# --- the archive window's sharp render ------------------------------------------------


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, value):
        self.emitted.append(value)


class _Timer:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1


class _Overlay:
    def __init__(self):
        self.injected = []

    def inject(self, png, bounds):
        self.injected.append((png, bounds))


class _Executor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append((fn, args))


class _Clock:
    def __init__(self, playing=False, speed=60, now=T0):
        self.is_playing = playing
        self.speed = speed
        self.current_time = now


class _Radar:
    station = "KTLX"

    def __init__(self, scan_times=()):
        self._times = sorted(scan_times)

    def next_scan_after(self, t):
        return next((s for s in self._times if s > t), None)


class _Window(MainWindowArchiveMixin):
    def __init__(self, scan, playing=False, scan_times=()):
        self._archive_render_generation = 3
        self._archive_radar = _Radar(scan_times)
        self._time_ctrl = _Clock(playing)
        self._radar_overlay = _Overlay()
        self._archive_superres_cache = RadarSuperresCache(capacity=12)
        self._archive_superres_executor = _Executor()
        self._archive_superres_timer = _Timer()
        self._archive_superres_ready = _Signal()
        self._archive_superres_size = ro.ARCHIVE_SUPERRES_GRID_SIZE
        self._archive_superres_busy = False
        self._archive_superres_wall_s = None
        self._archive_superres_pending = (3, "KTLX", scan)

    def key_for(self, scan):
        return RadarSuperresCache.make_key("KTLX", scan.pyart_field, scan.tilt_deg, scan.scan_time)


@pytest.fixture
def memory_ok(monkeypatch):
    monkeypatch.setattr(ro, "sharp_render_memory_ok", lambda: True)


def test_a_settled_frame_is_rendered_sharp_in_the_background(memory_ok):
    scan = _scan()
    win = _Window(scan)
    win._on_archive_superres_timer_fired()
    (fn, args), = win._archive_superres_executor.submitted
    assert fn == win._bg_render_archive_superres
    assert args[4] == 4096 and args[5] == ro.superres_crop_radius_m(scan)
    assert win._archive_superres_busy


def test_a_cached_sharp_frame_is_shown_without_rendering(memory_ok):
    scan = _scan()
    win = _Window(scan)
    win._archive_superres_cache.put(win.key_for(scan), b"sharp", [1, 2, 3, 4])
    win._on_archive_superres_timer_fired()
    assert win._radar_overlay.injected == [(b"sharp", [1, 2, 3, 4])]
    assert not win._archive_superres_executor.submitted


def test_low_memory_skips_the_render(monkeypatch):
    monkeypatch.setattr(ro, "sharp_render_memory_ok", lambda: False)
    win = _Window(_scan())
    win._on_archive_superres_timer_fired()
    assert not win._archive_superres_executor.submitted
    assert not win._archive_superres_busy


def test_a_frame_from_an_old_station_is_not_rendered(memory_ok):
    win = _Window(_scan())
    win._archive_render_generation = 4
    win._on_archive_superres_timer_fired()
    assert not win._archive_superres_executor.submitted


def test_while_playing_a_render_the_next_scan_would_beat_is_skipped(memory_ok):
    scan = _scan()
    # next scan 4 minutes on, at 60x: 4 s of wall time left
    win = _Window(scan, playing=True, scan_times=[T0, T0 + timedelta(minutes=4)])
    win._archive_superres_wall_s = 5.0
    assert win._sharp_render_would_be_stale(scan)
    win._on_archive_superres_timer_fired()
    assert not win._archive_superres_executor.submitted

    win._archive_superres_wall_s = 2.0
    assert not win._sharp_render_would_be_stale(scan)
    win._archive_superres_busy = True               # never queued behind a running one
    assert win._sharp_render_would_be_stale(scan)
    win._time_ctrl.is_playing = False               # paused: always
    assert not win._sharp_render_would_be_stale(scan)


def test_pausing_restarts_the_wait_for_the_frame_on_screen():
    win = _Window(_scan())
    win._on_archive_playing_changed(True)
    assert win._archive_superres_timer.started == 0
    win._on_archive_playing_changed(False)
    assert win._archive_superres_timer.started == 1
    win._archive_superres_pending = None
    win._on_archive_playing_changed(False)
    assert win._archive_superres_timer.started == 1


def test_the_finished_render_is_cached_and_shown():
    scan = _scan()
    win = _Window(scan)
    win._archive_superres_busy = True
    win._bg_render_archive_superres(3, "KTLX", scan, win.key_for(scan), 64, 60_000.0)
    assert not win._archive_superres_busy and win._archive_superres_wall_s is not None
    result, = win._archive_superres_ready.emitted
    win._on_archive_superres_ready(result)
    assert win._radar_overlay.injected == [(result["png"], result["bounds"])]
    assert win._archive_superres_cache.get(win.key_for(scan)) is not None


def test_a_render_for_a_frame_no_longer_on_screen_is_cached_not_shown():
    scan, later = _scan(), _scan(minutes=5)
    win = _Window(later)
    win._on_archive_superres_ready({"gen": 3, "station": "KTLX", "scan": scan, "key": win.key_for(scan),
                                    "png": b"old", "bounds": []})
    assert not win._radar_overlay.injected
    assert win._archive_superres_cache.get(win.key_for(scan)) == (b"old", [])


def test_a_render_for_an_old_station_is_dropped():
    scan = _scan()
    win = _Window(scan)
    win._archive_render_generation = 4
    win._on_archive_superres_ready({"gen": 3, "station": "KTLX", "scan": scan, "key": win.key_for(scan),
                                    "png": b"x", "bounds": []})
    assert not win._radar_overlay.injected and len(win._archive_superres_cache) == 0


def test_a_slow_render_steps_the_size_down(monkeypatch):
    import time
    scan = _scan()
    win = _Window(scan)
    ticks = iter([0.0, ro.ARCHIVE_SUPERRES_SLOW_S + 1])
    monkeypatch.setattr(time, "thread_time", lambda: next(ticks))
    monkeypatch.setattr(ro, "render_scan_to_png", lambda *a, **k: (b"png", [], 1.0))
    monkeypatch.setattr(ro, "ensure_pixel_index", lambda *a: None)
    win._bg_render_archive_superres(3, "KTLX", scan, win.key_for(scan), 4096, 60_000.0)
    assert win._archive_superres_size == 3072


def test_building_the_index_does_not_count_as_a_slow_render(monkeypatch):
    import time
    scan = _scan()
    win = _Window(scan)
    clock = {"cpu": 0.0}
    monkeypatch.setattr(time, "thread_time", lambda: clock["cpu"])

    def slow_index(*_a):
        clock["cpu"] += 10.0           # first sharp render for a station

    def quick_render(*_a, **_k):
        clock["cpu"] += 0.8
        return b"png", [], 800.0
    monkeypatch.setattr(ro, "ensure_pixel_index", slow_index)
    monkeypatch.setattr(ro, "render_scan_to_png", quick_render)
    win._bg_render_archive_superres(3, "KTLX", scan, win.key_for(scan), 4096, 60_000.0)
    assert win._archive_superres_size == 4096
