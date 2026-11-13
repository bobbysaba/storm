"""The launch window's archive section: calendar, year grid, the background
date scan, Open case… and the Change Day presets."""
import os
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QCoreApplication, QDate, QDateTime, QRect, QSettings, QTime, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QSpinBox, QToolButton

from archive.catalog import ALL_PLATFORMS, AvailabilitySnapshot, PlatformAvailability
from core import case_package
from ui import palette as P
from ui.launch import dialog as dialog_module
from ui.launch.availability import AvailabilityWorker
from ui.launch.dialog import LaunchDialog, _AvailabilityCalendar, _YearGridPopup


@pytest.fixture(autouse=True)
def app(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    # an empty settings folder, not this computer's saved launch settings
    old_org, old_format = QCoreApplication.organizationName(), QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setOrganizationName("STORM-test")
    monkeypatch.setattr(LaunchDialog, "_start_update_check", lambda self: None)   # no git/network
    monkeypatch.setattr(AvailabilityWorker, "request", lambda *args: None)
    yield qapp
    for w in QApplication.topLevelWidgets():
        w.close()
    qapp.processEvents()
    QCoreApplication.setOrganizationName(old_org)
    QSettings.setDefaultFormat(old_format)


def _snapshot(dates=(), failed=False, partial=False):
    results = {p.platform_id: PlatformAvailability(frozenset(), 1, 1, ()) for p in ALL_PLATFORMS}
    results[ALL_PLATFORMS[0].platform_id] = PlatformAvailability(
        frozenset(dates), 1, 2 if partial else 1, ("server timed out",) if failed else ())
    return AvailabilitySnapshot(results, len(results), len(results) + int(partial))


def _apply(dlg, snapshot):
    dlg._select_mode("archive")
    dlg._on_availability_updated(dlg._availability_generation, snapshot)


def _freeze_today(monkeypatch, year, month=6, day=15):
    monkeypatch.setattr(dialog_module.QDate, "currentDate", staticmethod(lambda: QDate(year, month, day)))


# ── calendar button ─────────────────────────────────────────
def test_calendar_button_sits_beside_a_still_typeable_date():
    dlg = LaunchDialog()
    assert dlg._cal_btn.toolTip()
    assert dlg._archive_dt_edit.displayFormat() == "yyyy-MM-dd  HH:mm:ss"
    assert dlg._archive_dt_edit.calendarPopup() is True
    assert isinstance(dlg._archive_dt_edit.calendarWidget(), _AvailabilityCalendar)


def test_calendar_button_opens_on_the_fields_date_and_reuses_its_popup():
    dlg = LaunchDialog()
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2023, 6, 15), QTime(14, 30, 0)))
    dlg._cal_btn.click()
    first = dlg._calendar_popup
    assert first.isVisible() and first.selectedDate() == QDate(2023, 6, 15)
    dlg._cal_btn.click()
    assert dlg._calendar_popup is first


def test_picking_a_day_keeps_the_time_and_closes_the_calendar():
    dlg = LaunchDialog()
    dlg._select_mode("archive")
    dlg._archive_dt_edit.setDateTime(QDateTime(QDate(2023, 6, 15), QTime(20, 30, 45)))
    dlg._cal_btn.click()
    dlg._on_date_picked(QDate(2019, 5, 28))
    assert dlg.archive_start_time() == datetime(2019, 5, 28, 20, 30, 45, tzinfo=timezone.utc)
    assert not dlg._calendar_popup.isVisible()


def test_no_day_after_today_can_be_picked():
    assert _AvailabilityCalendar().maximumDate() == QDate.currentDate()


def _render_cell(calendar, day):
    image = QImage(44, 34, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    calendar.paintCell(painter, QRect(0, 0, 44, 34), day)
    painter.end()
    return image


def test_both_calendars_shade_days_with_data_and_show_the_selection():
    dlg = LaunchDialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)]))
    dlg._cal_btn.click()
    target = QDate(2024, 4, 27)
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        calendar.setCurrentPage(2024, 4)
        calendar.setSelectedDate(target.addDays(-1))
        cell = _render_cell(calendar, target)
        assert cell.pixelColor(8, 7).name() == P.AVAILABLE.lower()
        assert cell.pixelColor(0, 0).name() == P.BG_SUNKEN_3.lower()
        assert any(cell.pixelColor(x, y).red() > 220 for x in range(10, 34) for y in range(8, 26))  # the number
        assert _render_cell(calendar, target.addDays(-2)).pixelColor(8, 7).name() == P.BG_SUNKEN_3.lower()
        calendar.setSelectedDate(target)
        assert _render_cell(calendar, target).pixelColor(8, 7).name() == P.ACCENT.lower()


