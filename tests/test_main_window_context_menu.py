"""The map right-click menu's actions, run through MainWindow's real handlers with a fake map."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from ui.app import main_window_context_menu
from ui.app.main_window import MainWindow
from ui.app.main_window_context_menu import MainWindowContextMenuMixin
from ui.widgets.annotation_tools import AnnotationTools

_METHODS = (
    "_init_click_modes", "_on_click_mode_prompt", "_show_mode_banner",
    "_set_placement_prompt", "_clear_placement_prompt", "_on_map_click",
    "_on_measure_toggled", "_on_measure_click",
    "_on_annotation_tool_selected", "_exit_annotation_mode", "_exit_drawing_mode",
    "_cancel_drawing", "_on_drawing_click", "_on_storm_motion_fix_click",
    "_enter_route_pick", "_on_map_pick_for_route", "_exit_route_pick",
    "_on_private_pin_route_requested", "_session_mode",
    "_start_annotation_move", "_enter_asos_bbox", "_exit_asos_bbox", "_exit_track_mode",
) + tuple(n for n, v in vars(MainWindowContextMenuMixin).items() if callable(v))


class FakeMap:
    def __init__(self):
        self.on: set[str] = set()
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **_kwargs):
            if name.startswith("set_") and name.endswith("_mode") and args:
                (self.on.add if args[0] else self.on.discard)(name[4:-5])
            self.calls.append((name, args))
        return record

    def called(self, name):
        return [args for n, args in self.calls if n == name]


class FakeRouting:
    def __init__(self):
        self.points = []
        self.pins = []

    def set_point(self, which, lat, lon):
        self.points.append((which, lat, lon))

    def route_to_pin(self, lat, lon, label=""):
        self.pins.append((lat, lon))

    def cancel_pick(self):
        pass


class Annotation:
    def __init__(self, type_key):
        self.type_key = type_key


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
    w.status_msg_label = QLabel()
    w._archive = w._viewer = w._monitor = False
    w._request_layout = lambda *a: None
    w._layout_overlays = lambda *a: None
    w._active_annotation_type = ""
    w._active_drawing_type = ""
    w._drawing_points = []
    w._pending_cone_params = None
    w._pending_cone_motion_fix = None
    w._measure_has_anchor = False
    w._measure_complete = False
    w._annotations = {"a1": Annotation("debris")}
    w._annotation_sync = object()
    w._vehicles = {}
    w._radar_station_sites = [
        {"site_id": "KTLX", "lat": 35.33, "lon": -97.28},
        {"site_id": "KVNX", "lat": 36.74, "lon": -98.13},
        {"site_id": "KINX", "lat": 36.18, "lon": -95.56},
        {"site_id": "KAMA", "lat": 35.23, "lon": -101.71},
    ]
    w._radar_station_picker_visible = False
    w.placed = []
    w._on_annotation_place_click = lambda lat, lon: w.placed.append(
        (w._active_annotation_type, lat, lon))
    w._init_click_modes()

    w.btn_measure = QToolButton(checkable=True)
    w.btn_measure.toggled.connect(w._on_measure_toggled)
    w.btn_route = QToolButton(checkable=True)
    w.annotation_tools = AnnotationTools()
    w.annotation_tools.tool_selected.connect(w._on_annotation_tool_selected)
    return w


def test_measure_from_here_places_the_first_point(win):
    win._run_context_action("measure_from", (35.0, -97.0))
    assert win._click_modes.mode == "measure" and win.btn_measure.isChecked()
    assert win.map_widget.called("measure_click") == [(35.0, -97.0)]
    assert win._measure_has_anchor


def test_draw_from_here_starts_the_drawing_at_the_point(win):
    win._run_context_action("draw_from", ("polygon", 35.0, -97.0))
    assert win._click_modes.mode == "drawing:polygon"
    assert win._drawing_points == [[35.0, -97.0]]
    assert "drawing" in win.map_widget.on


def test_place_annotation_places_once_and_leaves_the_tool_off(win):
    win._run_context_action("annotation_place", ("road_closure", 35.0, -97.0))
    assert win.placed == [("road_closure", 35.0, -97.0)]
    assert win._click_modes.mode == "none" and win.map_widget.on == set()


def test_route_to_here_uses_gps_in_vehicle_mode(win):
    win._run_context_action("route_to", (35.0, -97.0))
    assert win.routing_controls.pins == [(35.0, -97.0)]
    assert win.btn_route.isChecked()


def test_route_to_here_sets_the_destination_without_gps(win):
    win._monitor = True
    win._run_context_action("route_to", (35.0, -97.0))
    assert win.routing_controls.pins == []
    assert win.routing_controls.points == [("dest", 35.0, -97.0)]
    assert win.btn_route.isChecked()


def test_route_from_here_sets_the_start(win):
    win._run_context_action("route_from", (35.0, -97.0))
    assert win.routing_controls.points == [("origin", 35.0, -97.0)]


def test_radar_site_goes_through_the_station_click(win):
    picked = []
    win._on_radar_station_clicked = picked.append
    win._set_radar_station_picker_visible = lambda v: setattr(
        win, "_radar_station_picker_visible", v)
    win._radar_station_picker_visible = True
    win._run_context_action("radar_site", ("KVNX",))
    assert picked == ["KVNX"] and not win._radar_station_picker_visible


def test_copy_latlon(win):
    win._run_context_action("copy_latlon", (35.123456, -97.5))
    assert QApplication.clipboard().text() == "35.12346, -97.50000"


def test_hazard_text_goes_to_the_feature_click_handler(win):
    got = []
    win._on_spc_feature_clicked = got.append
    features = ({"source": "spc-mds", "properties": {"name": "MD 1234"}},)
    win._run_context_action("hazard_text", (features,))
    assert json.loads(got[0]) == list(features)


def test_private_pin_is_added_in_the_page(win):
    win._run_context_action("private_pin", (35.0, -97.0))
    assert win.map_widget.called("add_private_pin") == [(35.0, -97.0)]


def test_context_names_the_annotation_and_drops_unknown_ones(win):
    ctx = win._context_menu_context(35.0, -97.0, json.dumps({"kind": "annotation", "id": "a1"}))
    assert ctx.feature["type_key"] == "debris" and ctx.annotations
    ctx = win._context_menu_context(35.0, -97.0, json.dumps({"kind": "annotation", "id": "gone"}))
    assert ctx.feature is None


def test_context_lists_nearest_radar_sites(win):
    ctx = win._context_menu_context(35.2, -97.4, "")
    assert [s for s, _ in ctx.radar_sites] == ["KTLX", "KVNX", "KINX"]
    assert ctx.mode == "vehicle" and not ctx.hrrr and ctx.raob is not None


def test_menu_stays_shut_while_a_tool_owns_the_map(win, monkeypatch):
    shown = []
    monkeypatch.setattr(main_window_context_menu.map_context_menu, "show_menu",
                        lambda items, *a: shown.append(items))
    win.btn_measure.setChecked(True)
    win._on_map_context_menu(35.0, -97.0, "")
    assert shown == []
    win.btn_measure.setChecked(False)
    win._on_map_context_menu(35.0, -97.0, "")
    assert len(shown) == 1


def test_track_mode_gets_its_own_menu(win, monkeypatch):
    shown = []
    monkeypatch.setattr(main_window_context_menu.map_context_menu, "show_menu",
                        lambda items, *a: shown.append(items))
    win._track_context_menu = lambda lat, lon, feature_json: [("track menu", lat, lon, feature_json)]
    win.btn_track = QToolButton(checkable=True)
    win._click_modes.enter("track")
    win._on_map_context_menu(35.0, -97.0, '{"kind": "track_point", "id": 3}')
    assert shown == [[("track menu", 35.0, -97.0, '{"kind": "track_point", "id": 3}')]]


def test_show_menu_returns_the_chosen_item(app):
    from PyQt6.QtCore import QPoint, Qt, QTimer
    from PyQt6.QtTest import QTest
    from ui.widgets.map_context_menu import SEPARATOR, Item, show_menu

    items = [Item("Title", "title"), SEPARATOR,
             Item("Sub", children=(Item("Deep", "deep"),)),
             Item("Copy", "copy_latlon", (1.0, 2.0))]
    keys = []

    def choose():
        menu = QApplication.activePopupWidget()
        keys.append([a.text() for a in menu.actions()])
        # the disabled title and the separator are skipped: Sub, then Copy
        QTest.keyClick(menu, Qt.Key.Key_Down)
        QTest.keyClick(menu, Qt.Key.Key_Down)
        QTest.keyClick(menu, Qt.Key.Key_Return)

    QTimer.singleShot(50, choose)
    chosen = show_menu(items, None, QPoint(100, 100))
    assert keys == [["Title", "", "Sub", "Copy"]]
    assert chosen is items[3]


def test_archive_offers_no_hrrr_sounding(win):
    win._archive = True
    win._sounding_fetcher = object()  # live radar init still builds it in archive
    ctx = win._context_menu_context(35.2, -97.4, "")
    assert not ctx.hrrr and ctx.raob is not None
