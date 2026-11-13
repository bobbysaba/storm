"""The command palette's providers: mode filtering and toolbar parity, with fake controls."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

import config
from core.vehicle import Vehicle
from ui import shortcuts
from ui.app.main_window import MainWindow
from ui.app.main_window_palette import MainWindowPaletteMixin
from ui.controls.hazard_controls import HazardControls
from ui.controls.satellite_controls import SatelliteControls


class FakeToolbar:
    def __init__(self):
        self.over = set()

    def overflowed(self):
        return set(self.over)


class FakeDrawers:
    def __init__(self, entries):
        self._entries = entries
        self.mode = "auto"

    def keys(self):
        return list(self._entries)

    def entry(self, key):
        return self._entries[key]


class Entry:
    def __init__(self, button, child=False):
        self.button = button
        self.child = child


class FakeRadarControls:
    def __init__(self):
        self.selected = []

    def current_site(self):
        return "KTLX"

    def current_product(self):
        return "N0B"

    def products(self):
        return [("N0B", "Reflectivity"), ("N0G", "Velocity")]

    def select_product(self, code):
        self.selected.append(code)


class Host(QObject):
    pass


for _name, _v in vars(MainWindowPaletteMixin).items():
    if callable(_v):
        setattr(Host, _name, _v)
for _name in ("_SHORTCUT_BUTTONS", "_ctx_radar_site", "_vehicle_followable",
              "_ctx_vehicle_center", "_ctx_vehicle_follow"):
    setattr(Host, _name, getattr(MainWindow, _name))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _button(parent, checkable=True):
    b = QToolButton(parent)
    b.setCheckable(checkable)
    return b


def _make(app, mode=shortcuts.LIVE, archive=False):
    w = Host()
    w._root = QWidget()
    w._archive = archive
    w._floating_toolbar = FakeToolbar()
    w._shortcuts = shortcuts.ShortcutRegistry(w._root, mode)
    for attr in ("btn_radar", "btn_satellite", "btn_hazards", "btn_surface", "btn_vehicles",
                 "btn_sounding", "btn_measure", "btn_annotate", "btn_prev_locs", "btn_map",
                 "btn_track"):
        setattr(w, attr, _button(w._root))
    w.btn_route = _button(w._root)
    w._drawers = FakeDrawers({
        "radar": Entry(w.btn_radar), "satellite": Entry(w.btn_satellite),
        "surface": Entry(w.btn_surface), "prev_locs": Entry(w.btn_prev_locs),
        "map": Entry(w.btn_map), "route": Entry(w.btn_route, child=True),
    })
    for action, attr in MainWindow._SHORTCUT_BUTTONS.items():
        w._shortcuts.bind(action, getattr(w, attr).click)
    for action in ("loop", "play", "screenshot", "help", "escape"):
        w._shortcuts.bind(action, lambda: None)
    w._on_reset_layout = lambda: None
    w.radar_controls = FakeRadarControls()
    w.satellite_controls = SatelliteControls()
    w.hazard_controls = HazardControls()
    w._radar_station_sites = [
        {"site_id": "KAMA", "name": "Amarillo", "lat": 35.23, "lon": -101.71},
        {"site_id": "KTLX", "name": "Oklahoma City", "lat": 35.33, "lon": -97.28},
    ]
    w._vehicles = {}
    w.picked_sites = []
    w._ctx_radar_site = w.picked_sites.append
    return w


def _ids(commands):
    return [c.id for c in commands]


def test_actions_follow_the_shortcut_table_for_the_mode(app):
    live = _ids(_make(app)._palette_actions())
    assert "action:loop" in live and "action:play" not in live
    archive = _ids(_make(app, shortcuts.ARCHIVE, archive=True)._palette_actions())
    assert "action:play" in archive and "action:loop" not in archive
    # drawer toggles are listed once, under Drawers; Esc and the palette never
    assert "action:radar" not in live and "action:escape" not in live
    assert {"action:reset_layout", "action:text_130"} <= set(live)
    assert not any(i.startswith("action:drawers_") for i in live)   # always one at a time


def test_action_keys_are_shown(app):
    measure = next(c for c in _make(app)._palette_actions() if c.id == "action:measure")
    assert measure.keys == "M"


def test_a_button_left_out_of_this_mode_is_left_out(app):
    w = _make(app)
    w.btn_surface.hide()               # e.g. ARCHIVE hides SURFACE
    w.btn_prev_locs.hide()
    w._floating_toolbar.over = {w.btn_prev_locs}   # hidden only because it sits in ⋯
    drawers = _ids(w._palette_drawers())
    assert "drawer:surface" not in drawers
    assert "drawer:prev_locs" in drawers


def test_child_drawer_follows_its_own_button(app):
    w = _make(app)
    assert "drawer:route" in _ids(w._palette_drawers())
    w.btn_route.hide()                 # VIEWER hides ROUTE inside the MAP drawer
    assert "drawer:route" not in _ids(w._palette_drawers())


def test_drawer_command_toggles_through_its_button(app):
    w = _make(app)
    cmd = next(c for c in w._palette_drawers() if c.id == "drawer:radar")
    assert cmd.title == "Open Radar drawer" and cmd.keys == "R"
    cmd.run()
    assert w.btn_radar.isChecked()
    cmd = next(c for c in w._palette_drawers() if c.id == "drawer:radar")
    assert cmd.title == "Close Radar drawer"


def test_radar_sites_nearest_first_with_a_gps_fix(app):
    w = _make(app)
    assert _ids(w._palette_radar())[:2] == ["site:KAMA", "site:KTLX"]
    w._vehicles[config.VEHICLE_ID] = Vehicle(id=config.VEHICLE_ID, lat=35.2, lon=-97.4)
    cmds = list(w._palette_radar())
    assert _ids(cmds)[:2] == ["site:KTLX", "site:KAMA"]
    assert cmds[0].title == "KTLX Oklahoma City" and cmds[0].hint.startswith("current")
    cmds[1].run()
    assert w.picked_sites == ["KAMA"]


def test_radar_products_run_through_the_product_box(app):
    w = _make(app)
    vel = next(c for c in w._palette_radar() if c.id == "product:N0G")
    vel.run()
    assert w.radar_controls.selected == ["N0G"]


def test_satellite_lists_only_available_sectors(app):
    w = _make(app)
    assert _ids(w._palette_satellite()) == ["satellite:conus"]
    w.satellite_controls.set_meso_available(2, True, {"north": 1, "south": 0, "east": 1, "west": 0})
    cmds = {c.id: c for c in w._palette_satellite()}
    assert set(cmds) == {"satellite:conus", "satellite:meso2"}
    cmds["satellite:meso2"].run()
    assert w.satellite_controls.current_mode() == "meso2"


def test_spc_products_follow_the_day(app):
    w = _make(app)
    day1 = _ids(w._palette_spc())
    assert "spc_product:tor" in day1 and "spc_product:prob" not in day1
    next(c for c in w._palette_spc() if c.id == "spc_day:3").run()
    day3 = _ids(w._palette_spc())
    assert "spc_product:prob" in day3 and "spc_product:tor" not in day3
    warn = next(c for c in w._palette_spc() if c.id == "hazard:nws_warnings")
    assert warn.title == "Show NWS warnings"
    warn.run()
    assert next(c for c in w._palette_spc() if c.id == "hazard:nws_warnings").title == "Hide NWS warnings"


def test_vehicles_center_and_follow_rules(app):
    w = _make(app)
    w._vehicles = {
        config.VEHICLE_ID: Vehicle(id=config.VEHICLE_ID, lat=35.0, lon=-97.0),
        "OTHER": Vehicle(id="OTHER", lat=36.0, lon=-98.0),
    }
    ids = _ids(w._palette_vehicles())
    assert f"vehicle_follow:{config.VEHICLE_ID}" in ids
    assert "vehicle_center:OTHER" in ids and "vehicle_follow:OTHER" not in ids


def test_nearest_deploy_locations_only_with_the_button(app):
    w = _make(app)
    w._vehicles[config.VEHICLE_ID] = Vehicle(id=config.VEHICLE_ID, lat=35.0, lon=-97.0)
    w._deploy_points = [
        {"lat": 35.0 + i * 0.1, "lon": -97.0, "rank_abi": i + 1} for i in range(8)
    ]
    cmds = list(w._palette_deploy_locs())
    assert len(cmds) == 5 and cmds[0].title.endswith("rank 1")
    w.btn_prev_locs.hide()             # not offered in ARCHIVE
    assert list(w._palette_deploy_locs()) == []
