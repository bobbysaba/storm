"""The SATELLITE drawer works before MainWindow's deferred _init_satellite has run."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ui.app import main_window_overlays
from ui.app.main_window import MainWindow


class _Anything:
    """Accepts any method call (map widget, layer status, status label)."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeFetcher(QObject):
    meso_sectors_updated = pyqtSignal(dict)
    frames_updated = pyqtSignal(str, list)
    fetch_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_modes = []
        self.history = []

    def start(self):
        pass

    def set_active_mode(self, mode):
        self.active_modes.append(mode)

    def fetch_history(self, mode, count):
        self.history.append(mode)


class _FakeControls(QObject):
    mode_changed = pyqtSignal(str)
    opacity_changed = pyqtSignal(float)
    frame_requested = pyqtSignal(int)
    loop_toggled = pyqtSignal(bool)
    speed_changed = pyqtSignal(int)
    meso_preview = pyqtSignal(int, bool)

    def __init__(self, mode=""):
        super().__init__()
        self.mode = mode
        self.stopped = 0

    def current_mode(self):
        return self.mode

    def stop_loop(self):
        self.stopped += 1

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Host(QObject):
    """Stand-in window: the real satellite handlers, nothing initialised yet."""
    _init_satellite = MainWindow._init_satellite
    _on_satellite_toggled = MainWindow._on_satellite_toggled
    _on_satellite_mode_changed = MainWindow._on_satellite_mode_changed
    _on_satellite_frame_requested = MainWindow._on_satellite_frame_requested
    _on_satellite_loop_toggled = MainWindow._on_satellite_loop_toggled
    _on_satellite_speed_changed = MainWindow._on_satellite_speed_changed
    _on_satellite_frames_updated = MainWindow._on_satellite_frames_updated
    _on_satellite_error = MainWindow._on_satellite_error
    _on_meso_sectors_updated = MainWindow._on_meso_sectors_updated
    _on_meso_preview = MainWindow._on_meso_preview
    _satellite_loop_tick = MainWindow._satellite_loop_tick

    def __init__(self, mode=""):
        super().__init__()
        self._archive = False
        self.active = []
        self.satellite_controls = _FakeControls(mode)
        self.map_widget = _Anything()
        self.status_msg_label = _Anything()
        self._layer_status = {"satellite": _Anything()}

    def _set_layer_active(self, key, on):
        self.active.append((key, on))

    def _layout_overlays(self):
        pass


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fake_fetcher(monkeypatch):
    monkeypatch.setattr(main_window_overlays, "SatelliteFetcher", _FakeFetcher)


def test_toggle_before_init_does_not_raise(app):
    host = _Host(mode="conus")
    host._on_satellite_toggled(True)
    host._on_satellite_toggled(False)
    assert host.active == [("satellite", True), ("satellite", False)]


def test_init_applies_sector_picked_before_init(app):
    host = _Host(mode="conus")
    host._init_satellite()
    assert host._satellite_fetcher.active_modes == ["conus"]
    assert host._satellite_fetcher.history == ["conus"]


def test_init_without_early_sector_selects_nothing(app):
    host = _Host()
    host._init_satellite()
    assert host._satellite_fetcher.active_modes == []
    host._on_satellite_toggled(False)
    assert host.satellite_controls.stopped == 1
