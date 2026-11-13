"""Browse available cases: matching rules and the round trip to the launch window."""
import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QCoreApplication, QSettings, Qt
from PyQt6.QtWidgets import QApplication

from archive.catalog import ALL_PLATFORMS, AvailabilitySnapshot, PlatformAvailability
from ui.launch.availability import AvailabilityWorker
from ui.launch.date_finder import FindDatesDialog, dates_by_site, match_dates, sites_on
from ui.launch.dialog import LaunchDialog

A, B, C = date(2024, 5, 23), date(2024, 6, 2), date(2019, 5, 17)
BY_SITE = {"Truck": frozenset({A, B}), "CLAMPS 1": frozenset({A, C}), "Probe 1": frozenset({B})}


@pytest.fixture(autouse=True)
def qapp(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    old_org, old_format = QCoreApplication.organizationName(), QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QCoreApplication.setOrganizationName("STORM-test")
    monkeypatch.setattr(LaunchDialog, "_start_update_check", lambda self: None)
    monkeypatch.setattr(AvailabilityWorker, "request", lambda *args: None)
    yield app
    QCoreApplication.setOrganizationName(old_org)
    QSettings.setDefaultFormat(old_format)


def test_no_instruments_chosen_matches_any_site():
    assert match_dates(BY_SITE) == {A, B, C}


def test_all_of_requires_every_chosen_site():
    assert match_dates(BY_SITE, chosen=("Truck", "CLAMPS 1")) == {A}
    assert match_dates(BY_SITE, chosen=("CLAMPS 1", "Probe 1")) == set()


def test_any_of_takes_the_union():
    assert match_dates(BY_SITE, chosen=("CLAMPS 1", "Probe 1"), require_all=False) == {A, B, C}


def test_year_month_and_campaign_filters_combine():
    assert match_dates(BY_SITE, years=(2020, 2026)) == {A, B}
    assert match_dates(BY_SITE, months={5}) == {A, C}
    assert match_dates(BY_SITE, months={5}, campaign_years=(2019,)) == {C}


def test_empty_index_matches_nothing():
    assert match_dates({}) == set()
    assert match_dates({}, chosen=("Truck",)) == set()


def test_sites_on_lists_every_site_with_data_that_day():
    assert sites_on(A, BY_SITE) == ["CLAMPS 1", "Truck"]


def test_dates_by_site_unions_a_sites_products():
    plats = [SimpleNamespace(platform_id="x"), SimpleNamespace(platform_id="y")]
    snap = SimpleNamespace(platforms={"x": SimpleNamespace(dates={A}), "y": SimpleNamespace(dates={B})})
    assert dates_by_site(snap, {"Truck": plats, "Gone": [SimpleNamespace(platform_id="z")]}) == {
        "Truck": {A, B}, "Gone": set()}
    assert dates_by_site(None, {"Truck": plats}) == {}


def _snapshot(per_platform, checked=None):
    results = {p.platform_id: PlatformAvailability(frozenset(per_platform.get(p.platform_id, ())), 1, 1, ())
               for p in ALL_PLATFORMS}
    return AvailabilitySnapshot(results, len(results) if checked is None else checked, len(results))


def _check(dialog, site):
    for i in range(dialog._instruments.count()):
        item = dialog._instruments.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == site:
            item.setCheckState(Qt.CheckState.Checked)
            return
    raise AssertionError(site)


def test_dialog_filters_and_lists_matching_dates():
    snap = _snapshot({"FOFS-dltruck": {A, B}, "FOFS-probe1": {B, C}})
    dlg = FindDatesDialog()
    dlg.set_snapshot(snap)
    assert dlg.matching_dates() == {A, B, C}
    _check(dlg, "LiDAR Truck")
    _check(dlg, "Probe 1")
    assert dlg.matching_dates() == {B}
    assert dlg._days.count() == 1 and "LiDAR Truck" in dlg._days.item(0).text()
    dlg._mode_any.setChecked(True)
    assert dlg.matching_dates() == {A, B, C}
    dlg._month_btns[4].setChecked(True)                       # May
    assert dlg.matching_dates() == {A, C}
    dlg._on_month_clicked((2024, 5))
    assert [dlg._days.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg._days.count())] == [A]


def test_dates_are_listed_newest_first_with_counts_per_vehicle():
    dlg = FindDatesDialog()
    dlg.set_snapshot(_snapshot({"FOFS-dltruck": {A, B, C}, "FOFS-probe1": {B}}))
    shown = [dlg._days.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg._days.count())]
    assert shown == [B, A, C]
    texts = {dlg._instruments.item(i).data(Qt.ItemDataRole.UserRole): dlg._instruments.item(i).text()
             for i in range(dlg._instruments.count())}
    assert texts["LiDAR Truck"].endswith("3") and texts["Probe 1"].endswith("1")
    assert dlg._grid._years == [2024, 2019]


def test_status_says_while_the_catalogs_are_still_being_checked():
    dlg = FindDatesDialog()
    assert "Checking" in dlg._status.text()
    dlg.set_snapshot(_snapshot({"FOFS-dltruck": {A}}, checked=3))
    assert "still checking" in dlg._status.text()
    dlg.set_snapshot(_snapshot({"FOFS-dltruck": {A}}))
    assert dlg._status.text().startswith("1 matching dates")


def test_campaign_sets_the_year_range():
    dlg = FindDatesDialog()
    dlg._campaign.setCurrentIndex(dlg._campaign.findData("TORUS"))
    assert dlg._year_range() == (2019, 2023)


def test_choosing_a_date_sets_the_launch_date_and_keeps_the_time():
    launch = LaunchDialog()
    launch._select_mode("archive")
    launch._on_availability_updated(launch._availability_generation, _snapshot({"FOFS-dltruck": {A}}))
    time_before = launch._archive_dt_edit.time()
    launch._find_dates_btn.click()
    finder = launch._date_finder
    assert finder.matching_dates() == {A}
    finder._days.setCurrentRow(0)
    finder._use_btn.click()
    assert launch._archive_dt_edit.date().toPyDate() == A
    assert launch._archive_dt_edit.time() == time_before
    assert not finder.isVisible()


def test_the_finder_follows_the_scan_while_open():
    launch = LaunchDialog()
    launch._select_mode("archive")
    launch._find_dates_btn.click()
    finder = launch._date_finder
    assert finder.matching_dates() == set()
    launch._on_availability_updated(launch._availability_generation, _snapshot({"FOFS-probe1": {C}}))
    assert finder.matching_dates() == {C}
