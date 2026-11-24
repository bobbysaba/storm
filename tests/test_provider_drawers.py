"""A provider's own ARCHIVE drawer (archive_drawer; NSSL's, for every team):
the hook, the drawer's rows and greying, and which vehicles it shows."""
import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

import settings
from settings import code_providers
from ui.app.main_window_provider_drawers import MainWindowProviderDrawersMixin
from ui.controls.provider_drawer_controls import ProviderDrawerControls

T = datetime(2023, 6, 9, 20, tzinfo=timezone.utc)
ITEMS = (("vehicles", "Vehicles", "v"), ("lidar", "Lidar", "l"), ("sounding", "Sounding", "s"),
         ("uas", "UAS", "u"), ("retrievals", "Retrievals", "r"))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _nssl(**options):
    path = settings.PROJECT_DIR / "providers" / "nssl"
    return settings.CodeProvider("nssl", path, tuple(options.items()), builtin=not options)


def test_every_team_gets_nssls_drawer_and_no_shared_slot(app, monkeypatch):
    s = dataclasses.replace(settings.SETTINGS, code_providers=(_nssl(),))
    [(provider, drawer)] = code_providers.every_hook(s, "archive_drawer", archive_time=T)
    assert (drawer.label, drawer.team, [k for k, _, _ in drawer.items]) == \
        ("NSSL", False, ["vehicles", "lidar", "sounding", "retrievals"])
    assert drawer.lidar.track_vehicle == "dltruck" and drawer.soundings.track_vehicle == "dltruck"
    # NSSL no longer takes the shared LIDAR / VAD / SOUNDING slots
    for hook in ("archive_lidar", "archive_wind_profiles", "archive_surface_playback", "archive_sounding_fetcher"):
        assert code_providers.first_hook(s, hook) is None
    team = dataclasses.replace(settings.SETTINGS, code_providers=(_nssl(team=True),))
    [(_, drawer)] = code_providers.every_hook(team, "archive_drawer", archive_time=T)
    assert drawer.team


def test_availability_leaves_unpublished_indexes_unknown(monkeypatch):
    from providers.nssl import indexes
    from providers.nssl.archive import drawer
    monkeypatch.setattr(indexes.FOFS, "get", lambda: None)
    monkeypatch.setattr(indexes, "SONDES", indexes.PublishedIndex("sondes", ""))
    monkeypatch.setattr(indexes, "TROPOE", indexes.PublishedIndex("tropoe", ""))
    found = drawer.availability(T)
    assert found == {"lidar": None, "vehicles": None, "sounding": None, "retrievals": None}


def test_rows_grey_out_without_data(app):
    controls = ProviderDrawerControls(ITEMS)
    assert [b.text() for b in controls.buttons.values()] == ["VEHICLES", "LIDAR", "SOUNDING", "UAS", "RETRIEVALS"]
    assert controls.buttons["vehicles"].isCheckable() and not controls.buttons["sounding"].isCheckable()
    controls.set_availability({"uas": False, "sounding": None})
    assert not controls.buttons["uas"].isEnabled() and controls.buttons["sounding"].isEnabled()
    assert controls.buttons["uas"].toolTip() == "u: none for this date"
    assert controls.any_available()
    controls.set_availability({k: False for k, _, _ in ITEMS})
    assert not controls.any_available()


def test_uas_sites_are_listed_and_picked(app):
    controls = ProviderDrawerControls(ITEMS)
    picked = []
    controls.uas_selected.connect(picked.append)
    controls.set_uas_sets({"B": "b", "A": "a"})
    assert [controls.uas_combo.itemText(i) for i in range(2)] == ["A", "B"] and picked == ["a"]
    assert not controls.uas_combo.isHidden()


def _host(*entries):
    host = SimpleNamespace(_archive_drawers=list(entries))
    host._vehicle_hidden_by_drawer = MainWindowProviderDrawersMixin._vehicle_hidden_by_drawer.__get__(host)
    host._tagged = MainWindowProviderDrawersMixin._tagged.__get__(host)
    return host


def test_a_guests_off_drawer_hides_only_its_tagged_vehicles():
    guest = SimpleNamespace(team=False, tag="NSSL", vehicles_on=False)
    host = _host(guest)
    assert host._vehicle_hidden_by_drawer("NSSL probe1")
    assert not host._vehicle_hidden_by_drawer("wx1")
    guest.vehicles_on = True
    assert not host._vehicle_hidden_by_drawer("NSSL probe1")


def test_the_teams_off_drawer_hides_every_archive_vehicle():
    host = _host(SimpleNamespace(team=True, tag="NSSL", vehicles_on=False))
    assert host._vehicle_hidden_by_drawer("probe1") and host._vehicle_hidden_by_drawer("CLAMPS1")


def test_a_guests_vehicles_are_tagged_copies():
    from core.observation import Observation
    entry = SimpleNamespace(tag="NSSL")
    obs = Observation("probe1", 35.0, -97.0, T)
    tagged = _host()._tagged(entry, obs)
    assert tagged.vehicle_id == "NSSL probe1" and obs.vehicle_id == "probe1"
    assert (tagged.lat, tagged.lon, tagged.timestamp) == (35.0, -97.0, T)
