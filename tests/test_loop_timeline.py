"""The loop timeline and its routing to the driving drawer."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from tests.test_main_window_loop import FakeRadarControls, FakeSatControls, Host, _t
from ui.widgets.loop_timeline import LoopTimeline


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _state(tl, **kw):
    args = dict(frame_time=_t(10), now=_t(28), looping=False, live=False, can_step=True,
                speed_labels=["0.5×", "1×", "2×"], speed_index=1)
    args.update(kw)
    tl.set_state(**args)


# ── widget ─────────────────────────────────────────────────────────────

def test_label_shows_time_and_age_while_looping(app):
    tl = LoopTimeline()
    _state(tl)
    assert tl.time_label.text() == "21:10Z · −18m"
    assert tl.time_label.property("looping") is True and not tl.btn_live.isChecked()
    _state(tl, live=True, frame_time=_t(25))
    assert tl.time_label.text() == "21:25Z" and tl.btn_live.isChecked()
    assert tl.time_label.property("looping") is False
    _state(tl, looping=True)
    assert tl.btn_play.text() == "⏸"
    _state(tl, can_step=False)
    assert not tl.btn_play.isEnabled() and not tl.strip.isEnabled()


def test_buttons_emit_requests(app):
    tl = LoopTimeline()
    _state(tl)
    got = []
    tl.play_clicked.connect(lambda: got.append("play"))
    tl.step_requested.connect(lambda d: got.append(d))
    tl.live_clicked.connect(lambda: got.append("live"))
    tl.speed_chosen.connect(lambda i: got.append(("speed", i)))
    tl.btn_back.click(); tl.btn_play.click(); tl.btn_fwd.click(); tl.btn_live.click()
    tl.btn_speed.menu().actions()[2].trigger()
    assert got == [-1, "play", 1, "live", ("speed", 2)]
    assert tl.btn_speed.text() == "1×"            # the window's next state picks the label
    # SYNC and VEH moved to the settings pill: no ⋯ menu, no Vehicles button
    assert not hasattr(tl, "btn_more") and not hasattr(tl, "btn_vehicles")


def test_ticks_and_hollow_satellite_frames(app):
    tl = LoopTimeline()
    tl.resize(600, 40)
    strip = tl.strip
    strip.resize(208, 26)
    radar = [_t(m) for m in (0, 5, 10)]
    tl.set_ticks(radar, [_t(-20), _t(4)], tolerance_s=180)
    assert strip.span() == (_t(-20), _t(10))
    assert strip.x_of(_t(-20)) == 4 and strip.x_of(_t(10)) == 204
    assert strip.time_at(104) == _t(-5)
    assert strip.satellite_hollow(_t(-20))          # no radar scan within 3 min after
    assert not strip.satellite_hollow(_t(4))        # the 21:05 scan shows it


def test_scrub_emits_on_release(app):
    tl = LoopTimeline()
    strip = tl.strip
    strip.resize(208, 26)
    tl.set_ticks([_t(0), _t(10)], [])
    got = []
    tl.scrub_requested.connect(got.append)

    def mouse(kind, x):
        ev = QMouseEvent(kind, QPointF(x, 10), QPointF(x, 10), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(strip, ev)
    mouse(QMouseEvent.Type.MouseButtonPress, 4)
    mouse(QMouseEvent.Type.MouseMove, 104)
    assert got == []
    mouse(QMouseEvent.Type.MouseButtonRelease, 104)
    assert got == [_t(5)]


# ── routing ────────────────────────────────────────────────────────────

class _Playback:
    """The playback API both drawers share, recording what the timeline asks."""

    def _init_playback(self):
        self.calls = []
        self.speed = 1
        self.row_visible = True

    def toggle_loop(self):
        self.calls.append("toggle")

    def step_frame(self, d):
        self.calls.append(("step", d))

    def request_frame(self, i):
        self.calls.append(("frame", i))
        self.frame = i

    def stop_loop(self):
        self.calls.append("stop")

    def jump_to_latest(self):
        self.calls.append("live")

    def speed_labels(self):
        return ["0.5×", "1×"]

    def speed_index(self):
        return self.speed

    def set_speed_index(self, i):
        self.speed = i


class Radar(_Playback, FakeRadarControls):
    speed_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._init_playback()

    def set_playback_row_visible(self, v):
        self.row_visible = v


class Sat(_Playback, FakeSatControls):
    speed_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._init_playback()

    def is_at_latest_frame(self):
        return self.frame in (None, 2)

    def current_frame(self):
        return 2 if self.frame is None else self.frame


class TimelineHost(Host):
    def __init__(self, radar_on=True):
        super().__init__(radar_on)
        rc = Radar()
        rc.n, rc.frame = 4, 3
        self.radar_controls = rc
        self.satellite_controls = Sat()
        self.loop_timeline = LoopTimeline()
        self.layouts = 0

    def _request_layout(self):
        self.layouts += 1


def _timeline_host(app, **kw):
    h = TimelineHost(**kw)
    h._init_loop_sync()
    QApplication.processEvents()
    return h


def test_timeline_drives_radar(app):
    h = _timeline_host(app)
    tl, rc = h.loop_timeline, h.radar_controls
    assert not tl.isHidden() and rc.row_visible is False
    assert tl.btn_live.isChecked() and tl.time_label.text() == "21:15Z"
    assert tl.strip.radar == [_t(m) for m in (0, 5, 10, 15)]
    assert tl.strip.satellite == [_t(m) for m in (-10, 0, 10)]
    tl.btn_play.click(); tl.btn_back.click(); tl.btn_live.click()
    tl.btn_speed.menu().actions()[0].trigger()
    assert rc.calls == ["toggle", ("step", -1), "live"] and rc.speed == 0
    tl.scrub_requested.emit(_t(6))                        # nearest scan: 21:05
    assert rc.calls[-2:] == ["stop", ("frame", 1)]


def test_label_and_chip_follow_the_loop(app):
    h = _timeline_host(app)
    rc = h.radar_controls
    rc.frame = 1
    rc.frame_requested.emit(1)
    QApplication.processEvents()
    assert h.loop_timeline.time_label.text().startswith("21:05Z · −")
    assert h.loop_chip.isHidden()                         # the timeline shows it instead
    assert h.loop_timeline.strip.cursor_time == _t(5)


def test_driver_switches_to_satellite_when_radar_is_off(app):
    h = _timeline_host(app)
    tl = h.loop_timeline
    h._radar_on = False
    h.radar_controls.radar_toggled.emit(False)
    QApplication.processEvents()
    assert tl.strip.radar == []
    tl.btn_play.click()
    assert h.satellite_controls.calls == ["toggle"] and "toggle" not in h.radar_controls.calls
    assert tl.time_label.text() == "21:10Z"


def test_hidden_when_nothing_can_loop(app):
    h = _timeline_host(app, radar_on=False)
    h.satellite_controls.mode = ""
    h.satellite_controls.mode_changed.emit("")
    QApplication.processEvents()
    assert h.loop_timeline.isHidden()


def test_radar_drawer_never_shows_veh(app, monkeypatch):
    from ui.controls.radar_controls import RadarControls
    monkeypatch.setattr(RadarControls, "_refresh_product_availability", lambda self, site: None)
    rc = RadarControls()
    rc.show()
    assert rc._btn_play.isVisible()                       # no timeline: playback row shows
    assert not hasattr(rc, "_btn_sync") and not rc._btn_sync_vehicles.isVisible()
    rc.set_playback_row_visible(False)
    assert not rc._playback_row.isVisible()
    rc.set_sync_vehicles(True)                            # state still lives here
    assert rc.is_sync_vehicles()
    _KEEP_RADAR.append(rc)


_KEEP_RADAR = []   # controls live as long as the app does; collecting them mid-run can abort pytest
