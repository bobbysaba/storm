"""MainWindowLoopMixin: radar drives the live loop, satellite and vehicles follow."""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton

from core.observation import Observation
from core.vehicle import Vehicle
from data.fetchers.satellite_fetcher import SatFrame
from ui.app.main_window_loop import MainWindowLoopMixin
from ui.loop_sync import RADAR

T0 = datetime(2026, 5, 20, 21, 0, tzinfo=timezone.utc)


def _t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class FakeMap(QObject):
    satellite_idle = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.vehicles = {}        # id -> (lat, lon, past)
        self.removed = []
        self.sat_opacity = None
        self.sat_times = []
        self.follow = None
        self.plots_visible = None

    def add_vehicle(self, vid, lat, lon, color, icon, hover_text=None, past=False):
        self.vehicles[vid] = (lat, lon, past)

    def remove_vehicle(self, vid):
        self.vehicles.pop(vid, None)
        self.removed.append(vid)

    def set_satellite_opacity(self, o):
        self.sat_opacity = o

    def set_satellite_time(self, iso):
        self.sat_times.append(iso)

    def set_follow(self, on):
        self.follow = on

    def follow_move(self, lat, lon):
        pass

    def set_station_plots_visible(self, on):
        self.plots_visible = on


class FakeRadarControls(QObject):
    radar_toggled = pyqtSignal(bool)
    frame_requested = pyqtSignal(int)
    loop_toggled = pyqtSignal(bool)
    site_changed = pyqtSignal(str)
    product_changed = pyqtSignal(str)
    sync_toggled = pyqtSignal(bool)
    sync_vehicles_toggled = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.frame = 0
        self.n = 0
        self.looping = False
        self.sync = True
        self.vehicles = False
        self.jumped = 0

    def current_site(self):
        return "KTLX"

    def current_product(self):
        return "N0B"

    def is_looping(self):
        return self.looping

    def is_at_latest_frame(self):
        return self.frame >= self.n - 1

    def current_frame(self):
        return self.frame

    def is_sync_enabled(self):
        return self.sync

    def is_sync_vehicles(self):
        return self.sync and self.vehicles

    def jump_to_latest(self):
        self.jumped += 1


class FakeSatControls(QObject):
    frame_requested = pyqtSignal(int)
    loop_toggled = pyqtSignal(bool)
    mode_changed = pyqtSignal(str)

    def __init__(self, mode="conus"):
        super().__init__()
        self.mode = mode
        self.synced = False
        self.frame = None
        self.label = None

    def current_mode(self):
        return self.mode

    def set_synced(self, on):
        self.synced = on

    def is_synced(self):
        return self.synced

    def opacity(self):
        return 0.8

    def set_frame(self, i):
        self.frame = i

    def set_scan_time(self, text, when=None):
        self.label = text

    def is_looping(self):
        return False

    def is_at_latest_frame(self):
        return True

    def current_frame(self):
        return 0


class FakeFetcher(QObject):
    frames_updated = pyqtSignal(str, object)


class _Scan:
    def __init__(self, t):
        self.scan_time = t


class _Check:
    def isChecked(self):
        return True


class Host(MainWindowLoopMixin, QObject):
    def __init__(self, radar_on=True):
        QObject.__init__(self)
        self.map_widget = FakeMap()
        self.radar_controls = FakeRadarControls()
        self.satellite_controls = FakeSatControls()
        self._satellite_fetcher = FakeFetcher()
        self.loop_chip = QPushButton()
        self._loop_timer = QTimer()
        self._satellite_loop_timer = QTimer()
        self._chk_station_plots = _Check()
        self._radar_on = radar_on
        self._scan_cache = {"KTLX/N0B": [_Scan(_t(m)) for m in (0, 5, 10, 15)]}
        self.radar_controls.n = 4
        self.radar_controls.frame = 3
        self._satellite_cache = {"conus": [
            SatFrame(timestamp=_t(m), png=b"", bbox=[0, 0, 1, 1]) for m in (-10, 0, 10)
        ]}
        self._vehicles = {}
        self._vehicle_age_display_state = {}
        self._follow_mode = True

    # MainWindow pieces the mixin calls
    def _radar_data_enabled(self):
        return self._radar_on

    def _render_satellite_frame(self, frame):
        self.map_widget.set_satellite_time(frame.time_iso)

    def _layout_overlays(self):
        pass

    def _should_display_vehicle_obs(self, obs):
        return True

    def _obs_age_color(self, obs):
        return "#00ff00"

    def _archive_vehicle_hover_text(self, vid):
        return vid

    def _follow_target_id(self):
        return "truck1"


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _host_with_truck(app, **kw):
    h = Host(**kw)
    v = Vehicle("truck1", 36.0, -97.0)
    for m, lat in ((0, 35.0), (5, 35.5), (15, 36.0)):
        v.track.append(_t(m).timestamp(), lat, -97.0)
    v.latest_obs = Observation("truck1", 36.0, -97.0, _t(15))
    h._vehicles["truck1"] = v
    h._init_loop_sync()
    return h


