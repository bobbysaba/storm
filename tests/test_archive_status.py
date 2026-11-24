"""ARCHIVE status chip and popover (ui/widgets/archive_status.py)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.widgets.archive_status import ArchiveStatus, format_coordinates

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def status():
    host = QWidget()
    host.resize(800, 600)
    host.show()
    st = ArchiveStatus("9.9.9", host)
    yield st
    host.close()


def _dot(st):
    return st.chip.dot.property("pillStatus")


def test_coordinates_are_short():
    assert format_coordinates(35.12346, -97.43219) == "35.1235, -97.4322"


def test_dot_is_green_when_all_is_well(status):
    status.set_radar_status("Radar: REF 0.5deg 22:00Z")
    status.set_net_state("ok")
    assert status.state() == "ok" and _dot(status) == "ok"


def test_loading_message_turns_the_dot_amber_until_cleared(status):
    status.popover.status_msg_label.setText("Radar: loading KTLX…")
    assert _dot(status) == "warn"
    status.popover.status_msg_label.setText("")
    assert _dot(status) == "ok"


def test_a_layer_error_turns_the_dot_red_and_shows_in_the_tooltip(status):
    status.popover.status_msg_label.setText("Fetching HRRR sounding…")
    status.set_satellite_status("Sat: error", error=True)
    assert _dot(status) == "error"
    assert "Sat: error" in status.chip.toolTip()
    status.set_satellite_status("Sat: AWS CONUS VIS 22:01Z")
    assert _dot(status) == "warn"


def test_offline_network_is_an_error_and_slow_a_warning(status):
    status.set_net_state("slow")
    assert _dot(status) == "warn"
    status.set_net_state("fail")
    assert _dot(status) == "error"


def test_activity_keeps_earlier_messages(status):
    label = status.popover.status_msg_label
    for msg in ("Fetching ASOS observations…", "Copied 35.10000, -97.20000", ""):
        label.setText(msg)
    assert [m for _, m in label.history] == ["Fetching ASOS observations…", "Copied 35.10000, -97.20000"]
    assert "Copied" in status.popover.history_label.text()
    assert status.popover.idle_label.isHidden()


def test_chip_click_toggles_the_popover_and_asks_for_layout(status):
    asked = []
    status.layout_requested.connect(lambda: asked.append(True))
    status.chip.clicked.emit()
    assert status.popover.isVisible()
    status.chip.clicked.emit()
    assert not status.popover.isVisible()
    assert len(asked) == 2


def test_radar_done_loading_clears_only_its_own_message(status):
    from types import SimpleNamespace
    from ui.app.main_window_archive import MainWindowArchiveMixin
    label = status.popover.status_msg_label
    host = SimpleNamespace(_archive_radar=SimpleNamespace(station="KTLX"), status_msg_label=label)
    changed = MainWindowArchiveMixin._on_archive_radar_loading_changed
    changed(host, True)
    assert label.text() == "Radar: loading KTLX…"
    changed(host, False)
    assert label.text() == ""
    changed(host, True)
    label.setText("Lidar: Lidar truck · stop 1 of 1, first scan 19:48:23 UTC")
    changed(host, False)
    assert label.text().startswith("Lidar:")
