"""Archive window: timers and worker signals stop once closing begins; radar auto-pick."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher
from ui.app.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Host(QObject):
    _later = MainWindow._later
    _silence_archive_workers = MainWindow._silence_archive_workers
    _try_auto_select_radar_station = MainWindow._try_auto_select_radar_station
    _finish_radar_station_selection = MainWindow._finish_radar_station_selection
    _apply_radar_station = MainWindow._apply_radar_station
    _radar_wait_for_vehicles_timed_out = MainWindow._radar_wait_for_vehicles_timed_out
    _on_archive_vehicle_obs_loaded = MainWindow._on_archive_vehicle_obs_loaded
    _update_archive_session_end = MainWindow._update_archive_session_end


class Worker(QObject):
    done = pyqtSignal(str)


def test_later_runs_its_callback(app):
    host, calls = Host(), []
    host._later(0, lambda: calls.append(1))
    QTest.qWait(20)
    assert calls == [1]


def test_later_skips_its_callback_once_closing(app):
    host, calls = Host(), []
    host._later(10, lambda: calls.append(1))
    host._closing = True
    QTest.qWait(40)
    assert calls == []


def test_close_silences_archive_workers_only(app):
    host = Host()
    host._archive_mqtt = Worker()
    host._archive_controls = QWidget()      # a widget, not a worker
    host._other = Worker()
    got = []
    for worker in (host._archive_mqtt, host._other):
        worker.done.connect(got.append)

    host._silence_archive_workers()
    host._archive_mqtt.done.emit("archive")
    host._other.done.emit("other")

    assert got == ["other"]
    assert not host._archive_controls.signalsBlocked()


def test_auto_pick_uses_the_vehicles_at_the_session_start(app):
    host = Host()
    host._archive_time = datetime(2026, 5, 17, 21, tzinfo=timezone.utc)
    asked, started = [], []
    host._archive_mqtt = SimpleNamespace(
        vehicle_positions_near=lambda t: asked.append(t) or [("WX1", 41.0, -98.5)])
    host._nearest_nexrad = lambda lat, lon, archive=False: "KUEX" if (lat, lon) == (41.0, -98.5) else "KTLX"
    host._archive_session = SimpleNamespace(radar_station=None)
    host._start_archive_radar = started.append

    host._try_auto_select_radar_station()

    assert asked == [host._archive_time]
    assert started == ["KUEX"] and host._archive_session.radar_station == "KUEX"


def _utc(h, m=0):
    return datetime(2026, 5, 17, h, m, tzinfo=timezone.utc)


def test_one_second_positions_near_pick_the_closest_fix(app):
    fetcher = ArchiveVehicleObsFetcher(session_date=_utc(0))
    times = [_utc(15), _utc(20, 58), _utc(23)]
    fetcher._observations = {"WX1": [SimpleNamespace(lat=35.2 + i, lon=-97.4, timestamp=t)
                                     for i, t in enumerate(times)]}
    fetcher._timestamps = {"WX1": times}

    assert fetcher.vehicle_positions_near(_utc(21)) == [("WX1", 36.2, -97.4)]
    assert fetcher.vehicle_positions_near(_utc(1)) == [("WX1", 35.2, -97.4)]
    assert fetcher.vehicle_positions_near(_utc(23, 59)) == [("WX1", 37.2, -97.4)]


def _picking_host(mqtt_positions=(), dense=None, dense_loaded=False):
    host = Host()
    host._archive_time = _utc(21)
    host._archive_mqtt = SimpleNamespace(vehicle_positions_near=lambda t: list(mqtt_positions))
    host._archive_vehicle_obs = dense
    host._archive_vehicle_obs_loaded = dense_loaded
    host._archive_vehicle_obs_from_catalog = True
    host._radar_station_awaiting_dense_obs = False
    host._radar_station_provisional = False
    host._radar_station_user_picked = False
    host._archive_radar = None
    host._nearest_nexrad = lambda lat, lon, archive=False: "KUEX" if lat > 40 else "KTLX"
    host._archive_session = SimpleNamespace(radar_station=None)
    host.started = []

    def start(site):
        host.started.append(site)
        host._archive_radar = SimpleNamespace(station=site)
    host._start_archive_radar = start
    return host


def _loaded_dense(positions):
    return SimpleNamespace(vehicle_positions_near=lambda t: positions, roster_size=1,
                           on_time_changed=lambda t: None)


def test_auto_pick_waits_for_one_second_positions_when_mqtt_has_none(app, monkeypatch):
    import config
    monkeypatch.setattr(config, "HOME_LAT", 35.2)
    host = _picking_host(dense=_loaded_dense([]))   # still loading

    host._try_auto_select_radar_station()
    assert host.started == [] and host._radar_station_awaiting_dense_obs

    host._archive_vehicle_obs = _loaded_dense([("p1", 41.0, -98.5)])
    host._archive_controls = SimpleNamespace(set_obs_status=lambda *a, **k: None,
                                             set_precision_mode=lambda on: None)
    host._time_ctrl = SimpleNamespace(current_time=_utc(21))
    host._layout_overlays = lambda: None
    host._on_archive_vehicle_obs_loaded({"p1"})

    assert host.started == ["KUEX"] and not host._radar_station_awaiting_dense_obs


def test_slow_one_second_load_starts_radar_from_home_then_moves(app, monkeypatch):
    import config
    monkeypatch.setattr(config, "HOME_LAT", 35.2)
    host = _picking_host(dense=_loaded_dense([]))
    host._try_auto_select_radar_station()

    host._radar_wait_for_vehicles_timed_out()
    assert host.started == ["KTLX"] and host._radar_station_provisional

    host._archive_vehicle_obs_loaded = True
    host._archive_vehicle_obs = _loaded_dense([("p1", 41.0, -98.5)])
    host._try_auto_select_radar_station()
    assert host.started == ["KTLX", "KUEX"]


def test_provisional_radar_stays_when_the_user_picked_one(app, monkeypatch):
    import config
    monkeypatch.setattr(config, "HOME_LAT", 35.2)
    host = _picking_host(dense=_loaded_dense([]))
    host._try_auto_select_radar_station()
    host._radar_wait_for_vehicles_timed_out()
    host._radar_station_user_picked = True

    host._archive_vehicle_obs_loaded = True
    host._archive_vehicle_obs = _loaded_dense([("p1", 41.0, -98.5)])
    host._try_auto_select_radar_station()

    assert host.started == ["KTLX"]


def test_no_positions_anywhere_falls_back_to_home(app, monkeypatch):
    import config
    monkeypatch.setattr(config, "HOME_LAT", 35.2)
    host = _picking_host(dense=_loaded_dense([]), dense_loaded=True)

    host._try_auto_select_radar_station()

    assert host.started == ["KTLX"]
