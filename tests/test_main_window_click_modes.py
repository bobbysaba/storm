"""MainWindow's click-mode wiring, driven through its real handlers with a fake map widget."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

from core.annotation import ANNOTATION_TYPES
from core.drawing import DRAWING_TYPE_MAP
from ui.app.main_window import MainWindow
from ui.widgets.annotation_tools import AnnotationTools

ANNOTATION_KEY = next(t["key"] for t in ANNOTATION_TYPES
                      if t["key"] not in ("fork", "storm_motion", "pressure_system"))
DRAWING_KEY = next(iter(DRAWING_TYPE_MAP))

_METHODS = (
    "_init_click_modes", "_on_click_mode_prompt", "_show_mode_banner",
    "_set_placement_prompt", "_clear_placement_prompt", "_on_map_click", "_on_escape_pressed",
    "_on_measure_toggled", "_on_measure_click",
    "_on_sounding_mode_toggled", "_on_sounding_mode_changed",
    "_enter_route_pick", "_on_map_pick_for_route", "_exit_route_pick",
    "_on_annotation_tool_selected", "_exit_annotation_mode", "_exit_drawing_mode",
    "_cancel_drawing", "_on_drawing_click", "_on_annotation_place_click",
    "_on_storm_motion_fix_click", "_enter_asos_bbox", "_exit_asos_bbox",
)

# MapWidget setter → the click mode it switches on in the map page
_MAP_MODES = {
    "set_measure_mode": "measure",
    "set_sounding_mode": "sounding",
    "set_obs_sounding_mode": "obs_sounding",
    "set_route_pick_mode": "route_pick",
    "set_annotation_mode": "annotation",
    "set_drawing_mode": "drawing",
    "set_asos_bbox_mode": "asos_bbox",
    "set_storm_cone_placement_mode": "cone_place",
}


class FakeMap:
    def __init__(self):
        self.on: set[str] = set()
        self.measure_clicks = []

    def __getattr__(self, name):
        if name in _MAP_MODES:
            def setter(active, *_args):
                (self.on.add if active else self.on.discard)(_MAP_MODES[name])
            return setter
        if name == "measure_click":
            return lambda lat, lon: self.measure_clicks.append((lat, lon))
        return lambda *args, **kwargs: None


class FakeRouting:
    def __init__(self):
        self.picks = []
        self.cancels = 0

    def cancel_pick(self):
        self.cancels += 1

    def on_map_pick(self, lat, lon):
        self.picks.append((lat, lon))


class FakeSoundingControls:
    active_mode = "hrrr"

    def reset_to_hrrr(self):
        pass


class Host(QObject):
    pass


for _name in _METHODS:
    setattr(Host, _name, getattr(MainWindow, _name))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = Host()
    w._map_container = QWidget()
    w.map_widget = FakeMap()
    w.routing_controls = FakeRouting()
    w.sounding_controls = FakeSoundingControls()
    w._sounding_fetcher = object()
    w._archive = False
    w._pill_route_expanded = False
    w._request_layout = lambda *a: None
    w._active_annotation_type = ""
    w._active_drawing_type = ""
    w._drawing_points = []
    w._pending_cone_params = None
    w._pending_cone_motion_fix = None
    w._measure_has_anchor = False
    w._measure_complete = False
    w._init_click_modes()

    w.btn_measure = QToolButton(checkable=True)
    w.btn_measure.toggled.connect(w._on_measure_toggled)
    w.btn_sounding = QToolButton(checkable=True)
    w.btn_sounding.toggled.connect(w._on_sounding_mode_toggled)
    w.annotation_tools = AnnotationTools()
    w.annotation_tools.tool_selected.connect(w._on_annotation_tool_selected)
    return w


def _pick_tool(win, key):
    btn = next(b for b in win.annotation_tools._buttons if b.type_key == key)
    win.annotation_tools._on_button_clicked(btn)


def test_each_mode_turns_the_previous_one_off(win):
    m = win.map_widget
    win.btn_measure.setChecked(True)
    assert m.on == {"measure"}

    win.btn_sounding.setChecked(True)
    assert m.on == {"sounding"} and not win.btn_measure.isChecked()

    _pick_tool(win, ANNOTATION_KEY)
    assert m.on == {"annotation"} and not win.btn_sounding.isChecked()

    _pick_tool(win, DRAWING_KEY)
    assert m.on == {"drawing"}
    # switching tools inside the ANNOTATE drawer keeps the new tool highlighted
    assert win.annotation_tools._active_type == DRAWING_KEY

    win._enter_route_pick()
    assert m.on == {"route_pick"}
    assert win.annotation_tools._active_type == ""

    win._enter_asos_bbox()
    assert m.on == {"asos_bbox"} and win.routing_controls.cancels == 1

    win.btn_measure.setChecked(True)
    assert m.on == {"measure"} and win._click_modes.mode == "measure"


def test_map_click_goes_to_the_active_mode_only(win):
    win._on_map_click(35.0, -97.0)            # no mode: ignored
    win.btn_measure.setChecked(True)
    win._on_map_click(35.0, -97.0)
    assert win.map_widget.measure_clicks == [(35.0, -97.0)]
    win.btn_sounding.setChecked(True)
    win._on_map_click(36.0, -98.0)            # sounding clicks arrive via sounding_clicked
    assert win.map_widget.measure_clicks == [(35.0, -97.0)]


def test_route_pick_ends_after_the_pick(win):
    win._enter_route_pick()
    win._on_map_pick_for_route(35.5, -97.5)
    assert win.routing_controls.picks == [(35.5, -97.5)]
    assert win._click_modes.mode == "none" and "route_pick" not in win.map_widget.on


def test_banner_follows_the_mode_and_esc_exits(win):
    win.btn_measure.setChecked(True)
    assert not win._mode_banner.isHidden()
    assert "measure — click first point" in win._mode_banner.text()
    assert "Esc" in win._mode_banner.text()

    win._on_escape_pressed()
    assert win._click_modes.mode == "none"
    assert not win.btn_measure.isChecked()
    assert win._mode_banner.isHidden()
    assert win.map_widget.on == set()


def test_closing_annotate_drawer_exits_tool_but_keeps_a_storm_motion_fix(win):
    _pick_tool(win, ANNOTATION_KEY)
    win.annotation_tools.toggle_drawer(False)
    assert win._click_modes.mode == "none" and win.map_widget.on == set()

    _pick_tool(win, "storm_motion")
    win._pending_cone_motion_fix = {"lat": 35.0}
    win.annotation_tools.toggle_drawer(False)
    assert win._click_modes.mode == "storm_motion_fix"
