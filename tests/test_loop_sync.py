"""LoopSync (shared live loop time), frame picking and vehicle position tracks."""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
# QtWebEngine must be imported before any QApplication exists (a later test needs it)
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from core.vehicle import PositionTrack
from ui.loop_sync import RADAR, SATELLITE, TILE_WAIT_S, LoopSync, frame_at

T0 = datetime(2026, 5, 20, 21, 0, tzinfo=timezone.utc)


def _t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# frame_at

def test_frame_at_picks_latest_at_or_before():
    times = [_t(0), _t(5), _t(10)]
    assert frame_at(times, _t(7), 600) == 1
    assert frame_at(times, _t(10), 600) == 2
    assert frame_at(times, _t(30), 600) is None   # 20 min past the newest


def test_frame_at_before_first_and_empty():
    assert frame_at([_t(5)], _t(4), 600) is None
    assert frame_at([], _t(4), 600) is None


def test_frame_at_tolerance():
    times = [_t(0), _t(1)]
    assert frame_at(times, _t(4), 180) == 1
    assert frame_at(times, _t(4.5), 180) is None


# LoopSync

class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def sync():
    s = LoopSync(clock=_Clock())
    s.events = []
    s.vehicle_events = []
    s.time_changed.connect(lambda t, src: s.events.append((t, src)))
    s.vehicles_changed.connect(s.vehicle_events.append)
    return s


def test_set_time_emits_each_new_frame_and_live(sync):
    sync.set_time(_t(0))
    sync.set_time(_t(0))           # same frame: no repeat
    sync.set_time(_t(5))
    sync.set_time(None)
    assert sync.events == [(_t(0), RADAR), (_t(5), RADAR), (None, RADAR)]
    assert sync.loop_time is None


def test_disabled_sync_reports_live_and_stays_quiet(sync):
    sync.set_time(_t(0))
    sync.set_enabled(False)
    assert sync.loop_time is None
    sync.set_time(_t(5))
    assert sync.events == [(_t(0), RADAR), (None, RADAR)]
    sync.set_enabled(True)          # picks the held time back up
    assert sync.events[-1] == (_t(5), RADAR)


def test_source_change_is_reported(sync):
    sync.set_time(_t(0), SATELLITE)
    assert sync.events == [(_t(0), SATELLITE)]
    assert sync.source == SATELLITE


def test_vehicles_need_sync_on(sync):
    sync.set_vehicles(True)
    assert sync.vehicles and sync.vehicle_events == [True]
    sync.set_enabled(False)
    assert not sync.vehicles and sync.vehicle_events == [True, False]
    sync.set_enabled(True)
    assert sync.vehicle_events == [True, False, True]


def test_tile_wait(sync):
    clock = sync._clock
    assert sync.ready_to_step()
    sync.mark_tiles_pending()
    assert not sync.ready_to_step()
    sync.mark_tiles_idle()
    assert sync.ready_to_step()
    sync.mark_tiles_pending()
    clock.now += TILE_WAIT_S
    assert sync.ready_to_step()     # gave up waiting
    assert sync.ready_to_step()


# PositionTrack

def test_track_interpolates_between_fixes():
    tr = PositionTrack()
    tr.append(0, 35.0, -97.0)
    tr.append(60, 35.1, -97.2)
    lat, lon = tr.position_at(30)
    assert lat == pytest.approx(35.05) and lon == pytest.approx(-97.1)


def test_track_before_first_and_after_last():
    tr = PositionTrack()
    tr.append(100, 35.0, -97.0)
    tr.append(160, 35.1, -97.0)
    assert tr.position_at(99) is None
    assert tr.position_at(10_000) == (35.1, -97.0)


def test_track_holds_across_long_gaps():
    tr = PositionTrack(max_gap_s=120)
    tr.append(0, 35.0, -97.0)
    tr.append(600, 36.0, -97.0)
    assert tr.position_at(300) == (35.0, -97.0)


def test_track_thins_close_fixes_but_keeps_newest():
    tr = PositionTrack(min_spacing_s=15)
    for t in range(0, 31):          # 1 Hz for 30 s
        tr.append(t, 35.0 + t * 0.001, -97.0)
    assert len(tr) <= 4
    assert tr.position_at(30) == pytest.approx((35.03, -97.0))


def test_track_drops_old_and_out_of_order_fixes():
    tr = PositionTrack(keep_s=600, min_spacing_s=0)
    tr.append(0, 35.0, -97.0)
    tr.append(700, 35.5, -97.0)
    assert tr.position_at(0) is None            # older than keep_s behind newest
    tr.append(650, 40.0, -90.0)                 # late message: ignored
    assert tr.position_at(10_000) == (35.5, -97.0)


def test_track_ignores_missing_position():
    tr = PositionTrack()
    tr.append(0, None, -97.0)
    assert len(tr) == 0


# SatelliteControls synced mode

def test_satellite_controls_synced_disables_own_playback():
    from PyQt6.QtWidgets import QApplication
    from ui.controls.satellite_controls import SatelliteControls
    app = QApplication.instance() or QApplication([])
    assert app is not None   # held so it outlives the widgets below
    sc = SatelliteControls()
    sc.set_cache_size(5)
    assert sc._btn_play.isEnabled()
    sc._btn_play.setChecked(True)
    sc.set_synced(True)
    assert not sc.is_looping() and not sc._btn_play.isEnabled()
    assert not sc._synced_label.isHidden() and sc._speed_combo.isHidden()
    sc.set_cache_size(6)                    # new frames must not re-enable it
    assert not sc._frame_slider.isEnabled()
    sc.set_synced(False)
    assert sc._btn_play.isEnabled()
    assert sc._synced_label.isHidden() and not sc._speed_combo.isHidden()


# MESO frame thinning (satellite fetcher)

def test_meso_thinning_keeps_recent_frames_and_older_ones_sparse():
    from data.fetchers.satellite_fetcher import (
        MAX_FRAMES, MESO_KEEP_S, MESO_THIN_STRIDE_S, SatFrame, thin_meso_frames,
    )
    cache = []
    for m in range(60):                       # an hour of 1-minute MESO polls
        cache.append(SatFrame(timestamp=_t(m), png=b"x", bbox=[0, 0, 1, 1]))
        cache = thin_meso_frames(cache)
    times = [f.timestamp for f in cache]
    assert times[-MAX_FRAMES:] == [_t(m) for m in range(50, 60)]
    older = times[:-MAX_FRAMES]
    gaps = [(b - a).total_seconds() for a, b in zip(older, older[1:])]
    assert all(g >= MESO_THIN_STRIDE_S for g in gaps)
    assert (times[-1] - times[0]).total_seconds() <= MESO_KEEP_S
    assert (times[-1] - times[0]).total_seconds() >= 30 * 60   # covers the radar loop
    assert len(cache) <= MAX_FRAMES + MESO_KEEP_S // MESO_THIN_STRIDE_S
