"""The launch form scrolls on a short screen while LAUNCH stays reachable."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication, QSettings
from PyQt6.QtWidgets import QApplication, QScrollArea

from ui.launch.dialog import LaunchDialog


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    # read settings from an empty folder, not this computer's saved launch settings
    old_org, old_format = QCoreApplication.organizationName(), QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setOrganizationName("STORM-test")
    monkeypatch.setattr(LaunchDialog, "_start_update_check", lambda self: None)   # no git/network
    d = LaunchDialog()
    d.show()
    app.processEvents()
    yield d
    d.close()
    d.deleteLater()
    app.processEvents()
    QCoreApplication.setOrganizationName(old_org)
    QSettings.setDefaultFormat(old_format)


def _in_scroll_area(widget) -> bool:
    while widget is not None:
        if isinstance(widget, QScrollArea):
            return True
        widget = widget.parentWidget()
    return False


def test_launch_button_sits_outside_the_scrolling_form(dialog):
    assert not _in_scroll_area(dialog._launch_btn)
    assert _in_scroll_area(dialog._vid_input)


def test_window_fits_the_screen_and_form_gets_its_full_height(dialog):
    dialog._post_layout_adjust()
    available = dialog.screen().availableGeometry().height()
    assert dialog.height() <= max(240, available - 60)
    assert dialog._form.minimumHeight() > 0


def test_a_window_the_user_moved_is_not_recentered(dialog):
    dialog._post_layout_adjust()
    dialog.move(dialog.x() + 37, dialog.y() + 23)
    QApplication.processEvents()
    moved_to = dialog.pos()
    dialog._post_layout_adjust()
    assert dialog.pos() == moved_to
