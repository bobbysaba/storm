"""Tests for archive playback control timing."""

from datetime import datetime, timedelta, timezone

from PyQt6.QtWidgets import QApplication

from archive.time_controller import TimeController
from ui.controls.archive_controls import ArchiveControls


# Held here: when the QApplication is collected it takes the TimeController
# and widgets with it, so it can't live only in a test's throwaway `_`.
_app = QApplication.instance() or QApplication([])


def _controls():
    controller = TimeController(
        datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    )
    return _app, controller, ArchiveControls(controller)


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


_SCANS = ["2026-04-16T11:45:00Z", "2026-04-16T11:52:00Z",
          "2026-04-16T12:03:00Z", "2026-04-16T12:10:00Z"]


def _at(h, m, s=0):
    return datetime(2026, 4, 16, h, m, s, tzinfo=timezone.utc)


def test_scan_buttons_jump_to_the_nearest_indexed_scan():
    _, controller, controls = _controls()
    controls.set_available_scan_times(_SCANS)

    controls._btn_prev_scan.click()
    assert controller.current_time == _at(11, 52)
    controls._btn_next_scan.click()
    controls._btn_next_scan.click()
    assert controller.current_time == _at(12, 10)
    controls._btn_next_scan.click()                 # no later scan: stays put
    assert controller.current_time == _at(12, 10)


def test_scan_buttons_wait_for_the_index():
    _, controller, controls = _controls()
    assert not controls._btn_prev_scan.isEnabled()
    controls.prev_scan()
    controls.next_scan()
    assert controller.current_time == _at(12, 0)
    controls.set_available_scan_times(_SCANS)
    assert controls._btn_next_scan.isEnabled()
    controls.set_available_scan_times([])           # station changed, new index pending
    assert not controls._btn_next_scan.isEnabled()


def test_scan_buttons_ignore_precision_mode_and_pause():
    _, controller, controls = _controls()
    controls.set_available_scan_times(_SCANS)
    controls.set_precision_mode(True)
    controller.play()
    controls.next_scan()
    assert controller.current_time == _at(12, 3)
    assert not controller.is_playing


def test_slider_follows_the_clock_and_seeks_on_release():
    _, controller, controls = _controls()
    start, end = controller.window
    assert controls._slider.maximum() == controller.window_seconds() - 1
    assert controls._slider.value() == controller.seconds_since_start()

    controller.step(60)
    assert controls._slider.value() == controller.seconds_since_start()

    target = (_at(13, 30) - start).total_seconds()
    controls._on_slider_preview(int(target))        # dragging: label only
    assert controls._utc_label.text() == "13:30:00 UTC"
    assert controller.current_time == _at(12, 1)
    controls._slider.setValue(int(target))          # release
    assert controller.current_time == _at(13, 30)


def test_slider_range_follows_the_window():
    _, controller, controls = _controls()
    start, _ = controller.window
    controller.set_window_end(start + timedelta(hours=20))
    assert controls._slider.maximum() == 20 * 3600 - 1
    assert controls._slider.value() == controller.seconds_since_start()


def test_coverage_strip_shows_only_with_spans():
    _, controller, controls = _controls()
    assert controls._coverage.isHidden()
    controls.set_coverage([(_at(12, 0), _at(13, 0))], tooltip="Lidar")
    assert not controls._coverage.isHidden() and controls._coverage.toolTip() == "Lidar"
    controls.set_coverage([])
    assert controls._coverage.isHidden()


def test_bar_paints_at_narrow_and_wide_widths():
    _, controller, controls = _controls()
    controls.set_available_scan_times(_SCANS)
    controls.set_coverage([(_at(12, 0), _at(13, 0))])
    for width in (360, 680, 1400):
        controls.resize(width, controls.sizeHint().height())
        assert not controls.grab().isNull()
