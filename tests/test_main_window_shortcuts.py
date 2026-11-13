"""MainWindow's shortcut wiring, through its real _init_shortcuts with stand-in widgets."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtWidgets import QApplication, QLineEdit, QToolButton, QWidget

from ui.app.main_window import MainWindow
from ui.app.main_window_studio import MainWindowStudioMixin
from ui.click_mode import ClickModeController

_METHODS = ("_init_shortcuts", "_click_if_shown", "_call_archive_controls",
            "_toggle_shortcut_overlay", "_on_escape_pressed", "_loop_key")


class Host(MainWindowStudioMixin, QWidget):
    _SHORTCUT_BUTTONS = MainWindow._SHORTCUT_BUTTONS


for _name in _METHODS:
    setattr(Host, _name, MainWindow.__dict__[_name])


class FakeRadar:
    def __init__(self):
        self.calls = []

    def toggle_loop(self):
        self.calls.append("loop")

    def step_frame(self, delta):
        self.calls.append(delta)


class FakeArchive:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda: self.calls.append(name)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _host(archive=False):
    w = Host()
    w._archive = archive
    w._map_container = QWidget(w)
    w._pill_route_expanded = False
    w._request_layout = lambda *a: None
    w._click_modes = ClickModeController(w)
    w._toggle_debug_panel = lambda: None
    w._toggle_error_log_panel = lambda: None
    w._open_case_package = lambda: None
    w._export_case_package = lambda: None
    w.radar_controls = FakeRadar()
    w._archive_controls = FakeArchive()
    w.track_calls = []
    for name in ("_add_track_point_at_marker", "_delete_selected_track_point",
                 "_undo_track_edit", "_redo_track_edit"):
        setattr(w, name, lambda n=name: w.track_calls.append(n))
    for attr in list(MainWindow._SHORTCUT_BUTTONS.values()) + ["btn_screenshot"]:
        btn = QToolButton(w)
        btn.setCheckable(attr != "btn_screenshot")
        btn.setToolTip(attr)
        setattr(w, attr, btn)
    w._init_shortcuts()
    return w


def _press(w, action, index=0):
    w._shortcuts.bound[action][index].activated.emit()


def test_toolbar_keys_toggle_their_buttons_and_tooltips_show_keys(app):
    w = _host()
    _press(w, "radar")
    assert w.btn_radar.isChecked()
    _press(w, "radar")
    assert not w.btn_radar.isChecked()
    assert w.btn_radar.toolTip() == "btn_radar (R)"
    assert w.btn_sounding.toolTip() == "btn_sounding (K)"


def test_hidden_or_disabled_buttons_ignore_their_key(app):
    w = _host()
    w.btn_surface.hide()
    _press(w, "surface")
    assert not w.btn_surface.isChecked()
    w.btn_hazards.setEnabled(False)
    _press(w, "hazards")
    assert not w.btn_hazards.isChecked()


def test_live_playback_keys_drive_the_radar_loop(app):
    w = _host()
    for action in ("loop", "frame_prev", "frame_next"):
        _press(w, action)
    assert w.radar_controls.calls == ["loop", -1, 1]
    assert "play" not in w._shortcuts.bound and "step_back" not in w._shortcuts.bound


def test_archive_playback_keys_drive_the_time_controller(app):
    w = _host(archive=True)
    _press(w, "play")
    _press(w, "step_back", 1)       # "["
    _press(w, "step_forward", 2)    # "D"
    _press(w, "skip_end")
    assert w._archive_controls.calls == ["toggle_play", "step_back", "step_forward", "skip_end"]
    assert "loop" not in w._shortcuts.bound and "surface" not in w._shortcuts.bound


def test_typing_in_a_text_box_switches_letter_keys_off(app):
    w = _host()
    box = QLineEdit(w)
    w._shortcuts._on_focus_changed(None, box)
    assert not w._shortcuts.bound["radar"][0].isEnabled()
    assert not w._shortcuts.bound["loop"][0].isEnabled()
    assert w._shortcuts.bound["escape"][0].isEnabled()
    assert w._shortcuts.bound["screenshot"][0].isEnabled()


def test_question_mark_opens_the_list_and_esc_closes_it_first(app):
    w = _host()
    w._click_modes.register("measure", lambda _m, _n: None)
    w._click_modes.enter("measure")
    _press(w, "help")
    assert not w._shortcut_overlay.isHidden()
    w._on_escape_pressed()
    assert w._shortcut_overlay.isHidden()
    assert w._click_modes.mode == "measure"     # the mode survives the first Esc
    w._on_escape_pressed()
    assert w._click_modes.mode == "none"


def test_track_keys_exist_only_in_archive(app):
    live = _host()
    assert not {"track", "track_delete", "track_undo"} & set(live._shortcuts.bound)
    w = _host(archive=True)
    _press(w, "track")
    assert w.btn_track.isChecked()
    _press(w, "track_delete", 1)                  # Backspace
    _press(w, "track_point_at_marker")
    _press(w, "track_undo")
    _press(w, "track_redo")
    assert w.track_calls == ["_delete_selected_track_point", "_add_track_point_at_marker",
                             "_undo_track_edit", "_redo_track_edit"]
