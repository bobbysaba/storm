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


def _manager(settings, **drawer_heights):
    mgr = DrawerManager(settings)
    buttons = {}
    for key, h in drawer_heights.items():
        drawer = _FakeDrawer(h)
        buttons[key] = _FakeButton(drawer)
        mgr.add(key, drawer, buttons[key], child=(key == "route"))
    return mgr, buttons


def _open(buttons):
    return sorted(k for k, b in buttons.items() if b.isChecked())


def test_one_drawer_at_a_time(settings):
    mgr, b = _manager(settings, radar=64, hazards=36)
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    assert _open(b) == ["hazards"]


def test_nothing_is_pinned_by_default(settings):
    mgr, b = _manager(settings, radar=64, sounding=37, prev_locs=40)
    assert not any(mgr.is_pinned(k) for k in b)
    b["prev_locs"].setChecked(True)
    b["sounding"].setChecked(True)
    assert _open(b) == ["sounding"]


@pytest.mark.parametrize("old, migrated", [
    (["prev_locs", "sounding"], set()),                  # the old default
    (["prev_locs", "radar", "sounding"], {"radar"}),     # user pins are kept
    (["hazards"], {"hazards"}),
])
def test_old_default_pins_are_dropped(settings, old, migrated):
    settings.setValue("ui/pinned_drawers", old)
    mgr, b = _manager(settings, radar=64, hazards=36, sounding=37, prev_locs=40)
    assert {k for k in b if mgr.is_pinned(k)} == migrated
    assert not settings.contains("ui/pinned_drawers")
    mgr.set_pinned("sounding", True)                     # migrates once, not every launch
    again, b = _manager(settings, sounding=37)
    assert again.is_pinned("sounding")


def test_pinned_drawers_stay_open_and_close_nothing(settings):
    mgr, b = _manager(settings, radar=64, hazards=36, sounding=37)
    mgr.set_pinned("sounding", True)
    b["radar"].setChecked(True)
    b["sounding"].setChecked(True)
    assert _open(b) == ["radar", "sounding"]
    b["hazards"].setChecked(True)
    assert _open(b) == ["hazards", "sounding"]


def test_child_drawers_are_exempt(settings):
    mgr, b = _manager(settings, map=37, route=280, radar=64)
    b["map"].setChecked(True)
    b["route"].setChecked(True)
    assert _open(b) == ["map", "route"]


def test_close_newest_closes_in_reverse_open_order(settings):
    mgr, b = _manager(settings, radar=64, hazards=36, sounding=40)
    mgr.set_pinned("radar", True)
    mgr.set_pinned("sounding", True)
    for key in ("radar", "sounding", "hazards"):
        b[key].setChecked(True)
    assert mgr.close_newest()
    assert _open(b) == ["radar", "sounding"]
    assert mgr.close_newest()                     # pinned drawers close on request too
    assert _open(b) == ["radar"]
    assert mgr.close_newest(skip=("radar",)) is False
    assert mgr.close_newest()
    assert not mgr.close_newest()


def test_enforce_fit_closes_oldest_unpinned_first(settings):
    mgr, b = _manager(settings, radar=64, hazards=200, satellite=67, map=37)
    for key in ("radar", "hazards", "satellite"):
        mgr.set_pinned(key, True)                 # pinned: they stack
    for key in ("radar", "hazards", "satellite", "map"):
        b[key].setChecked(True)
    for key in ("radar", "hazards", "satellite"):
        mgr.set_pinned(key, False)                # now closable by the fit rule
    closed = mgr.enforce_fit(available=250, fixed_heights=[0])
    assert closed == ["radar", "hazards"]
    assert _open(b) == ["map", "satellite"]


def test_enforce_fit_never_closes_newest(settings):
    mgr, b = _manager(settings, radar=64, hazards=900)
    mgr.set_pinned("radar", True)
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    mgr.set_pinned("radar", False)
    mgr.enforce_fit(available=500)
    assert _open(b) == ["hazards"]


def test_pins_persist_and_old_mode_setting_is_ignored(settings):
    settings.setValue("ui/drawer_mode", "stack")  # saved by an older version
    mgr, _b = _manager(settings, radar=64)
    mgr.set_pinned("radar", True)
    mgr.set_pinned("sounding", False)
    again, b = _manager(settings, radar=64, hazards=36)
    assert again.is_pinned("radar")
    assert not again.is_pinned("sounding")
    again.set_pinned("radar", False)
    b["radar"].setChecked(True)
    b["hazards"].setChecked(True)
    assert _open(b) == ["hazards"]                # one at a time regardless
