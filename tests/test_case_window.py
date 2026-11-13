"""D28 / D31 in the window: the menu bar and its keys, case settings, opening
a package (track, radar view, clock, data) and the background export."""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox, QToolButton, QWidget

from core import case_package, package_sources, provenance
from core.storm_track import StormTrack, TrackPoint, track_csv_text
from ui.app import main_window_case
from ui.app.main_window import MainWindow
from ui.app.main_window_case import MainWindowCaseMixin
from ui.app.main_window_radar import MainWindowRadarMixin
from ui.app.main_window_studio import MainWindowStudioMixin
from ui.click_mode import ClickModeController

UTC = timezone.utc
T0 = datetime(2024, 4, 27, 20, 0, tzinfo=UTC)
WINDOW = (datetime(2024, 4, 27, 0, tzinfo=UTC), datetime(2024, 4, 28, 3, tzinfo=UTC))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean():
    provenance.reset(active=True)
    package_sources.deactivate()
    yield
    package_sources.deactivate()
    provenance.reset(active=False)


class FakeClock:
    def __init__(self):
        self.window = WINDOW
        self.current_time = T0
        self.paused = False

    def pause(self):
        self.paused = True

    def set_time(self, t):
        self.current_time = t


class FakeRadarControls:
    def __init__(self):
        self.product, self.tilt, self.dealias = "reflectivity", 0, False
        self.calls = []

    def current_product(self):
        return self.product

    def current_tilt_index(self):
        return self.tilt

    def is_dealias(self):
        return self.dealias

    def set_dealias(self, on):
        self.dealias = on

    def select_product(self, code):
        self.calls.append(("product", code))

    def select_tilt(self, idx):
        self.calls.append(("tilt", idx))


class FakeRadar:
    def __init__(self, station):
        self.station = station


class Host(MainWindowCaseMixin, MainWindowRadarMixin, MainWindowStudioMixin, QMainWindow):
    _SHORTCUT_BUTTONS = MainWindow._SHORTCUT_BUTTONS


for _name in ("_init_shortcuts", "_click_if_shown", "_call_archive_controls", "_toggle_shortcut_overlay",
              "_on_escape_pressed", "_loop_key"):
    setattr(Host, _name, MainWindow.__dict__[_name])


def _host(archive=True, track_file=None):
    w = Host()
    w._archive = archive
    w._archive_time = T0
    w._map_container = QWidget(w)
    w._pill_route_expanded = False
    w._request_layout = lambda *a: None
    w._click_modes = ClickModeController(w)
    w.calls = []
    for name in ("_toggle_debug_panel", "_toggle_error_log_panel", "_add_track_point_at_marker",
                 "_delete_selected_track_point", "_undo_track_edit", "_redo_track_edit"):
        setattr(w, name, lambda n=name: w.calls.append(n))
    for attr in list(MainWindow._SHORTCUT_BUTTONS.values()) + ["btn_screenshot"]:
        btn = QToolButton(w)
        btn.setToolTip(attr)
        setattr(w, attr, btn)
    w.status_msg_label = QLabel()
    w.radar_controls = FakeRadarControls()
    w._time_ctrl = FakeClock()
    w._track = StormTrack()
    w._track_changed = lambda: w.calls.append("track_changed")
    w._track_session_day = lambda: T0.date()
    w._track_file = lambda: track_file or Path("/nonexistent/track.csv")
    w._later = lambda _ms, cb: cb()
    w._archive_radar = None
    w._on_radar_station_clicked = lambda s: w.calls.append(("station_clicked", s))
    w._start_archive_radar = lambda s: w.calls.append(("start_radar", s))
    w._archive_session = type("S", (), {"radar_station": None})()
    w._setup_case_sources(archive, None)
    w._init_menu_bar()
    w._init_shortcuts()
    return w


def _menu(w, title):
    return next(a.menu() for a in w.menuBar().actions() if a.text() == title)


def _texts(menu):
    return [a.text() for a in menu.actions() if a.isVisible() and not a.isSeparator()]


