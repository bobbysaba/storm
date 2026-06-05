"""Tests for archive playback control timing."""

from datetime import datetime, timezone

from PyQt6.QtWidgets import QApplication

from archive.time_controller import TimeController
from ui.controls.archive_controls import ArchiveControls


def _controls():
    app = QApplication.instance() or QApplication([])
    controller = TimeController(
        datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    )
    return app, controller, ArchiveControls(controller)


def test_normal_archive_controls_use_minute_and_ten_second_steps():
    _, controller, controls = _controls()

    controls._btn_start.click()
    assert controller.current_time.hour == 11
    assert controller.current_time.minute == 59

    controls._btn_end.click()
    controls._btn_back.click()
    assert controller.current_time.second == 50

    controls._btn_fwd.click()
    assert controller.current_time.second == 0


def test_precision_archive_controls_use_ten_and_one_second_steps():
    _, controller, controls = _controls()
    controls.set_precision_mode(True)

    controls._btn_start.click()
    controls._btn_back.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 11, 59, 49, tzinfo=timezone.utc
    )

    controls._btn_fwd.click()
    controls._btn_end.click()
    assert controller.current_time == datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc
    )
