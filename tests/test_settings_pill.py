"""The settings pill opened by the gear (text size, VEH, shortcuts, reset)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from ui.widgets.settings_pill import SettingsPill

_KEEP = []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pill():
    p = SettingsPill((100, 115, 130))
    _KEEP.append(p)
    return p


def test_text_size_quiet_set_and_choice(app):
    p = _pill()
    got = []
    p.text_scale_chosen.connect(got.append)
    p.set_text_scale(115)                         # from the window: no echo
    assert p._scale_buttons[115].isChecked() and got == []
    p._scale_buttons[130].click()
    p.choose_text_scale(100)                      # the palette
    assert got == [130, 100]


def test_vehicles_button_follows_and_drives_radar_state(app):
    p = _pill()
    got = []
    p.vehicles_toggled.connect(got.append)
    p.set_vehicles(True)                          # from the window: no echo
    assert p.btn_vehicles.isChecked() and p.btn_vehicles.isEnabled() and got == []
    p.btn_vehicles.click()
    assert got == [False] and not hasattr(p, "btn_sync")


def test_loop_row_hidden_without_a_live_loop(app):
    p = _pill()
    p.show()
    p.set_loop_available(False)
    assert not p._loop_row.isVisible()
    p.set_loop_available(True)
    assert p._loop_row.isVisible()


def test_shortcuts_and_reset(app):
    p = _pill()
    got = []
    p.shortcuts_clicked.connect(lambda: got.append("keys"))
    p.reset_clicked.connect(lambda: got.append("reset"))
    p.btn_shortcuts.click()
    p.btn_reset.click()
    assert got == ["keys", "reset"]