# ── menu bar ──────────────────────────────────────────────
def test_archive_menus(app):
    w = _host(archive=True)
    assert [a.text() for a in w.menuBar().actions()] == ["File", "Edit", "View", "Help"]
    assert _texts(_menu(w, "File")) == ["Open Case Package…", "Export Case Package…",
                                        "Show Case Packages Folder", "Video Studio", "Change Day…",
                                        "Save Map Screenshot…"]
    assert _texts(_menu(w, "Edit")) == ["Undo Track Edit", "Redo Track Edit"]
    assert _texts(_menu(w, "View")) == ["Error Log", "Debug Panel"]
    assert _texts(_menu(w, "Help")) == ["STORM User Guide", "About STORM"]


def test_live_modes_hide_archive_entries(app):
    w = _host(archive=False)
    assert _texts(_menu(w, "File")) == ["Save Map Screenshot…"]
    edit = next(a for a in w.menuBar().actions() if a.text() == "Edit")
    assert not edit.isVisible()
    assert "case_open" not in w._shortcuts.bound and "track_undo" not in w._shortcuts.bound


def test_menu_entries_carry_the_table_keys_once(app):
    w = _host(archive=True)
    keys = {name: a.shortcut().toString() for name, a in w._menu_actions.items()}
    assert keys == {"case_open": "Ctrl+O", "case_export": "Ctrl+Shift+E", "screenshot": "Ctrl+S",
                    "track_undo": "Ctrl+Z", "track_redo": "Ctrl+Shift+Z", "error_log": "Ctrl+E",
                    "debug": "Ctrl+D", "studio": "Ctrl+Shift+V"}
    # bound on the menu entry, not again as a QShortcut (an ambiguous key does nothing)
    assert w._shortcuts.bound["case_open"] == [w._menu_actions["case_open"]]
    w._menu_actions["track_undo"].trigger()
    w._menu_actions["debug"].trigger()
    assert w.calls[-2:] == ["_undo_track_edit", "_toggle_debug_panel"]


def test_every_menu_entry_is_in_the_palette(app):
    w = _host(archive=True)
    labels = [c.title for c in w._palette_menu()]
    assert labels == ["Show case packages folder", "Change day", "STORM user guide", "About STORM"]
    keyed = {spec_action for spec_action in w._shortcuts.callbacks}
    assert set(w._menu_actions) <= keyed          # the keyed ones come with the shortcuts
    live = _host(archive=False)
    assert [c.title for c in live._palette_menu()] == ["STORM user guide", "About STORM"]


# ── case settings ─────────────────────────────────────────
def test_case_settings(app):
    w = _host()
    w._archive_radar = FakeRadar("KFDR")
    w.radar_controls.product, w.radar_controls.tilt, w.radar_controls.dealias = "velocity", 2, True
    assert w._case_settings() == {
        "session_date": "2024-04-27", "archive_time": "2024-04-27T20:00:00Z",
        "window_start": "2024-04-27T00:00:00Z", "window_end": "2024-04-28T03:00:00Z",
        "clock_time": "2024-04-27T20:00:00Z", "radar_station": "KFDR", "radar_product": "velocity",
        "radar_tilt_index": 2, "velocity": {"dealias": True}}


