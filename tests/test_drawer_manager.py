import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, QSettings, QSize, pyqtSignal

from ui.app.drawer_manager import DrawerManager


class _FakeDrawer(QObject):
    opened = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, height):
        super().__init__()
        self.height = height
        self.open = False

    def toggle_drawer(self, checked):
        self.open = checked
        (self.opened if checked else self.closed).emit()

    def sizeHint(self):
        return QSize(300, self.height)


class _FakeButton(QObject):
    """Checkable button wired to its drawer like the toolbar buttons."""

    def __init__(self, drawer):
        super().__init__()
        self._checked = False
        self._drawer = drawer

    def isChecked(self):
        return self._checked

    def setChecked(self, on):
        if on != self._checked:
            self._checked = on
            self._drawer.toggle_drawer(on)


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "storm.ini"), QSettings.Format.IniFormat)


def _manager(settings, height=768, **drawer_heights):
    mgr = DrawerManager(lambda: height, settings)
    buttons = {}
    for key, h in drawer_heights.items():
        drawer = _FakeDrawer(h)
        buttons[key] = _FakeButton(drawer)
        mgr.add(key, drawer, buttons[key], child=(key == "route"))
    return mgr, buttons


def _open(buttons):
    return sorted(k for k, b in buttons.items() if b.isChecked())


def test_auto_mode_is_one_at_a_time_on_short_windows(settings):
    mgr, b = _manager(settings, height=768, radar=64, hazards=36)
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    assert _open(b) == ["hazards"]


def test_auto_mode_stacks_on_tall_windows(settings):
    mgr, b = _manager(settings, height=1000, radar=64, hazards=36)
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    assert _open(b) == ["hazards", "radar"]


def test_pinned_drawers_stay_open_and_close_nothing(settings):
    mgr, b = _manager(settings, height=768, radar=64, hazards=36, sounding=37)
    assert mgr.is_pinned("sounding")  # default pin
    b["radar"].setChecked(True)
    b["sounding"].setChecked(True)
    assert _open(b) == ["radar", "sounding"]
    b["hazards"].setChecked(True)
    assert _open(b) == ["hazards", "sounding"]


def test_child_drawers_are_exempt(settings):
    mgr, b = _manager(settings, height=768, map=37, route=280, radar=64)
    b["map"].setChecked(True)
    b["route"].setChecked(True)
    assert _open(b) == ["map", "route"]


def test_close_newest_closes_in_reverse_open_order(settings):
    mgr, b = _manager(settings, height=1000, radar=64, hazards=36, sounding=40)
    for key in ("radar", "sounding", "hazards"):
        b[key].setChecked(True)
    assert mgr.close_newest()
    assert _open(b) == ["radar", "sounding"]
    assert mgr.close_newest()                     # pinned drawers close on request too
    assert _open(b) == ["radar"]
    assert mgr.close_newest(skip=("radar",)) is False
    assert mgr.close_newest()
    assert not mgr.close_newest()


def test_switching_to_one_closes_all_but_newest(settings):
    mgr, b = _manager(settings, height=1000, radar=64, hazards=36, satellite=67)
    for key in ("radar", "hazards", "satellite"):
        b[key].setChecked(True)
    mgr.set_mode("one")
    assert _open(b) == ["satellite"]


def test_enforce_fit_closes_oldest_unpinned_first(settings):
    mgr, b = _manager(settings, height=1000, radar=64, hazards=200, satellite=67, map=37)
    for key in ("radar", "hazards", "satellite", "map"):
        b[key].setChecked(True)
    closed = mgr.enforce_fit(available=250, fixed_heights=[0])
    assert closed == ["radar", "hazards"]
    assert _open(b) == ["map", "satellite"]


def test_enforce_fit_never_closes_newest(settings):
    mgr, b = _manager(settings, height=1000, radar=64, hazards=900)
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    mgr.enforce_fit(available=500)
    assert _open(b) == ["hazards"]


def test_mode_and_pins_persist(settings):
    mgr, _b = _manager(settings, radar=64)
    mgr.set_mode("stack")
    mgr.set_pinned("radar", True)
    mgr.set_pinned("sounding", False)
    again, _b = _manager(settings, radar=64)
    assert again.mode == "stack"
    assert again.is_pinned("radar")
    assert not again.is_pinned("sounding")


def test_enforce_fit_applies_one_mode_after_window_shrinks(settings):
    height = [1000]
    mgr = DrawerManager(lambda: height[0], settings)
    b = {}
    for key, h in (("radar", 64), ("hazards", 36)):
        d = _FakeDrawer(h)
        b[key] = _FakeButton(d)
        mgr.add(key, d, b[key])
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    height[0] = 768
    mgr.enforce_fit(available=600)
    assert _open(b) == ["hazards"]