# ── year grid ─────────────────────────────────────────────
def test_year_grid_shows_a_dozen_years_around_the_shown_one(monkeypatch):
    _freeze_today(monkeypatch, 2030)
    popup = _YearGridPopup(2022, parent=LaunchDialog())
    assert popup._range_lbl.text() == "2017 – 2028"
    assert [b.text() for b in popup._buttons] == [str(y) for y in range(2017, 2029)]
    assert [b.text() for b in popup._buttons if b.property("selected")] == ["2022"]


def test_year_grid_never_pages_past_this_year(monkeypatch):
    _freeze_today(monkeypatch, 2022)
    popup = _YearGridPopup(2022, parent=LaunchDialog())
    assert popup._range_lbl.text() == "2011 – 2022"
    assert popup._next_btn.isEnabled() is False
    popup._shift(_YearGridPopup._COUNT)
    assert popup._range_lbl.text() == "2011 – 2022"
    popup._shift(-_YearGridPopup._COUNT)
    assert popup._range_lbl.text() == "1999 – 2010"


def test_picking_a_year_emits_it_and_closes(monkeypatch):
    _freeze_today(monkeypatch, 2030)
    popup = _YearGridPopup(2022, parent=LaunchDialog())
    picked = []
    popup.yearPicked.connect(picked.append)
    popup.show()
    popup._pick(3)
    assert picked == [2020] and not popup.isVisible()


def test_one_click_on_the_year_button_opens_the_grid_and_moves_the_calendar():
    dlg = LaunchDialog()
    dlg._cal_btn.click()
    calendar = dlg._calendar_popup
    calendar.setCurrentPage(2023, 6)
    year_btn = calendar.findChild(QToolButton, "qt_calendar_yearbutton")
    spin = calendar.findChild(QSpinBox, "qt_calendar_yearedit")
    assert spin.isReadOnly() and spin.focusPolicy() == Qt.FocusPolicy.NoFocus
    QTest.mouseClick(year_btn, Qt.MouseButton.LeftButton)
    grid = dlg._year_grid_popup
    assert grid.isVisible() and grid._current_year == 2023
    assert year_btn.isVisibleTo(calendar)            # Qt's spin box wasn't revealed
    grid.yearPicked.emit(2019)
    assert (calendar.yearShown(), calendar.monthShown()) == (2019, 6)


def test_the_year_grid_opens_from_the_keyboard_too():
    dlg = LaunchDialog()
    dlg._cal_btn.click()
    year_btn = dlg._calendar_popup.findChild(QToolButton, "qt_calendar_yearbutton")
    QTest.keyClick(year_btn, Qt.Key.Key_Space)
    assert dlg._year_grid_popup.isVisible()


def test_the_date_fields_own_calendar_has_the_year_grid_too():
    dlg = LaunchDialog()
    calendar = dlg._archive_dt_edit.calendarWidget()
    year_btn = calendar.findChild(QToolButton, "qt_calendar_yearbutton")
    QTest.keyClick(year_btn, Qt.Key.Key_Space)
    assert dlg._year_grid_popup is not None


def test_known_dates_mark_both_calendars_and_the_year_grid():
    dlg = LaunchDialog()
    dates = {date(2024, 4, 27), date(2015, 5, 15)}
    _apply(dlg, _snapshot(dates))
    dlg._cal_btn.click()
    for calendar in (dlg._archive_dt_edit.calendarWidget(), dlg._calendar_popup):
        assert calendar._known_dates == dates
    year_btn = dlg._calendar_popup.findChild(QToolButton, "qt_calendar_yearbutton")
    dlg._open_year_grid(dlg._calendar_popup, year_btn)
    assert dlg._year_grid_popup._known_years == {2015, 2024}