def test_default_frame_follows_the_track_or_the_clock(app):
    w = _host()
    assert w._default_case_frame(WINDOW) == (T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    w._track.load([TrackPoint(1, T0 + timedelta(hours=6), 35, -98), TrackPoint(2, T0 + timedelta(hours=8), 35, -97)])
    # 02:00-04:00Z plus 30 min either side, cut at the window's 03Z end
    assert w._default_case_frame(WINDOW) == (T0 + timedelta(hours=5, minutes=30), WINDOW[1])


# ── open ──────────────────────────────────────────────────
def _make_package(tmp_path, case_overrides=None, track_points=None, data=None):
    case = {"session_date": "2024-04-27", "archive_time": "2024-04-27T20:00:00Z",
            "clock_time": "2024-04-27T21:15:00Z", "radar_station": "KTLX",
            "radar_product": "velocity", "radar_tilt_index": 1, "velocity": {"dealias": True}}
    case.update(case_overrides or {})
    tracks = []
    if track_points:
        track = tmp_path / "storm_20240427_track.csv"
        track.write_text(track_csv_text(track_points))
        tracks = [track]
    sources, kinds = [], set()
    if data:
        for url, payload in data.items():
            provenance.record("radar", url, payload)
        sources, kinds = provenance.snapshot(), {"radar"}
    return case_package.build(tmp_path / "case.zip", case=case, tracks=tracks, sources=sources,
                              include_kinds=kinds, fetch=provenance.kept_bytes)


def test_a_package_for_another_date_is_not_opened_here(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: shown.append(a[2]) or QMessageBox.StandardButton.Cancel)
    path = _make_package(tmp_path, {"session_date": "2024-05-06"}, data={"https://s3/a": b"A"})
    w = _host()
    w._load_case_package(path)
    assert "2024-05-06" in shown[0]
    assert package_sources.active_package() is None and w.radar_controls.dealias is False
    assert w.restart_with is None


def test_a_package_for_another_date_reopens_storm_on_that_date(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    path = _make_package(tmp_path, {"session_date": "2024-05-06", "archive_time": "2024-05-06T21:30:00Z"})
    w = _host()
    closed = []
    w.close = lambda: closed.append(True)
    w._load_case_package(path)
    assert w.restart_with == main_window_case.SessionRestart(
        datetime(2024, 5, 6, 21, 30, tzinfo=UTC), str(path), launch_window=False)
    assert closed == [True] and package_sources.active_package() is None


# ── change day ────────────────────────────────────────────
def test_change_day_asks_then_closes_for_the_launch_window_on_the_clock(app, monkeypatch):
    answers = [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.pop(0))
    w = _host()
    closed = []
    w.close = lambda: closed.append(True)
    w._time_ctrl.current_time = T0 + timedelta(hours=1)
    entry = next(a for a in _menu(w, "File").actions() if a.text() == "Change Day…")
    entry.trigger()
    assert w.restart_with is None and closed == []
    entry.trigger()
    assert w.restart_with == main_window_case.SessionRestart(T0 + timedelta(hours=1))
    assert w.restart_with.launch_window and w._time_ctrl.paused and closed == [True]


def test_change_day_is_archive_only(app):
    w = _host(archive=False)
    entry = next(a for a in _menu(w, "File").actions() if a.text() == "Change Day…")
    assert not entry.isVisible()


def test_opening_restores_track_radar_clock_and_data(app, tmp_path):
    points = [TrackPoint(1, T0, 35.0, -98.0), TrackPoint(2, T0 + timedelta(minutes=30), 35.2, -97.6)]
    path = _make_package(tmp_path, track_points=points, data={"https://s3/a": b"A"})
    provenance.reset(active=True)
    w = _host()
    w._load_case_package(path)
    assert [(p.lat, p.lon) for p in w._track.points] == [(35.0, -98.0), (35.2, -97.6)]
    assert "track_changed" in w.calls
    assert w._package_radar_station == "KTLX"            # taken when the radar starts
    assert w._package_radar_view == {"product": "velocity", "tilt_index": 1}
    assert w.radar_controls.dealias is True
    assert w._time_ctrl.paused and w._time_ctrl.current_time == datetime(2024, 4, 27, 21, 15, tzinfo=UTC)
    assert package_sources.get("https://s3/a") == b"A"
    assert "Opened case package case.zip" in w.status_msg_label.text()


def test_opening_switches_a_running_radar(app, tmp_path):
    path = _make_package(tmp_path)
    w = _host()
    w._archive_radar = FakeRadar("KFDR")
    w._load_case_package(path)
    assert ("station_clicked", "KTLX") in w.calls
    w._apply_package_radar_view()                  # the new station's first scan
    assert w.radar_controls.calls == [("product", "velocity"), ("tilt", 1)]
    assert w._package_radar_view is None


def test_same_station_applies_the_view_right_away(app, tmp_path):
    path = _make_package(tmp_path)
    w = _host()
    w._archive_radar = FakeRadar("KTLX")
    w._load_case_package(path)
    assert w.radar_controls.calls == [("product", "velocity"), ("tilt", 1)]


def test_a_different_local_track_is_replaced_only_when_asked(app, tmp_path, monkeypatch):
    path = _make_package(tmp_path, track_points=[TrackPoint(1, T0, 35.0, -98.0)])
    w = _host()
    w._track.load([TrackPoint(1, T0, 36.0, -99.0), TrackPoint(2, T0 + timedelta(minutes=5), 36.1, -99.0)])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    w._load_case_package(path)
    assert len(w._track) == 2 and "track_changed" not in w.calls
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w._load_case_package(path)
    assert [(p.lat, p.lon) for p in w._track.points] == [(35.0, -98.0)]
    assert w._track.can_undo()


def test_the_same_track_is_left_alone(app, tmp_path, monkeypatch):
    points = [TrackPoint(1, T0, 35.0, -98.0)]
    path = _make_package(tmp_path, track_points=points)
    w = _host()
    w._track.load(points)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("asked"))
    w._load_case_package(path)
    assert "track_changed" not in w.calls and not w._track.can_undo()


def test_a_package_given_at_launch_is_served_before_startup(app, tmp_path):
    path = _make_package(tmp_path, data={"https://s3/a": b"A"})
    w = _host()
    w._setup_case_sources(True, str(path))
    assert w._case_package_at_start == str(path) and package_sources.active_package() == path
    w._setup_case_sources(False, None)
    assert package_sources.active_package() is None and not provenance.is_active()


def test_a_package_station_skips_the_wait_for_vehicles(app):
    w = _host()
    w._archive_mqtt = object()            # vehicles haven't been asked for
    w._package_radar_station = "KTLX"
    w._try_auto_select_radar_station()
    assert ("start_radar", "KTLX") in w.calls and w._package_radar_station is None


# ── export ────────────────────────────────────────────────
def _wait_for_export(app, w, seconds=10):
    end = time.time() + seconds
    while w._case_export is not None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert w._case_export is None


def test_export_runs_in_the_background_and_reports(app, tmp_path):
    w = _host()                      # starts this window's provenance afresh
    provenance.record("radar", "https://s3/KFDR20240427_200445_V06", b"volume")
    w._start_case_export(tmp_path / "out.zip", dict(case=w._case_settings(), tracks=[],
                                                    sources=provenance.snapshot(), include_kinds={"radar"},
                                                    time_frame=None))
    bar = w._case_export_bar
    assert bar.isVisible()
    _wait_for_export(app, w)
    assert not bar.isVisible() and bar._heading.text() != "Canceling…"
    assert "Case package saved to" in w.status_msg_label.text() and "1 data file" in w.status_msg_label.text()
    manifest, _ = case_package.read(tmp_path / "out.zip")
    assert manifest["data"][0]["url"].endswith("_V06")


def test_cancel_closes_the_progress_window(app, tmp_path, monkeypatch):
    def slow_build(path, *, cancel, **_kwargs):
        cancel.wait(5)
        raise InterruptedError("export canceled")
    monkeypatch.setattr(case_package, "build", slow_build)
    w = _host()
    w._start_case_export(tmp_path / "out.zip", dict(case={}, tracks=[], sources=[], include_kinds=set(),
                                                    time_frame=None))
    bar = w._case_export_bar
    bar._cancel.click()
    assert bar._heading.text() == "Canceling…"
    _wait_for_export(app, w)
    assert not bar.isVisible() and w.status_msg_label.text() == "Case package export canceled"
    assert not (tmp_path / "out.zip").exists()


def test_closing_the_window_cancels_an_export(app, tmp_path, monkeypatch):
    def slow_build(path, *, cancel, **_kwargs):
        cancel.wait(5)
        raise InterruptedError("export canceled")
    monkeypatch.setattr(case_package, "build", slow_build)
    w = _host()
    w._start_case_export(tmp_path / "out.zip", dict(case={}, tracks=[], sources=[], include_kinds=set(),
                                                    time_frame=None))
    job = w._case_export
    w._close_case_sources()
    assert job.cancel.is_set()
    _wait_for_export(app, w)


def test_only_one_export_at_a_time(app, tmp_path, monkeypatch):
    w = _host()
    w._case_export = object()
    monkeypatch.setattr(main_window_case, "PACKAGES_ROOT", tmp_path)
    w._export_case_package()
    assert w.status_msg_label.text() == "A case package is already being exported"