def test_satellite_is_synced_while_radar_drives(app):
    h = _host_with_truck(app)
    assert h.satellite_controls.synced


def test_paused_on_old_radar_frame_moves_satellite_and_shows_chip(app):
    h = _host_with_truck(app)
    h.radar_controls.frame = 1                      # 21:05Z
    h.radar_controls.frame_requested.emit(1)
    assert h._loop_sync.loop_time == _t(5)
    assert h.satellite_controls.frame == 1          # 21:00Z frame
    assert h.map_widget.sat_times[-1].startswith("2026-05-20T21:00")
    assert not h.loop_chip.isHidden() and "21:05Z" in h.loop_chip.text()


def test_satellite_dims_when_no_frame_is_close_enough(app):
    h = _host_with_truck(app)
    h._satellite_cache["conus"] = [SatFrame(timestamp=_t(-30), png=b"", bbox=[0, 0, 1, 1])]
    h.radar_controls.frame = 0
    h.radar_controls.frame_requested.emit(0)
    assert h.satellite_controls.label == "no frame"
    assert h.map_widget.sat_opacity == pytest.approx(0.8 * 0.3)


def test_back_to_live_restores_satellite_and_hides_chip(app):
    h = _host_with_truck(app)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    h.radar_controls.frame = 3
    h.radar_controls.frame_requested.emit(3)
    assert h._loop_sync.loop_time is None
    assert h.satellite_controls.frame == 2
    assert h.loop_chip.isHidden()


def test_loop_playing_on_latest_frame_is_still_a_loop_time(app):
    h = _host_with_truck(app)
    h.radar_controls.looping = True
    h.radar_controls.loop_toggled.emit(True)
    assert h._loop_sync.loop_time == _t(15)


def test_conus_step_waits_for_tiles(app):
    h = _host_with_truck(app)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    assert not h._loop_step_ready()
    h.map_widget.satellite_idle.emit()
    assert h._loop_step_ready()


def test_vehicles_follow_only_when_enabled(app):
    h = _host_with_truck(app)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    assert h.map_widget.vehicles == {}              # VEH off: markers untouched
    h.radar_controls.sync_vehicles_toggled.emit(True)
    lat, lon, past = h.map_widget.vehicles["truck1"]
    assert lat == pytest.approx(35.5) and past
    assert h.map_widget.follow is False and h.map_widget.plots_visible is False


def test_vehicle_without_fix_at_loop_time_is_hidden_then_restored(app):
    h = _host_with_truck(app)
    late = Vehicle("chase2", 35.0, -98.0)
    late.track.append(_t(12).timestamp(), 35.0, -98.0)
    late.latest_obs = Observation("chase2", 35.0, -98.0, _t(12))
    h._vehicles["chase2"] = late
    h.radar_controls.sync_vehicles_toggled.emit(True)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    assert "chase2" in h.map_widget.removed
    h.radar_controls.frame = 3
    h.radar_controls.frame_requested.emit(3)        # live
    assert h.map_widget.vehicles["chase2"] == (35.0, -98.0, False)
    assert h.map_widget.vehicles["truck1"] == (36.0, -97.0, False)
    assert h.map_widget.follow is True and h.map_widget.plots_visible is True


def test_sync_off_returns_everything_to_live(app):
    h = _host_with_truck(app)
    h.radar_controls.sync_vehicles_toggled.emit(True)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    h.radar_controls.sync_toggled.emit(False)
    assert not h.satellite_controls.synced
    assert h.map_widget.vehicles["truck1"][2] is False
    assert h.loop_chip.isHidden()


def test_radar_off_hands_the_loop_to_satellite(app):
    h = _host_with_truck(app)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    h._radar_on = False
    h.radar_controls.radar_toggled.emit(False)
    assert not h.satellite_controls.synced
    assert h._loop_sync.loop_time is None


def test_chip_click_returns_radar_to_live(app):
    h = _host_with_truck(app)
    h.radar_controls.frame = 1
    h.radar_controls.frame_requested.emit(1)
    assert h._loop_sync.source == RADAR
    h._return_loop_to_live()
    assert h.radar_controls.jumped == 1