# ── the background scan ───────────────────────────────────────
def test_archive_mode_starts_the_scan_and_date_changes_restart_it_once(monkeypatch):
    dlg = LaunchDialog()
    calls, canceled = [], []
    monkeypatch.setattr(dlg._availability, "request", lambda *args: calls.append(args))
    monkeypatch.setattr(dlg._availability, "cancel", lambda: canceled.append(True))
    dlg._select_mode("archive")
    dlg.show()
    assert dlg._availability_timer.isActive()
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 27))
    dlg._archive_dt_edit.setDate(QDate(2025, 5, 1))
    generation = dlg._availability_generation
    QTest.qWait(350)
    assert calls == [(generation,)]
    assert len(canceled) >= 3
    dlg._select_mode("viewer")
    assert not dlg._availability_timer.isActive()


def test_live_modes_dont_scan(monkeypatch):
    dlg = LaunchDialog()
    calls = []
    monkeypatch.setattr(dlg._availability, "request", lambda *args: calls.append(args))
    dlg._select_mode("viewer")
    dlg.show()
    QTest.qWait(300)
    assert calls == [] and not dlg._archive_scan_progress.isVisibleTo(dlg)


def test_a_late_result_is_dropped_after_a_date_change_or_close():
    dlg = LaunchDialog()
    dlg._select_mode("archive")
    old = dlg._availability_generation
    dlg._archive_dt_edit.setDate(QDate(2025, 5, 1))
    dlg._on_availability_updated(old, _snapshot([date(2024, 4, 27)]))
    assert dlg._availability_snapshot is None
    dlg.show()
    generation = dlg._availability_generation
    dlg.close()
    dlg._on_availability_updated(generation, _snapshot([date(2024, 4, 27)]))
    assert dlg._availability_snapshot is None
    assert not dlg._availability_timer.isActive()


def test_failed_checks_are_logged_once_and_dont_hide_found_dates(caplog):
    dlg = LaunchDialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)], failed=True))
    _apply(dlg, _snapshot([date(2024, 4, 27)], failed=True))
    assert caplog.text.count("server timed out") == 1
    assert dlg._archive_dt_edit.calendarWidget()._known_dates == {date(2024, 4, 27)}


def test_the_progress_ring_shows_only_while_catalogs_are_left():
    dlg = LaunchDialog()
    _apply(dlg, _snapshot([date(2024, 4, 27)], partial=True))
    ring, snapshot = dlg._archive_scan_progress, dlg._availability_snapshot
    assert ring.isVisibleTo(dlg)
    assert (ring._value, ring._maximum) == (snapshot.checked, snapshot.total)
    assert "catalogs" in ring.toolTip()
    _apply(dlg, _snapshot())
    assert not ring.isVisibleTo(dlg)


def test_closing_the_window_cancels_the_scan_and_the_finder(monkeypatch):
    dlg = LaunchDialog()
    canceled = []
    monkeypatch.setattr(dlg._availability, "cancel", lambda: canceled.append(True))
    dlg._select_mode("archive")
    dlg.show()
    dlg._find_dates_btn.click()
    canceled.clear()
    dlg.hide()
    assert canceled and not dlg._date_finder.isVisible()


def test_the_worker_closes_with_the_window(monkeypatch):
    from PyQt6.QtCore import QEvent
    closed = []
    monkeypatch.setattr(AvailabilityWorker, "close", lambda self: closed.append(self))
    dlg = LaunchDialog()
    worker = dlg._availability
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert closed == [worker]


# ── Open case… ─────────────────────────────────────────────
def _package(tmp_path, **case):
    base = {"session_date": "2024-04-27", "archive_time": "2024-04-27T20:15:00Z", "radar_station": "KTLX"}
    base.update(case)
    return case_package.build(tmp_path / "case.zip", case=base, tracks=[], sources=[])


