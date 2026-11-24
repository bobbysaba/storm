"""Several providers' lidars and wind profiles as one (archive/combined.py),
and the lidar drawer's buttons (ui/app/main_window_lidar.py)."""
import threading
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QToolButton

from archive import combined


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Stares(QObject):
    found = pyqtSignal(dict)
    ready = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    progress = pyqtSignal(str, str)

    def __init__(self, instruments):
        super().__init__()
        self.sources = tuple(SimpleNamespace(instrument=i) for i in instruments)
        self.loads = []

    def name(self, instrument):
        return f"{instrument} lidar"

    def fetch(self):
        pass

    def cancel(self):
        pass

    def load(self, instrument):
        self.loads.append(instrument)


class _Lidar(QObject):
    assets_ready = pyqtSignal(dict)
    sites_ready = pyqtSignal(dict)
    file_ready = pyqtSignal(str, object)
    large_file = pyqtSignal(str, object, int)
    instrument_loaded = pyqtSignal(str, int)
    survey_done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, instruments, vehicle, description):
        super().__init__()
        self.sources = tuple(SimpleNamespace(instrument=i, platform_id=f"{i}-PPI") for i in instruments)
        self.track_vehicle = vehicle
        self.description = description
        self.stares = _Stares(instruments)
        self.surveyed, self.loaded, self.track = [], [], None
        self.cancelled = False

    def set_truck_track(self, source):
        self.track = source

    def fetch(self):
        pass

    def cancel(self):
        self.cancelled = True

    def survey(self, assets):
        self.surveyed.append(assets)

    def load_files(self, instrument, assets):
        self.loaded.append((instrument, assets))


def test_one_or_no_fetcher_is_used_as_it_is():
    one = _Lidar(["A"], "truck", "")
    assert combined.lidar([one]) is one and combined.lidar([None]) is None
    assert combined.wind_profiles([]) is None


def test_two_providers_lidars_answer_as_one(app):
    team, nssl = _Lidar(["OURS"], None, "our lidar"), _Lidar(["DLTRUCK1", "CLAMPS1"], "dltruck", "NSSL's")
    both = combined.lidar([team, nssl])
    assert [s.instrument for s in both.sources] == ["OURS", "DLTRUCK1", "CLAMPS1"]
    assert both.description == "our lidar; NSSL's"
    got = []
    both.assets_ready.connect(got.append)
    team.assets_ready.emit({"OURS-PPI": ["a"]})
    assert got == []                                   # waits for every provider's
    nssl.assets_ready.emit({"DLTRUCK1-PPI": ["b"], "CLAMPS1-PPI": None})
    assert got == [{"OURS-PPI": ["a"], "DLTRUCK1-PPI": ["b"], "CLAMPS1-PPI": None}]

    done = []
    both.survey_done.connect(lambda: done.append(1))
    both.survey({"OURS": ["a"], "DLTRUCK1": ["b"]})
    assert team.surveyed == [{"OURS": ["a"]}] and nssl.surveyed == [{"DLTRUCK1": ["b"]}]
    team.survey_done.emit()
    assert done == []
    nssl.survey_done.emit()
    assert done == [1]
    both.load_files("CLAMPS1", ["big"])
    assert nssl.loaded == [("CLAMPS1", ["big"])] and team.loaded == []

    # each lidar on its own vehicle's track
    asked = []
    both.set_truck_track(lambda vehicle: asked.append(vehicle) or [])
    team.track(), nssl.track()
    assert asked == [None, "dltruck"]
    both.cancel()
    assert team.cancelled and nssl.cancelled

    # their stares too
    found = []
    both.stares.found.connect(found.append)
    team.stares.found.emit({"OURS": None})
    nssl.stares.found.emit({"DLTRUCK1": ["fp"]})
    assert found == [{"OURS": None, "DLTRUCK1": ["fp"]}]
    progress = []
    both.stares.progress.connect(lambda instrument, text: progress.append(instrument))
    nssl.stares.progress.emit("DLTRUCK1", "Downloading…")
    assert progress == ["DLTRUCK1"]
    both.stares.load("DLTRUCK1")
    assert nssl.stares.loads == ["DLTRUCK1"] and both.stares.name("CLAMPS1") == "CLAMPS1 lidar"


