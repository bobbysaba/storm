"""TRACK editing, run on MainWindow's own track methods with the map, clock and
radar stubbed out (MainWindow itself needs a live map and network)."""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtWidgets import QApplication, QToolButton, QWidget

from core.storm_track import TrackPoint, read_track_csv, write_track_csv
from ui.app.main_window import MainWindow
from ui.app.main_window_track import MainWindowTrackMixin
from ui.click_mode import ClickModeController
from ui.session_state import ARCHIVE
from ui.widgets.map_context_menu import find

T0 = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)


class FakeMap:
    def __init__(self):
        self.geojson = None
        self.marker = "unset"
        self.edit_mode = False
        self.layers = None

    def set_track_geojson(self, geojson):
        self.geojson = geojson

    def set_track_edit_mode(self, active):
        self.edit_mode = active

    def set_track_layers_visible(self, line, points):
        self.layers = (line, points)

    def set_track_marker(self, marker):
        self.marker = None if marker is None else dict(marker)

    def __getattr__(self, name):          # the signals _init_track_toolbar connects
        return type("Sig", (), {"connect": lambda self, slot: None})()


class FakeToolbar:
    def __init__(self, parent):
        self.parent = parent

    def add_button(self, label, tooltip, icon_key, overflow=False):
        btn = QToolButton(self.parent)
        btn.setCheckable(True)
        return btn


class Clock:
    def __init__(self):
        self.current_time = T0


class Label:
    text = ""

    def setText(self, text):
        self.text = text


class Scan:
    tilt_deg = 0.5


class Radar:
    station = "KTLX"


class RadarControls:
    def current_product(self):
        return "reflectivity"


class Host(QWidget):          # a widget: the table dialog's parent
    pass


for _name, _v in vars(MainWindowTrackMixin).items():
    if callable(_v):
        setattr(Host, _name, _v)
Host._session_mode = lambda self: ARCHIVE


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tracks(tmp_path, monkeypatch):
    """storm_tracks/ in a temporary folder: every edit autosaves there."""
    monkeypatch.setattr("core.workspace.TRACKS_ROOT", tmp_path / "storm_tracks")
    return tmp_path / "storm_tracks"


@pytest.fixture
def w(app, tracks):
    w = Host()
    w._archive_time = T0
    w._map_container = QWidget()
    w.map_widget = FakeMap()
    w._time_ctrl = Clock()
    w.status_msg_label = Label()
    w.radar_controls = RadarControls()
    w._archive_radar = Radar()
    w._current_radar_scan = Scan()
    w._click_modes = ClickModeController(w)
    w._click_modes.register("track", w._exit_track_mode)
    w._click_modes.register("measure", lambda _m, _n: None)
    w._init_track_toolbar(FakeToolbar(w._map_container))
    w.btn_track.setChecked(True)
    return w


def _later(w, minutes):
    w._time_ctrl.current_time = T0 + timedelta(minutes=minutes)


def _points(w):
    return [f for f in w.map_widget.geojson["features"] if f["geometry"]["type"] == "Point"]


def test_opening_track_enters_its_click_mode_and_draws_the_layers(w):
    assert w._click_modes.is_active("track") and w.map_widget.edit_mode
    assert w._click_modes.prompt.startswith("storm track")
    assert w.map_widget.layers == (True, True)
    w._click_modes.enter("measure")                 # another tool takes the map
    assert not w.btn_track.isChecked() and not w.map_widget.edit_mode
    assert w.map_widget.layers == (False, False)


def test_keep_on_map_draws_the_track_with_track_closed(w):
    w.track_controls.chk_keep.setChecked(True)
    w.track_controls.chk_points.setChecked(False)
    w.btn_track.setChecked(False)
    assert w.map_widget.layers == (True, False)


def test_placing_points_records_the_radar_and_redraws(w):
    w._on_track_point_add(35.0, -97.0)
    _later(w, 2)
    w._on_track_point_add(35.1, -97.1)
    assert [p.lat for p in w._track.points] == [35.0, 35.1]
    p = w._track.points[0]
    assert (p.radar_site, p.product, p.product_label, p.tilt_deg) == (
        "KTLX", "reflectivity", "Reflectivity (REF)", 0.5)
    assert len(_points(w)) == 2
    assert w.track_controls._count_label.text() == "2 points"


def test_a_point_can_be_placed_before_any_radar_is_chosen(w):
    w._archive_radar, w._current_radar_scan = None, None
    w._on_track_point_add(35.0, -97.0)
    assert w._track.points[0].radar_site == "" and w._track.points[0].tilt_deg is None


