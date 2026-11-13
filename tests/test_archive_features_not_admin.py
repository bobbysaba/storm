"""Archive mode's one-second observations are for every archive user, not
only admin mode (which turns on just the live mesoanalysis overlays)."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

import feature_flags
import runtime_flags
from archive.fetchers import vehicle_obs_archive_fetcher as vof
from ui.app.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Host(QObject):
    _start_archive_vehicle_obs = MainWindow._start_archive_vehicle_obs
    _on_archive_vehicle_obs_loaded = MainWindow._on_archive_vehicle_obs_loaded
    _update_archive_session_end = MainWindow._update_archive_session_end


def _host(vehicles):
    host = Host()
    host._archive_time = datetime(2019, 6, 1, tzinfo=timezone.utc)
    host._archive_vehicle_obs_started = False
    host._archive_vehicle_obs_loaded = False
    host._radar_station_awaiting_dense_obs = False
    host._archive_mqtt = SimpleNamespace(vehicle_metadata=lambda: dict(vehicles))
    host.statuses = []
    host._archive_controls = SimpleNamespace(
        set_obs_status=lambda text, active=False: host.statuses.append(text),
        set_precision_mode=lambda on: None)
    host._time_ctrl = SimpleNamespace(time_changed=SimpleNamespace(connect=lambda slot: None),
                                      current_time=host._archive_time)
    host._on_archive_dense_vehicle_position = lambda obs: None
    host._on_archive_vehicle_obs_error = lambda msg: None
    host._layout_overlays = lambda: None
    return host


def test_one_second_data_starts_without_admin_mode(app, monkeypatch):
    monkeypatch.setattr(runtime_flags.FLAGS, "admin_mode", False)
    loads = []
    monkeypatch.setattr(vof.ArchiveVehicleObsFetcher, "load", lambda self, v: loads.append(v))
    host = _host({"p1": "probe"})

    host._start_archive_vehicle_obs()

    assert loads == [{"p1": "probe"}]


def test_one_second_data_starts_even_with_no_mqtt_history(app, monkeypatch):
    monkeypatch.setattr(runtime_flags.FLAGS, "admin_mode", False)
    loads = []
    monkeypatch.setattr(vof.ArchiveVehicleObsFetcher, "load", lambda self, v: loads.append(v))
    host = _host({})

    host._start_archive_vehicle_obs()
    host._archive_vehicle_obs.roster_size = 3
    host._on_archive_vehicle_obs_loaded({"p1", "p2"})

    assert loads == [{}]
    assert host.statuses == ["OBS: catalog partial 2/3"]


def test_status_when_nothing_was_published_for_the_date(app, monkeypatch):
    monkeypatch.setattr(vof.ArchiveVehicleObsFetcher, "load", lambda self, v: None)
    host = _host({})
    host._start_archive_vehicle_obs()

    host._on_archive_vehicle_obs_loaded(set())

    assert host.statuses == ["OBS: no data for this date"]


def test_only_mesoanalysis_needs_admin_mode():
    assert [k for k, f in feature_flags.FEATURES.items() if f.admin_only] == ["mesoanalysis"]