def test_a_file_found_on_a_worker_thread_reaches_the_window(app):
    team, nssl = _Lidar(["OURS"], None, ""), _Lidar(["DLTRUCK1"], "dltruck", "")
    both = combined.lidar([team, nssl])
    got = []
    both.file_ready.connect(lambda instrument, rays: got.append((instrument, rays)))
    worker = threading.Thread(target=lambda: nssl.file_ready.emit("DLTRUCK1", "rays"))
    worker.start()
    worker.join()
    app.processEvents()
    assert got == [("DLTRUCK1", "rays")]


def test_a_lidar_two_providers_both_offer_goes_to_the_first():
    first, second = _Lidar(["X"], None, ""), _Lidar(["X", "Y"], None, "")
    both = combined.lidar([first, second])
    assert [s.instrument for s in both.sources] == ["X", "Y"]
    both.load_files("X", ["f"])
    assert first.loaded == [("X", ["f"])] and second.loaded == []


class _Wind(QObject):
    sets_ready = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, prefix):
        super().__init__()
        self.prefix, self.sets, self.fetches = prefix, None, 0
        self.description = f"{prefix} winds"

    def is_loading(self):
        return False

    def source_name(self, platform_id):
        return f"{self.prefix}: {platform_id}"

    def fetch(self):
        self.fetches += 1
        return True


def test_wind_profiles_from_two_providers_come_back_together(app):
    team, nssl = _Wind("ours"), _Wind("nssl")
    both = combined.wind_profiles([team, nssl])
    got, errors = [], []
    both.sets_ready.connect(got.append)
    both.error.connect(errors.append)
    assert both.fetch() and not both.fetch()             # the second waits for the first's answer
    assert (team.fetches, nssl.fetches) == (1, 1)
    team.sets_ready.emit({"PROFILER": "set a"})
    assert got == [] and both.is_loading()
    nssl.error.emit("THREDDS timed out")
    assert got == [{"PROFILER": "set a"}] and errors == []
    assert both.source_name("PROFILER") == "ours: PROFILER"
    assert both.sets is None                             # nssl's failed: asked again next time
    team.sets, nssl.sets = {"PROFILER": "set a"}, {"CLAMPS2-VAD": "set b"}
    assert both.sets == {"PROFILER": "set a", "CLAMPS2-VAD": "set b"}

    both.fetch()
    team.error.emit("down")
    nssl.error.emit("down")
    assert errors == ["down"]


class _LidarHost:
    from ui.app.main_window_lidar import MainWindowLidarMixin as _mixin
    _add_lidar_button = _mixin._add_lidar_button
    _set_lidar_button = _mixin._set_lidar_button


def test_a_drawers_lidar_row_follows_the_teams_lidar_button(app):
    host = _LidarHost()
    host.btn_raw_lidar = QToolButton()
    host.btn_raw_lidar.setCheckable(True)
    host._lidar_buttons = [host.btn_raw_lidar]
    host._set_lidar_button(False, "Looking for lidar data for this date…")
    row = QToolButton()
    row.setCheckable(True)
    host._add_lidar_button(row)
    assert not row.isEnabled() and row.toolTip().startswith("Looking")
    host._set_lidar_button(True, "Doppler lidar: ours; NSSL's")
    assert row.isEnabled() and row.toolTip() == "Doppler lidar: ours; NSSL's"
    row.setChecked(True)
    assert host.btn_raw_lidar.isChecked()
    host.btn_raw_lidar.setChecked(False)
    assert not row.isChecked()
