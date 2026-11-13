"""D29: the Video Studio panel (ui/studio/panel.py) and opening it in the
window: keyframes from the clock and map, the inspector, undo, preview,
projects, the wheel guard, its keys, and the window's side (archive bar,
suspended keys, Edit menu, Esc, layout)."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QObject, QPoint, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QWheelEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from archive.time_controller import TimeController
from core.studio import Project, View
from ui.app import main_window_studio
from ui.app.main_window import MainWindow
from ui.app.main_window_studio import STUDIO_SUSPENDS, MainWindowStudioMixin
from ui.click_mode import ClickModeController
from ui.shortcuts import ARCHIVE, ShortcutRegistry
from ui.studio import panel as panel_mod
from ui.studio.panel import StudioPanel

UTC = timezone.utc
T0 = datetime(2024, 4, 27, 20, 0, tzinfo=UTC)
A = View(lon=-98.9, lat=34.0, zoom=8.0)
B = View(lon=-98.5, lat=34.3, zoom=9.5)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


class FakeCapture(QObject):
    """The map: read_view hands back `view`; apply records what was shown."""
    frame_ready = pyqtSignal(object)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._w = window
        self.view = A
        self.applied = []

    def read_view(self, callback):
        callback(self.view)

    def apply(self, when, view):
        self.applied.append((when, view))
        self._w._time_ctrl.set_time(when)

    def begin(self, size, overlays, done):
        self.overlays = overlays
        done()

    def capture_when_ready(self):
        from PyQt6.QtGui import QImage
        img = QImage(40, 20, QImage.Format.Format_RGB32)
        img.fill(0)
        self.frame_ready.emit(img)

    def end(self):
        self.ended = True


class FakeArchiveBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = []

    def local_time_text(self):
        return "15:00 CDT / 14:00 MDT"

    def next_scan(self):
        self.calls.append("next_scan")

    def prev_scan(self):
        self.calls.append("prev_scan")

    def __getattr__(self, name):          # the table's playback keys
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda: self.calls.append(name)


class Window:
    """What the panel asks of the main window."""

    def __init__(self):
        self._time_ctrl = TimeController(T0)
        self.radar = {"station": "KTLX", "product": "reflectivity", "tilt": 0}
        self.radar_applied = []
        self.map_widget = SimpleNamespace(grab=lambda: SimpleNamespace(isNull=lambda: True))
        self._archive_controls = FakeArchiveBar()
        self.frames_saved = 0

    def studio_radar_state(self):
        return dict(self.radar)

    def studio_apply_radar(self, state):
        self.radar_applied.append(state)

    def studio_scan_times(self):
        return [T0 + timedelta(minutes=m) for m in range(0, 120, 4)]

    def _track_session_day(self):
        return T0.date()

    def _studio_save_frame(self):
        self.frames_saved += 1


@pytest.fixture
def studio(monkeypatch, tmp_path):
    monkeypatch.setattr("ui.studio.capture.MapCapture", FakeCapture)
    monkeypatch.setattr(panel_mod, "STUDIO_ROOT", tmp_path / "studio")
    w = Window()
    p = StudioPanel(w)
    p.resize(1200, 300)
    return w, p


def _add(w, p, minutes, view):
    w._time_ctrl.set_time(T0 + timedelta(minutes=minutes))
    p._capture.view = view
    p.seek(p.project.duration())
    p.add_keyframe()


# ── keyframes ──────────────────────────────────────────────
def test_keyframes_come_from_the_clock_and_map(studio):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)                        # 10 min at the default 300x: 2 s later
    ks = p.project.sorted()
    assert [(k.at, k.time, k.view) for k in ks] == [(0.0, T0, A), (2.0, T0 + timedelta(minutes=10), B)]
    assert ks[1].radar == {"station": "KTLX", "product": "reflectivity", "tilt": 0}
    assert p.selected is ks[1] and p.playhead == 2.0
    assert "Keyframe 2 added" in p._hint.text()
    p.add_keyframe()                          # nothing changed at the end: nothing added
    assert len(p.project.keyframes) == 2 and "No change" in p._hint.text()


def test_inside_a_move_a_keyframe_splits_it_and_on_one_it_retakes_it(studio):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)
    p.seek(1.0)
    w._time_ctrl.set_time(T0 + timedelta(minutes=3))
    p.add_keyframe()
    assert [k.at for k in p.project.sorted()] == [0.0, 1.0, 2.0]
    p.seek(1.0)
    w.radar = {"station": "KOAX", "product": "velocity", "tilt": 1}
    w._time_ctrl.set_time(T0 + timedelta(minutes=4))
    p.add_keyframe()
    mid = p.project.sorted()[1]
    assert len(p.project.keyframes) == 3 and mid.time == T0 + timedelta(minutes=4)
    assert mid.radar["station"] == "KOAX" and "updated" in p._hint.text()


def test_delete_and_undo_redo(studio):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)
    p.delete_selected()
    assert len(p.project.keyframes) == 1 and p._btn_undo.isEnabled()
    p.undo()
    assert len(p.project.keyframes) == 2 and p.selected is p.project.sorted()[1]
    p.redo()
    assert len(p.project.keyframes) == 1
    p.undo()
    p.undo()
    p.undo()
    assert p.project.keyframes == [] and not p._btn_undo.isEnabled()


# ── the inspector ──────────────────────────────────────────
def test_speed_hold_and_easing_edits(studio):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)
    p.select(p.project.sorted()[0])
    assert p._insp_stack.currentIndex() == 1 and p._kf_title.text() == "KEYFRAME 1 OF 2"
    p._speed.setEditText("60×")
    p._speed_edited()                         # 600 case s at 60x: 10 s
    assert p.project.segment_length(0) == pytest.approx(10.0)
    p._hold.setValue(1.0)
    p._hold.setValue(1.5)                     # typing in one field: one undo step
    assert p.project.sorted()[1].at == pytest.approx(11.5)
    p.undo()
    assert p.project.sorted()[1].at == pytest.approx(10.0)
    p._easing.setCurrentIndex(p._easing.findData("linear"))
    assert p.project.sorted()[0].easing == "linear"


def test_the_whole_movie_retimes_together(studio):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)
    p._retime(2.0)
    assert p.project.duration() == pytest.approx(4.0) and p._movie_len.value() == pytest.approx(4.0)
    p._movie_len.setValue(3.0)
    assert p.project.duration() == pytest.approx(3.0)


def test_a_field_ignores_the_wheel_until_clicked_into(studio):
    _w, p = studio
    p._hold.setEnabled(True)
    before = p._hold.value()
    wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(p._hold, wheel)
    assert p._hold.value() == before


# ── the playhead and preview ───────────────────────────────
def test_scrubbing_shows_that_moment_with_its_radar(studio):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)
    p.project.sorted()[1].radar = {"station": "KOAX", "product": "velocity", "tilt": 1}
    p.project.time_step = "smooth"
    p.seek(1.0, apply=True)
    when, view = p._capture.applied[-1]
    assert when == T0 + timedelta(minutes=5) and A.zoom < view.zoom < B.zoom
    assert w.radar_applied[-1]["station"] == "KTLX"
    p.seek(2.0, apply=True)
    assert w.radar_applied[-1]["station"] == "KOAX"
    p.project.time_step = "radar scans"
    p.seek(0.55, apply=True)                  # 2:45 into the case: the 20:00 scan
    assert p._capture.applied[-1][0] == T0


def test_preview_needs_two_keyframes_and_stops_on_close(studio):
    w, p = studio
    _add(w, p, 0, A)
    p.toggle_play()
    assert not p._play_timer.isActive() and not p._btn_play.isEnabled()
    _add(w, p, 10, B)
    p.seek(0.0)
    p.toggle_play()
    assert p._play_timer.isActive() and "Pause" in p._btn_play.text()
    seen = []
    p.closed.connect(lambda: seen.append(1))
    p._close()
    assert not p._play_timer.isActive() and seen == [1]


def test_case_clock_buttons_and_save_frame(studio):
    w, p = studio
    p._scan(1)
    p._scan(-1)
    assert w._archive_controls.calls == ["next_scan", "prev_scan"]
    p._nudge_clock(60)
    assert w._time_ctrl.current_time == T0 + timedelta(minutes=1)
    assert p._local.text() == "15:00 CDT / 14:00 MDT"
    p._btn_frame.click()
    assert w.frames_saved == 1


# ── projects ───────────────────────────────────────────────
def test_save_and_reopen_a_project(studio, monkeypatch, tmp_path):
    w, p = studio
    _add(w, p, 0, A)
    _add(w, p, 10, B)
    p._name.setText("el reno")
    p._renamed("el reno")
    p._fps.setCurrentIndex(p._fps.findData(24))
    path = tmp_path / "el reno.json"
    monkeypatch.setattr(panel_mod.QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    assert p._save() and not p._dirty and path.exists()
    assert p.folder() == panel_mod.STUDIO_ROOT / "20240427"
    p._new()                                  # saved: no question
    assert p.project.keyframes == [] and p._name.text() == "movie"
    assert p.load(path)
    assert p.project.name == "el reno" and p.project.fps == 24 and len(p.project.keyframes) == 2
    assert p._fps.currentData() == 24 and p.selected is p.project.sorted()[0]


def test_unsaved_changes_are_asked_about(studio, monkeypatch):
    w, p = studio
    _add(w, p, 0, A)
    answers = []
    monkeypatch.setattr(panel_mod.QMessageBox, "question",
                        lambda *a, **k: answers.append(1) or panel_mod.QMessageBox.StandardButton.Cancel)
    p._new()
    assert answers == [1] and len(p.project.keyframes) == 1


def test_export_waits_for_two_keyframes(studio):
    w, p = studio
    seen = []
    p.export_requested.connect(lambda: seen.append(1))
    _add(w, p, 0, A)
    assert not p._btn_export.isEnabled()
    _add(w, p, 10, B)
    p._btn_export.click()
    assert seen == [1]


# ── its keys ───────────────────────────────────────────────
def test_studio_keys_go_quiet_while_typing(studio, monkeypatch):
    _w, p = studio
    p._install_shortcuts()
    space = next(sc for sc in p._shortcuts if sc.key().toString() == "Space")
    p.show()
    assert space.isEnabled()
    monkeypatch.setattr(panel_mod, "is_text_input", lambda _w: True)
    p._on_focus_changed(None, None)
    assert not space.isEnabled()
    monkeypatch.setattr(panel_mod, "is_text_input", lambda _w: False)
    p._on_focus_changed(None, None)
    assert space.isEnabled()
    p.hide()
    assert not space.isEnabled()


def test_the_registry_keeps_suspended_keys_off_while_focus_moves(app):
    host = QWidget()
    reg = ShortcutRegistry(host, ARCHIVE)
    reg.bind("play", lambda: None)
    reg.bind("track_undo", lambda: None, QAction("Undo", host))
    (space,) = reg.bound["play"]
    reg.suspend(["play", "track_undo"])
    assert not space.isEnabled()
    reg.set_typing(False)                     # focus left a text box: stays off
    assert not space.isEnabled() and reg.bound["track_undo"][0].isEnabled()
    reg.set_typing(True)
    reg.resume(["play"])
    assert not space.isEnabled()              # still typing
    reg.set_typing(False)
    assert space.isEnabled()


# ── the window's side ──────────────────────────────────────
class Host(MainWindowStudioMixin, QWidget):
    _SHORTCUT_BUTTONS = MainWindow._SHORTCUT_BUTTONS


for _name in ("_init_shortcuts", "_click_if_shown", "_call_archive_controls", "_toggle_shortcut_overlay",
              "_on_escape_pressed", "_loop_key"):
    setattr(Host, _name, MainWindow.__dict__[_name])


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setattr("ui.studio.capture.MapCapture", FakeCapture)
    monkeypatch.setattr(panel_mod, "STUDIO_ROOT", tmp_path / "studio")
    base = Window()
    w = Host()
    w.resize(1280, 800)
    for name in ("_time_ctrl", "radar", "radar_applied", "map_widget"):
        setattr(w, name, getattr(base, name))
    for name in ("studio_radar_state", "studio_apply_radar", "studio_scan_times", "_track_session_day"):
        setattr(w, name, getattr(Window, name).__get__(w))
    w._archive = True
    w._map_container = QWidget(w)
    w._map_container.resize(1280, 800)
    w._archive_controls = FakeArchiveBar(w._map_container)
    w._pill_route_expanded = False
    w._request_layout = lambda *a: None
    w._layout_overlays = lambda: w.calls.append("layout")
    w._click_modes = ClickModeController(w)
    w.calls = []
    for name in ("_toggle_debug_panel", "_toggle_error_log_panel", "_open_case_package", "_export_case_package",
                 "_add_track_point_at_marker", "_delete_selected_track_point", "_undo_track_edit",
                 "_redo_track_edit"):
        setattr(w, name, lambda n=name: w.calls.append(n))
    from PyQt6.QtWidgets import QToolButton
    for attr in list(MainWindow._SHORTCUT_BUTTONS.values()) + ["btn_screenshot"]:
        setattr(w, attr, QToolButton(w))
    w._menu_actions = {name: QAction(text, w) for name, text in
                       (("track_undo", "Undo Track Edit"), ("track_redo", "Redo Track Edit"),
                        ("studio", "Video Studio"))}
    w._menu_actions["studio"].setCheckable(True)
    w.status_msg_label = QLabel()
    w._init_shortcuts()
    w.show()
    yield w
    w.close()


def test_opening_takes_the_archive_bars_place_and_its_keys(host):
    w = host
    w._menu_actions["studio"].trigger()              # File > Video Studio
    assert w._studio_open() and w._archive_controls.isHidden()
    assert w._menu_actions["studio"].isChecked()
    assert w._menu_actions["track_undo"].text() == "Undo Studio Edit"
    for action in STUDIO_SUSPENDS:
        assert all(not sc.isEnabled() for sc in w._shortcuts.bound.get(action, []))
    assert "layout" in w.calls
    w._on_escape_pressed()                           # Esc leaves
    assert not w._studio_open() and not w._archive_controls.isHidden()
    assert not w._menu_actions["studio"].isChecked()
    assert w._menu_actions["track_undo"].text() == "Undo Track Edit"
    assert all(sc.isEnabled() for sc in w._shortcuts.bound["play"])


def test_close_studio_button_and_toggle(host):
    w = host
    w._toggle_studio()
    w._studio._close()
    assert not w._studio_open()
    w._toggle_studio()
    w._toggle_studio()
    assert not w._studio_open()


def test_undo_follows_whats_open(host):
    w = host
    w._menu_actions["track_undo"].trigger()
    assert w.calls[-1] == "_undo_track_edit"
    w._open_studio()
    w._studio._capture.view = A
    w._studio.add_keyframe()
    w._menu_actions["track_undo"].trigger()
    assert w._studio.project.keyframes == [] and w.calls[-1] != "_undo_track_edit"
    w._menu_actions["track_redo"].trigger()
    assert len(w._studio.project.keyframes) == 1


def test_export_asks_where_then_renders(host, monkeypatch, tmp_path):
    w = host
    w._open_studio()
    started = []
    w._start_studio_export = lambda project, path: started.append(path)
    w._studio.project.format = "gif"
    monkeypatch.setattr(main_window_studio.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "clip"), ""))
    w._export_studio()
    assert started == [tmp_path / "clip.gif"]
    w._studio.project.format = "png"
    monkeypatch.setattr(main_window_studio.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    w._export_studio()
    assert started[-1] == tmp_path / "movie"
    monkeypatch.setattr(main_window_studio.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    w._export_studio()                              # canceled: nothing
    assert len(started) == 2



def test_save_frame_waits_for_the_map_and_writes_a_png(host, monkeypatch, tmp_path):
    w = host
    w._open_studio()
    w._studio.project.overlays.legend = True
    path = tmp_path / "frame"
    monkeypatch.setattr(main_window_studio.QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    w._studio_save_frame()
    assert (tmp_path / "frame.png").exists()
    assert "Frame saved: frame.png" in w._studio._hint.text()