def test_open_case_moves_to_its_date_and_launch_opens_it(tmp_path, monkeypatch):
    path = _package(tmp_path)
    monkeypatch.setattr(dialog_module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    dlg = LaunchDialog()
    dlg._select_mode("archive")
    dlg._open_case_btn.click()
    assert dlg.archive_start_time() == datetime(2024, 4, 27, 20, 15, tzinfo=timezone.utc)
    assert dlg.case_package() == Path(path)
    assert "KTLX" in dlg._case_label.text() and dlg._case_label.isVisibleTo(dlg)
    dlg._archive_dt_edit.setTime(QTime(22, 0))          # the same day keeps it
    assert dlg.case_package() == Path(path)
    dlg._select_mode("viewer")
    assert dlg.case_package() is None                   # ARCHIVE only
    dlg._select_mode("archive")
    dlg._archive_dt_edit.setDate(QDate(2024, 4, 28))    # another day drops it
    assert dlg.case_package() is None and not dlg._case_label.isVisibleTo(dlg)


def test_clear_drops_the_case(tmp_path):
    dlg = LaunchDialog()
    dlg._select_mode("archive")
    dlg._choose_case_package(_package(tmp_path))
    dlg._case_clear_btn.click()
    assert dlg.case_package() is None


def test_a_file_that_isnt_a_package_is_refused(tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    dlg = LaunchDialog()
    dlg._select_mode("archive")
    before = dlg._archive_dt_edit.dateTime()
    dlg._choose_case_package(bad)
    assert shown and "bad.zip" in shown[0]
    assert dlg.case_package() is None and dlg._archive_dt_edit.dateTime() == before


# ── Change Day presets ─────────────────────────────────────
def test_change_day_reopens_in_archive_on_the_last_date():
    when = datetime(2024, 4, 27, 21, 5, tzinfo=timezone.utc)
    dlg = LaunchDialog(mode="archive", archive_time=when)
    assert dlg.archive() and dlg.archive_start_time() == when


def test_the_admin_passphrase_given_earlier_in_the_run_isnt_asked_again(monkeypatch):
    accepted = []
    monkeypatch.setattr(LaunchDialog, "_do_accept", lambda self: accepted.append(True))
    monkeypatch.setattr(dialog_module._config, "missing_mqtt_cert_files", lambda: [])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail("asked for a passphrase"))
    dlg = LaunchDialog(mode="archive", verified_modes={"admin"})
    dlg.show()
    assert dlg._admin_check.isChecked() and not dlg._admin_passphrase_row.isVisibleTo(dlg)
    dlg._on_launch()
    assert accepted == [True]


@pytest.mark.parametrize("mode", ["vehicle", "monitor", "archive"])
def test_modes_launch_without_a_passphrase_when_the_certificates_exist(monkeypatch, mode):
    accepted = []
    monkeypatch.setattr(LaunchDialog, "_do_accept", lambda self: accepted.append(True))
    monkeypatch.setattr(dialog_module._config, "missing_mqtt_cert_files", lambda: [])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(a[1]))
    dlg = LaunchDialog()
    dlg._select_mode(mode)
    dlg._vid_input.setText("TEST1")
    dlg._on_launch()
    assert accepted == [True]


@pytest.mark.parametrize("mode", ["vehicle", "monitor", "archive"])
def test_modes_wont_launch_without_the_certificates(monkeypatch, mode):
    warned = []
    monkeypatch.setattr(LaunchDialog, "_do_accept", lambda self: pytest.fail("launched"))
    monkeypatch.setattr(dialog_module._config, "missing_mqtt_cert_files", lambda: ["/x/storm.pem"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    dlg = LaunchDialog()
    dlg._select_mode(mode)
    dlg._vid_input.setText("TEST1")
    dlg._on_launch()
    assert warned == ["Missing Certificate Files"]


def test_viewer_launches_without_the_certificates(monkeypatch):
    accepted = []
    monkeypatch.setattr(LaunchDialog, "_do_accept", lambda self: accepted.append(True))
    monkeypatch.setattr(dialog_module._config, "missing_mqtt_cert_files", lambda: ["/x/storm.pem"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(a[1]))
    dlg = LaunchDialog()
    dlg._select_mode("viewer")
    dlg._on_launch()
    assert accepted == [True]


def test_admin_mode_still_asks_for_its_passphrase(monkeypatch):
    warned = []
    monkeypatch.setattr(dialog_module._config, "missing_mqtt_cert_files", lambda: [])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    dlg = LaunchDialog()
    dlg._select_mode("monitor")
    dlg.show()
    assert not dlg._admin_passphrase_row.isVisibleTo(dlg)
    dlg._admin_check.setChecked(True)
    assert dlg._admin_passphrase_row.isVisibleTo(dlg)
    dlg._on_launch()
    assert warned == ["Incorrect Admin Passphrase"]


def test_the_archive_section_fits_the_form_at_its_minimum_width():
    dlg = LaunchDialog(mode="archive")
    dlg.show()
    QTest.qWait(30)
    for w in (dlg._find_dates_btn, dlg._open_case_btn, dlg._cal_btn):
        assert w.isVisibleTo(dlg)
        right = w.mapTo(dlg._form, w.rect().topRight()).x()
        assert right <= dlg._form.width()