def test_clicking_at_a_frame_that_has_a_point_moves_that_point(w):
    w._on_track_point_add(35.0, -97.0)
    w._on_track_point_add(35.2, -97.2)
    assert len(w._track) == 1 and w._track.points[0].lat == 35.2
    assert "moved point 1" in w.status_msg_label.text


def test_dragging_a_point_retimes_it_unless_alt_is_held(w):
    w._on_track_point_add(35.0, -97.0)
    _later(w, 5)
    w._on_track_point_moved(1, 35.5, -97.5, True)
    assert (w._track.points[0].lat, w._track.points[0].time) == (35.5, T0)
    w._on_track_point_moved(1, 35.6, -97.6, False)
    assert w._track.points[0].time == T0 + timedelta(minutes=5)


def test_delete_undo_and_redo(w):
    w._on_track_point_add(35.0, -97.0)
    _later(w, 2)
    w._on_track_point_add(35.1, -97.1)
    w._on_track_point_select(1)
    assert [p["properties"]["state"] for p in _points(w)] == ["selected", "current"]
    w._delete_selected_track_point()
    assert [p.point_id for p in w._track.points] == [2]
    w._undo_track_edit()
    assert [p.point_id for p in w._track.points] == [1, 2]
    assert w.track_controls._btn_redo.isEnabled()
    w._redo_track_edit()
    assert [p.point_id for p in w._track.points] == [2] and len(_points(w)) == 1


def test_nothing_is_edited_while_track_is_closed(w):
    w._on_track_point_add(35.0, -97.0)
    w.btn_track.setChecked(False)
    w._on_track_point_add(35.5, -97.5)
    w._on_track_point_moved(1, 35.5, -97.5, False)
    w._track.selected_id = 1
    w._delete_selected_track_point()
    w._undo_track_edit()
    assert [(p.lat, p.lon) for p in w._track.points] == [(35.0, -97.0)]


def test_clear_can_be_undone(w):
    w._on_track_point_add(35.0, -97.0)
    w._clear_track()
    assert len(w._track) == 0 and "Ctrl+Z" in w.status_msg_label.text
    w._undo_track_edit()
    assert len(w._track) == 1


def test_a_single_session_marker_and_adding_a_point_at_it(w):
    w._add_track_point_at_marker()
    assert len(w._track) == 0 and "no marker" in w.status_msg_label.text
    w._on_track_marker_add(35.3, -97.3)
    w._on_track_marker_add(35.4, -97.4)               # replaces: only ever one
    assert w.map_widget.marker == {"lat": 35.4, "lon": -97.4, "label": "M1"}
    w._add_track_point_at_marker()
    assert (w._track.points[0].lat, w._track.points[0].lon) == (35.4, -97.4)
    w._on_track_marker_remove()
    assert w._track_marker is None and w.map_widget.marker is None


def test_renaming_the_marker_keeps_its_label_when_moved(w, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("meso A", True))
    w._on_track_marker_add(35.3, -97.3)
    w._on_track_marker_rename()
    w._on_track_marker_add(35.5, -97.5)
    assert w.map_widget.marker["label"] == "meso A"


def test_the_right_click_menu_offers_the_marker_and_the_point(w):
    items = w._track_context_menu(35.0, -97.0, "")
    assert find(items, "track_marker_add").label == "Add marker"
    assert find(items, "track_marker_remove") is None
    w._on_track_point_add(35.0, -97.0)
    w._on_track_marker_add(35.3, -97.3)
    items = w._track_context_menu(35.0, -97.0, '{"kind": "track_point", "id": 1}')
    assert items[0].label == "Track point 1"
    assert find(items, "track_marker_add").label == "Move marker here"
    assert find(items, "track_marker_rename") and find(items, "track_marker_remove")
    w._ctx_track_point_delete(*find(items, "track_point_delete").args)
    assert len(w._track) == 0


def test_the_panel_shows_mean_and_current_storm_motion(w):
    w._on_track_point_add(35.0, -97.0)
    assert "2 points needed" in w.track_controls._motion_mean.text()
    _later(w, 10)
    w._on_track_point_add(35.0, -96.9)
    assert w.track_controls._motion_mean.text().startswith("Mean 270°")
    assert w.track_storm_motion().direction_from_deg == pytest.approx(270.0)
    _later(w, 20)
    w._on_track_time_changed(w._time_ctrl.current_time)
    assert "outside the track" in w.track_controls._motion_now.text()


