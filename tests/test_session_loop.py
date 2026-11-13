"""main.py's session loop: Change Day and a case package for another date
open the next session without quitting STORM; the map frees storm:// first."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PyQt6.QtWebEngineWidgets")      # before any QApplication
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

import main
from ui.app.main_window_case import SessionRestart

UTC = timezone.utc
DAY1 = datetime(2024, 4, 27, 20, tzinfo=UTC)
DAY2 = datetime(2024, 5, 6, 21, 30, tzinfo=UTC)


@pytest.fixture
def app(monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main, "_warn_missing_files", lambda: None)
    import ui.dialogs.loading_dialog as loading
    monkeypatch.setattr(loading, "LoadingDialog", lambda: SimpleNamespace(show=lambda: None, close=lambda: None))
    return qapp


class FakeDialog:
    """Answers the launch window in turn: (accepted, archive_time, case, admin)."""
    answers: list = []
    made: list = []

    def __init__(self, mode=None, archive_time=None, verified_modes=()):
        FakeDialog.made.append({"mode": mode, "archive_time": archive_time, "verified": set(verified_modes)})
        self._answer = FakeDialog.answers.pop(0)

    def exec(self):
        return QDialog.DialogCode.Accepted if self._answer[0] else QDialog.DialogCode.Rejected

    def archive_start_time(self):
        return self._answer[1]

    def case_package(self):
        return self._answer[2]

    def admin_mode(self):
        return self._answer[3]

    def archive(self):
        return self._answer[1] is not None

    vehicle_id = data_dir = lambda self: ""
    vehicle_icon = lambda self: "car"
    gps_file_mode = monitor = viewer = lambda self: False
    radar_resolution = lambda self: -1
    deleteLater = lambda self: None


def _window_class(restarts):
    """Windows that each close with the next restart_with; records what they opened."""
    opened = []

    class FakeWindow(QWidget):
        def __init__(self, **kwargs):
            super().__init__()
            opened.append(kwargs)
            self.map_widget = SimpleNamespace()
            self.restart_with = restarts.pop(0)
    return FakeWindow, opened


def _run(app, monkeypatch, restarts, answers, case=None):
    FakeDialog.answers, FakeDialog.made = list(answers), []
    monkeypatch.setattr(main, "LaunchDialog", FakeDialog)
    monkeypatch.setattr(app, "exec", lambda: 0)
    window_class, opened = _window_class(list(restarts))
    args = SimpleNamespace(case=case, debug=False, truck_replay_file=None)
    code = main._run_sessions(app, args, window_class=window_class)
    return code, opened


def test_a_normal_quit_runs_one_session(app, monkeypatch):
    code, opened = _run(app, monkeypatch, [None], [(True, DAY1, None, False)])
    assert code == 0 and [o["archive_time"] for o in opened] == [DAY1]
    assert FakeDialog.made == [{"mode": None, "archive_time": None, "verified": set()}]


def test_change_day_reopens_the_launch_window_in_archive_on_the_last_date(app, monkeypatch):
    code, opened = _run(app, monkeypatch, [SessionRestart(DAY1), None],
                        [(True, DAY1, None, True), (True, DAY2, None, True)])
    assert [o["archive_time"] for o in opened] == [DAY1, DAY2]
    second = FakeDialog.made[1]
    assert second["mode"] == "archive" and second["archive_time"] == DAY1
    assert second["verified"] == {"admin"}          # no admin passphrase again


def test_closing_the_launch_window_after_change_day_quits(app, monkeypatch):
    code, opened = _run(app, monkeypatch, [SessionRestart(DAY1)],
                        [(True, DAY1, None, False), (False, None, None, False)])
    assert code == 0 and len(opened) == 1


def test_a_package_for_another_date_opens_it_directly(app, monkeypatch):
    code, opened = _run(app, monkeypatch, [SessionRestart(DAY2, "/x/case.zip", launch_window=False), None],
                        [(True, DAY1, None, False)])
    assert len(FakeDialog.made) == 1
    assert opened[1]["archive_time"] == DAY2 and opened[1]["case_package"] == "/x/case.zip"
    assert not opened[1]["monitor"] and not opened[1]["viewer"]


def test_case_at_launch_skips_the_launch_window_only_the_first_time(app, monkeypatch):
    monkeypatch.setattr(main, "_case_archive_time", lambda path: DAY1)
    code, opened = _run(app, monkeypatch, [SessionRestart(DAY1), None],
                        [(True, DAY2, None, False)], case="/x/case.zip")
    assert opened[0]["case_package"] == "/x/case.zip"
    assert len(FakeDialog.made) == 1 and opened[1]["archive_time"] == DAY2


def test_the_closed_sessions_windows_are_deleted_and_its_other_windows_closed(app, monkeypatch):
    stray = QWidget()          # e.g. a sounding window left open
    stray.show()
    alive = []
    FakeDialog.answers, FakeDialog.made = [(True, DAY1, None, False), (True, DAY2, None, False)], []
    monkeypatch.setattr(main, "LaunchDialog", FakeDialog)
    monkeypatch.setattr(app, "exec", lambda: 0)
    restarts = [SessionRestart(DAY1), None]

    class Window(QWidget):
        def __init__(self, **kwargs):
            super().__init__()
            assert alive == []         # the last session's window is gone first
            alive.append(self)
            self.destroyed.connect(alive.clear)
            self.map_widget = SimpleNamespace()
            self.restart_with = restarts.pop(0)
    main._run_sessions(app, SimpleNamespace(case=None, debug=False, truck_replay_file=None), window_class=Window)
    assert not stray.isVisible()
    assert len(alive) == 1             # the last one, on a real quit, isn't deleted here


# ── the map frees storm:// for the next window ─────────────
def test_map_shutdown_removes_its_scheme_handler_once(monkeypatch):
    from ui.map import widget
    removed = []
    profile = SimpleNamespace(removeUrlSchemeHandler=removed.append)
    import PyQt6.QtWebEngineCore as core
    monkeypatch.setattr(core.QWebEngineProfile, "defaultProfile", staticmethod(lambda: profile))
    monkeypatch.setattr(widget, "SAFE_MAP_MODE", False)
    handler = object()
    fake = SimpleNamespace(_scheme_handler=handler)
    widget.MapWidget.shutdown(fake)
    widget.MapWidget.shutdown(fake)
    assert removed == [handler]


def test_closing_the_window_shuts_the_map_down():
    import inspect
    from ui.app.main_window import MainWindow
    assert "self.map_widget.shutdown()" in inspect.getsource(MainWindow.closeEvent)
