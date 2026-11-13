"""C22 / C23: the archive window's ASOS replay and damage paths
(ui/app/main_window_archive_surface.py): the shared map box and its two
click modes, the replayed station plots, the damage-path layer, and the
controls and map pieces they use."""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow

from archive.fetchers.asos_archive_fetcher import parse_asos_history_csv
from ui import palette as P
from ui.app.main_window_archive_surface import (
    ASOS_BOX_PROMPT, DAMAGE_BOX_PROMPT, MainWindowArchiveSurfaceMixin,
)
from ui.click_mode import ClickModeController
from ui.controls.hazard_controls import HazardControls
from ui.controls.surface_controls import SurfaceControls
from ui.map.widget import MapWidget

T0 = datetime(2013, 5, 31, 0, 0, tzinfo=timezone.utc)
_ROSTER = {
    "OUN": {"lat": 35.24, "lon": -97.47, "name": "Norman"},
    "OKC": {"lat": 35.39, "lon": -97.60, "name": "Oklahoma City"},
}
_CSV = (
    "station,valid,tmpf,dwpf,relh,drct,sknt,alti,mslp\n"
    "OUN,2013-05-31 20:15,68.00,50.00,M,170.00,10.00,29.67,M\n"
    "OUN,2013-05-31 20:35,70.00,M,M,160.00,M,29.68,M\n"
    "OKC,2013-05-31 20:52,75.00,65.00,M,170.00,13.00,M,1003.10\n"
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Map(QObject):
    asos_bbox_selected = pyqtSignal(float, float, float, float)
    asos_box_cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.box_mode = False
        self.damage = "unset"
        self.plots: dict[str, tuple] = {}
        self.adds = 0
        self.plots_visible = True

    def set_asos_bbox_mode(self, active):
        self.box_mode = active

    def run_js(self, script):
        pass

    def set_damage_paths(self, fc):
        self.damage = fc

    def add_surface_station_plots_batch(self, items):
        self.adds += 1
        for sid, lat, lon, png, name in items:
            self.plots[sid] = (lat, lon, name)

    def remove_surface_station_plots_batch(self, ids):
        for sid in ids:
            self.plots.pop(sid, None)

    def set_surface_station_plots_visible(self, visible):
        self.plots_visible = visible


class _Clock(QObject):
    time_changed = pyqtSignal(object)

    def __init__(self, when):
        super().__init__()
        self.current_time = when

    def set_time(self, when):
        self.current_time = when
        self.time_changed.emit(when)


class _Status:
    def __init__(self):
        self.successes = 0
        self.errors = []

    def mark_success(self, valid_time=None):
        self.successes += 1

    def mark_error(self, msg):
        self.errors.append(msg)


class Host(QMainWindow, MainWindowArchiveSurfaceMixin):
    def __init__(self):
        super().__init__()
        self._archive_time = T0
        self.map_widget = _Map()
        self._time_ctrl = _Clock(T0 + timedelta(hours=20))
        self._click_modes = ClickModeController(self)
        self.status_msg_label = QLabel()
        self.surface_controls = SurfaceControls(self)
        self.surface_controls.configure_for_archive()
        self.hazard_controls = HazardControls(self)
        self.btn_damage_paths = self.hazard_controls.add_damage_paths_button()
        self._layer_status = {"surface": _Status()}
        self.active_layers = {}
        self._init_archive_surface()
        self.asos_boxes, self.damage_boxes = [], []
        self._archive_asos.set_bbox = lambda *box: self._fake_asos_box(box)
        self._archive_damage_paths.set_bbox = lambda *box: self.damage_boxes.append(box)

    def _fake_asos_box(self, box):
        self.asos_boxes.append(box)
        self._archive_asos.clear()
        self._archive_asos._loading = True

    def _set_layer_active(self, key, active):
        self.active_layers[key] = active

    def load_asos(self):
        """The fetcher's results arriving for the box drawn."""
        fetcher = self._archive_asos
        fetcher._on_results(fetcher._generation, parse_asos_history_csv(_CSV, _ROSTER),
                            {"OUN": "Norman", "OKC": "Oklahoma City"})


def _settle(app, host, timeout=3.0):
    """Let the plot renders finish and land on the map."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if not host._archive_asos_render_busy and not host._archive_asos_flush_timer.isActive():
            app.processEvents()
            if not host._archive_asos_render_busy:
                return
        time.sleep(0.01)


# ── ASOS ──────────────────────────────────────────────────

def test_asos_on_asks_for_a_box(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    assert host._click_modes.mode == "asos_bbox" and host._click_modes.prompt == ASOS_BOX_PROMPT
    assert host.map_widget.box_mode and host.active_layers["asos"]


def test_the_box_goes_to_asos_and_keeps_it_on(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    assert host.asos_boxes == [(-98.0, 35.0, -97.0, 36.0)] and host.damage_boxes == []
    assert not host._click_modes.is_active() and not host.map_widget.box_mode
    assert host.surface_controls.asos_enabled()
    assert "finding stations" in host.surface_controls._status.text()


def test_backing_out_of_the_box_turns_asos_off(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_box_cancelled.emit()
    assert not host.surface_controls.asos_enabled() and not host._click_modes.is_active()
    assert host.status_msg_label.text() == "ASOS: box canceled"


def test_a_new_box_backed_out_of_keeps_the_stations(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host.load_asos()
    host.surface_controls.asos_bbox_requested.emit()          # "new ASOS bbox"
    assert host._click_modes.mode == "asos_bbox"
    host.map_widget.asos_box_cancelled.emit()
    assert host.surface_controls.asos_enabled() and host._archive_asos.has_stations()


def test_replay_draws_plots_at_the_clock_and_rewinds(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host._time_ctrl.set_time(T0 + timedelta(hours=21))
    host.load_asos()
    _settle(app, host)
    assert set(host.map_widget.plots) == {"surface:asos:OUN", "surface:asos:OKC"}
    assert host.map_widget.plots["surface:asos:OUN"][2] == "Norman"
    assert host._layer_status["surface"].successes == 1
    assert host.surface_controls._status.text() == "ASOS: 2 stations"

    host._time_ctrl.set_time(T0 + timedelta(hours=20, minutes=20))   # before OKC's first report
    _settle(app, host)
    assert set(host.map_widget.plots) == {"surface:asos:OUN"}


def test_plots_age_against_the_clock_not_now(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host._time_ctrl.set_time(T0 + timedelta(hours=21))
    host.load_asos()
    _settle(app, host)
    assert host._archive_asos_drawn["surface:asos:OKC"][3] == P.OK        # 8 min old at 21:00
    adds = host.map_widget.adds
    host._time_ctrl.set_time(T0 + timedelta(hours=22, minutes=45))      # no newer report: 113 min old
    _settle(app, host)
    assert host._archive_asos_drawn["surface:asos:OKC"][3] == P.ERROR
    assert host.map_widget.adds > adds                                   # redrawn in its new color


def test_asos_off_clears_the_plots(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host._time_ctrl.set_time(T0 + timedelta(hours=21))
    host.load_asos()
    _settle(app, host)
    host.surface_controls.set_asos_enabled(False)
    _settle(app, host)
    assert host.map_widget.plots == {} and not host._archive_asos.has_stations()
    assert not host.active_layers["asos"]


def test_an_empty_box_turns_asos_off_and_says_so(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(0.0, 0.0, 0.1, 0.1)
    host._archive_asos._on_results(host._archive_asos._generation, {}, {})
    assert not host.surface_controls.asos_enabled()
    assert host.surface_controls._status.text() == "ASOS: no reports in this box"


def test_a_load_error_turns_asos_off_and_marks_the_layer(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host._archive_asos.error.emit("ASOS history unavailable: HTTP 503")
    assert not host.surface_controls.asos_enabled()
    assert host._layer_status["surface"].errors == ["ASOS history unavailable: HTTP 503"]
    assert host.status_msg_label.text() == "ASOS history unavailable: HTTP 503"


def test_show_plots_reaches_the_map(app):
    host = Host()
    host.surface_controls._chk_show_plots.setChecked(False)
    assert host.map_widget.plots_visible is False


# ── damage paths ──────────────────────────────────────────

def _path(efscale="EF3"):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-98, 35], [-97.9, 35.1]]},
            "properties": {"ef_label": efscale, "damage_source": "NOAA DAT"}}


def test_damage_paths_get_their_own_box(app):
    host = Host()
    host.btn_damage_paths.setChecked(True)
    assert host._click_modes.mode == "damage_box" and host._click_modes.prompt == DAMAGE_BOX_PROMPT
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    assert host.damage_boxes == [(-98.0, 35.0, -97.0, 36.0)] and host.asos_boxes == []
    assert host.btn_damage_paths.isChecked() and host._damage_paths_state == "loading"
    assert host.status_msg_label.text() == "Damage paths: searching…"


def test_damage_paths_drawn_when_found(app):
    host = Host()
    host.btn_damage_paths.setChecked(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    fc = {"type": "FeatureCollection", "features": [_path(), _path("EF1")]}
    host._archive_damage_paths.paths_ready.emit(fc)
    assert host.map_widget.damage == fc and host._damage_paths_state == "shown"
    assert host.status_msg_label.text() == "Damage paths: 2 paths (NOAA DAT)"
    assert host.active_layers["damage_paths"]

    host.btn_damage_paths.setChecked(False)
    assert host.map_widget.damage is None and host._damage_paths_state == "none"


def test_no_damage_paths_turns_the_button_off(app):
    host = Host()
    host.btn_damage_paths.setChecked(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host._archive_damage_paths.paths_ready.emit({"type": "FeatureCollection", "features": []})
    assert not host.btn_damage_paths.isChecked()
    assert host.status_msg_label.text() == "Damage paths: none in this box on 2013-05-31"


def test_damage_path_error_turns_the_button_off(app):
    host = Host()
    host.btn_damage_paths.setChecked(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host._archive_damage_paths.error.emit("Damage paths unavailable: offline")
    assert not host.btn_damage_paths.isChecked()
    assert host.status_msg_label.text() == "Damage paths unavailable: offline"


def test_late_damage_paths_after_turning_off_are_ignored(app):
    host = Host()
    host.btn_damage_paths.setChecked(True)
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    host.btn_damage_paths.setChecked(False)
    host._archive_damage_paths.paths_ready.emit({"type": "FeatureCollection", "features": [_path()]})
    assert host.map_widget.damage is None and not host.btn_damage_paths.isChecked()


def test_backing_out_of_the_damage_box_turns_it_off(app):
    host = Host()
    host.btn_damage_paths.setChecked(True)
    host.map_widget.asos_box_cancelled.emit()
    assert not host.btn_damage_paths.isChecked()
    assert host.status_msg_label.text() == "Damage paths: box canceled"


def test_switching_boxes_turns_off_the_one_left_waiting(app):
    host = Host()
    host.surface_controls.set_asos_enabled(True)
    host.btn_damage_paths.setChecked(True)
    assert not host.surface_controls.asos_enabled()
    assert host._click_modes.mode == "damage_box" and host.map_widget.box_mode
    host.map_widget.asos_bbox_selected.emit(-98.0, 35.0, -97.0, 36.0)
    assert host.damage_boxes and not host.asos_boxes


# ── controls and map ──────────────────────────────────────

def test_archive_surface_drawer_is_asos_only(app):
    controls = SurfaceControls()
    controls.configure_for_archive()
    assert all(btn.isHidden() for btn in controls._source_buttons().values())
    assert not controls._btn_asos.isHidden() and controls._btn_asos.toolTip()
    assert "Report age at the clock" in controls._legend.text() and not controls._legend.isHidden()
    assert "Mesonet" not in controls._legend.text()


def test_damage_paths_button_follows_the_nws_buttons(app):
    controls = HazardControls()
    btn = controls.add_damage_paths_button()
    row = controls._btn_cwa.parentWidget().layout()
    assert row.indexOf(btn) == row.indexOf(controls._btn_cwa) + 2      # after a divider
    assert btn.text() == "DAMAGE PATHS" and btn.isCheckable() and btn.toolTip()


def test_set_damage_paths_sends_the_collection():
    class _Fake:
        def __init__(self):
            self.scripts = []

        def run_js(self, script):
            self.scripts.append(script)

    widget = _Fake()
    MapWidget.set_damage_paths(widget, {"type": "FeatureCollection", "features": [_path()]})
    MapWidget.set_damage_paths(widget, None)
    assert all("stormSetDamagePaths" in s for s in widget.scripts)
    assert "EF3" in widget.scripts[0]
    assert json.dumps(json.dumps({"type": "FeatureCollection", "features": []})) in widget.scripts[1]


def test_map_template_has_damage_layers_and_hover():
    html = (Path(__file__).resolve().parents[1] / "ui" / "map" / "map_template.html").read_text()
    for layer in ("damage-paths-hit", "damage-paths-casing", "damage-paths-line",
                  "damage-paths-start", "damage-paths-end"):
        assert f"id: '{layer}'" in html
    assert "window.stormSetDamagePaths = function" in html
    assert 'id="damage-path-tooltip"' in html and "_damagePathHover(e)" in html
    assert "window._damagePathsData" in html          # redrawn after a basemap switch


def test_layer_order_knows_damage_paths():
    from ui.widgets.layer_order_pill import DEFAULT_LAYERS, LAYER_LABELS, MAPLIBRE_LAYERS
    assert "damage_paths" in DEFAULT_LAYERS and LAYER_LABELS["damage_paths"] == "Damage Paths"
    assert "damage-paths-hit" in MAPLIBRE_LAYERS["damage_paths"]