def test_the_current_point_follows_the_clock(w):
    w._on_track_point_add(35.0, -97.0)
    _later(w, 10)
    w._on_track_point_add(35.0, -96.9)
    w._time_ctrl.current_time = T0 + timedelta(minutes=1)
    w._on_track_time_changed(w._time_ctrl.current_time)
    assert [p["properties"]["state"] for p in _points(w)] == ["current", "normal"]


def test_track_mixin_methods_are_on_main_window():
    for name, value in vars(MainWindowTrackMixin).items():
        if callable(value):
            assert getattr(MainWindow, name) is value


# ── files, table, sounding motion (session 9) ─────────────

def test_every_edit_saves_the_case_dates_csv(w, tracks):
    w._on_track_point_add(35.0, -97.0)
    path = tracks / "storm_20240427_track.csv"
    assert [p.lat for p in read_track_csv(path)] == [35.0]
    assert "Track saved to" in w.status_msg_label.text
    assert w.track_controls._file_label.text() == "Saved to storm_tracks/storm_20240427_track.csv"
    _later(w, 4)
    w._on_track_point_add(35.1, -97.1)
    w._undo_track_edit()                            # undo saves too
    assert len(read_track_csv(path)) == 1


def test_the_session_opens_the_dates_saved_track(w, tracks):
    write_track_csv([TrackPoint(1, T0, 35.0, -97.0), TrackPoint(2, T0 + timedelta(minutes=10), 35.1, -96.9)],
                    tracks / "storm_20240427_track.csv")
    w._open_session_track()
    assert len(w._track) == 2 and not w._track.can_undo()
    assert w.track_storm_motion() is not None
    assert "Storm track loaded (2 points" in w.status_msg_label.text


def test_an_unreadable_file_is_kept_and_edits_save_beside_it(w, tracks):
    tracks.mkdir()
    bad = tracks / "storm_20240427_track.csv"
    bad.write_text("nothing,useful\n1,2\n")
    w._open_session_track()
    w._on_track_point_add(35.0, -97.0)
    assert bad.read_text() == "nothing,useful\n1,2\n"
    assert len(read_track_csv(tracks / "storm_20240427_track_2.csv")) == 1


def test_import_replaces_the_track_as_one_undoable_edit(w, tmp_path):
    w._on_track_point_add(35.0, -97.0)
    other = tmp_path / "mesoview.csv"
    other.write_text("point_id,time,lat,lon,case_id\n1,2024-04-27 20:30:00,36,-96,T1\n"
                     "2,2024-04-27 20:40:00,36.1,-95.9,T1\n")
    w._import_track_path(other)
    assert [p.lat for p in w._track.points] == [36.0, 36.1]
    w._undo_track_edit()
    assert [p.lat for p in w._track.points] == [35.0]
    w._import_track_path(tmp_path / "missing.csv")
    assert "import failed" in w.status_msg_label.text and len(w._track) == 1


def test_the_table_applies_as_one_edit_and_follows_the_map(w):
    w._on_track_point_add(35.0, -97.0)
    w._open_track_table()
    table = w._track_table
    assert table.rows() == [(1, "2024-04-27 20:00:00", "35.00000", "-97.00000")]
    table._table.item(0, 2).setText("35.5")
    assert table.apply()
    assert w._track.points[0].lat == 35.5 and w._track.points[0].source == "table_edited"
    w._undo_track_edit()
    assert table.rows()[0][2] == "35.00000"          # the table follows undo on the map
    w._on_track_point_select(1)
    assert table._selected_point_id() == 1
    w.btn_track.setChecked(False)                    # closing TRACK hides the table
    assert not table.isVisible()


def test_double_clicking_a_row_moves_the_clock_to_the_point(w):
    moves = []
    w._time_ctrl.pause = lambda: moves.append("pause")
    w._time_ctrl.set_time = moves.append
    w._on_track_point_add(35.0, -97.0)
    _later(w, 5)
    w._jump_to_track_point(1)
    assert moves == ["pause", T0] and w._track.selected_id == 1


def test_the_open_sounding_viewer_gets_the_tracks_motion(w):
    motions = []
    w._sounding_dialog_obj = type("Dlg", (), {"set_observed_storm_motion": lambda _s, m: motions.append(m)})()
    w._on_track_point_add(35.0, -97.0)
    _later(w, 10)
    w._on_track_point_add(35.1, -96.9)
    assert motions[0] is None and motions[-1] == w.track_storm_motion() is not None
