"""Archive ASOS replay (archive/fetchers/asos_archive_fetcher.py), ported
from the storm_ty_edits fork's tests."""
import threading
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import pytest
from PyQt6.QtWidgets import QApplication

from archive.fetchers import asos_archive_fetcher as aaf
from archive.fetchers.asos_archive_fetcher import (
    ArchiveAsosFetcher,
    _batch_url,
    fetch_asos_history,
    parse_asos_history_csv,
    stations_in_bbox,
)

_ROSTER = {
    "OUN": {"lat": 35.24, "lon": -97.47, "name": "Norman"},
    "OKC": {"lat": 35.39, "lon": -97.60, "name": "Oklahoma City"},
}

_CSV = (
    "station,valid,tmpf,dwpf,relh,drct,sknt,alti,mslp\n"
    "OUN,2013-05-31 20:15,68.00,50.00,M,170.00,10.00,29.67,M\n"
    "OUN,2013-05-31 20:35,70.00,M,M,160.00,M,29.68,M\n"
    "OKC,2013-05-31 20:52,75.00,65.00,M,170.00,13.00,M,1003.10\n"
    "XXX,2013-05-31 20:52,75.00,65.00,M,170.00,13.00,M,1003.10\n"
)


def _qapp():
    return QApplication.instance() or QApplication(["storm"])


def _utc(hour, minute=0):
    return datetime(2013, 5, 31, hour, minute, tzinfo=timezone.utc)


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return _CSV.encode()


# ── parsing ──────────────────────────────────────────────────

def test_parse_converts_units_like_live_asos():
    oun = parse_asos_history_csv(_CSV, _ROSTER)["OUN"]
    assert len(oun) == 2
    assert oun[0].temperature_c == pytest.approx((68.0 - 32.0) * 5.0 / 9.0)
    assert oun[0].dewpoint_c == pytest.approx((50.0 - 32.0) * 5.0 / 9.0)
    assert oun[0].wind_speed_ms == pytest.approx(10.0 * 0.514444)
    assert oun[0].wind_dir_deg == 170.0
    assert oun[0].vehicle_id == "surface:asos:OUN"
    assert (oun[0].lat, oun[0].lon) == (35.24, -97.47)


def test_parse_prefers_mslp_then_altimeter():
    result = parse_asos_history_csv(_CSV, _ROSTER)
    assert result["OKC"][0].pressure_mb == pytest.approx(1003.10)
    assert result["OUN"][0].pressure_mb == pytest.approx(29.67 * 33.8639)


def test_parse_missing_marker_becomes_none():
    second = parse_asos_history_csv(_CSV, _ROSTER)["OUN"][1]
    assert second.dewpoint_c is None
    assert second.wind_speed_ms is None


def test_parse_drops_stations_missing_from_the_roster():
    assert "XXX" not in parse_asos_history_csv(_CSV, _ROSTER)


def test_parse_sorts_each_station_by_time():
    unordered = (
        "station,valid,tmpf,dwpf,relh,drct,sknt,alti,mslp\n"
        "OUN,2013-05-31 21:00,70.00,M,M,M,M,M,M\n"
        "OUN,2013-05-31 20:00,68.00,M,M,M,M,M,M\n"
    )
    result = parse_asos_history_csv(unordered, _ROSTER)
    assert [o.timestamp.hour for o in result["OUN"]] == [20, 21]


def test_stations_in_bbox():
    assert stations_in_bbox(_ROSTER, -97.5, 35.0, -97.4, 35.3) == ["OUN"]
    assert stations_in_bbox(_ROSTER, -98, 35, -97, 36) == ["OKC", "OUN"]


# ── requests ─────────────────────────────────────────────────

def test_batch_url_repeats_station_and_gives_the_window():
    url = _batch_url(["OUN", "OKC"], _utc(0), datetime(2013, 6, 1, 6, tzinfo=timezone.utc))
    assert url.startswith(aaf.endpoints.IEM_ASOS_HISTORY + "?")
    assert url.count("station=OUN") == 1 and url.count("station=OKC") == 1
    assert "sts=2013-05-31T00:00:00Z" in unquote(url)
    assert "ets=2013-06-01T06:00:00Z" in unquote(url)
    assert "format=onlycomma" in url and "tz=UTC" in url


def test_fetch_covers_the_whole_session_span(monkeypatch):
    urls = []
    monkeypatch.setattr(aaf, "_urlopen_with_retry", lambda request: urls.append(request.full_url) or _Resp())
    fetch_asos_history(["OUN"], _ROSTER, _utc(21))
    assert "sts=2013-05-31T00:00:00Z" in urls[0]
    assert "ets=2013-06-01T06:00:00Z" in urls[0]       # the session runs to 06Z next morning


def test_fetch_paces_between_batches_not_per_station(monkeypatch):
    sleeps = []
    monkeypatch.setattr(aaf.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(aaf, "_urlopen_with_retry", lambda request: _Resp())
    station_ids = [f"S{i}" for i in range(120)]  # 3 batches of 50
    fetch_asos_history(station_ids, _ROSTER, _utc(0))
    assert len(sleeps) == 2  # between batches, never after the last one


def test_fetch_merges_batches_per_station(monkeypatch):
    monkeypatch.setattr(aaf.time, "sleep", lambda s: None)
    monkeypatch.setattr(aaf, "_urlopen_with_retry", lambda request: _Resp())
    result = fetch_asos_history([f"S{i}" for i in range(60)], _ROSTER, _utc(0))
    assert len(result["OUN"]) == 4        # the same CSV came back for both batches
    assert result["OUN"] == sorted(result["OUN"], key=lambda o: o.timestamp)


def test_fetch_stops_early_when_canceled(monkeypatch):
    calls = []
    monkeypatch.setattr(aaf, "_urlopen_with_retry", lambda request: calls.append(request) or _Resp())
    monkeypatch.setattr(aaf.time, "sleep", lambda s: None)
    cancel = threading.Event()
    cancel.set()
    result = fetch_asos_history([f"S{i}" for i in range(120)], _ROSTER, _utc(0), cancel=cancel)
    assert calls == [] and result == {}


def test_requests_are_recorded_for_case_packages(monkeypatch):
    kinds = []
    monkeypatch.setattr(aaf.package_sources, "read_url",
                        lambda kind, request, opener, **kw: kinds.append(kind) or _CSV.encode())
    fetch_asos_history(["OUN"], _ROSTER, _utc(0))
    assert kinds == ["asos"]


# ── replay ───────────────────────────────────────────────────

def _populated_fetcher():
    _qapp()
    fetcher = ArchiveAsosFetcher(_utc(0))
    fetcher._on_results(fetcher._generation, parse_asos_history_csv(_CSV, _ROSTER),
                        {"OUN": "Norman", "OKC": "Oklahoma City"})
    return fetcher


def test_replay_emits_only_stations_whose_report_changed():
    fetcher = _populated_fetcher()
    updates = []
    fetcher.station_updated.connect(lambda sid, name, obs: updates.append((sid, name, obs.timestamp)))

    fetcher.on_time_changed(_utc(20, 20))  # only OUN's 20:15 report is reached
    assert updates == [("OUN", "Norman", _utc(20, 15))]

    updates.clear()
    fetcher.on_time_changed(_utc(20, 40))  # OUN moves to 20:35
    assert [u[0] for u in updates] == ["OUN"]

    updates.clear()
    fetcher.on_time_changed(_utc(20, 40))  # nothing changed
    assert updates == []

    updates.clear()
    fetcher.on_time_changed(_utc(21, 0))   # OKC's 20:52 is reached too
    assert [u[0] for u in updates] == ["OKC"]


def test_replay_before_any_report_emits_nothing():
    fetcher = _populated_fetcher()
    updates = []
    fetcher.station_updated.connect(lambda *a: updates.append(a))
    fetcher.on_time_changed(_utc(0, 0))
    assert updates == []


def test_rewind_removes_stations_without_a_report_yet_and_steps_others_back():
    fetcher = _populated_fetcher()
    updates, removed, cleared = [], [], []
    fetcher.station_updated.connect(lambda sid, name, obs: updates.append((sid, obs.timestamp)))
    fetcher.station_removed.connect(removed.append)
    fetcher.stations_cleared.connect(lambda: cleared.append(True))

    fetcher.on_time_changed(_utc(21, 0))
    updates.clear()
    fetcher.on_time_changed(_utc(20, 20))   # back before OKC's first report
    assert removed == ["OKC"]
    assert updates == [("OUN", _utc(20, 15))]
    assert cleared == []                     # no wholesale clear: no flicker

    removed.clear()
    updates.clear()
    fetcher.on_time_changed(_utc(21, 0))    # forward again
    assert sorted(u[0] for u in updates) == ["OKC", "OUN"]


def test_replay_does_nothing_before_load_completes():
    _qapp()
    fetcher = ArchiveAsosFetcher(_utc(0))
    updates = []
    fetcher.station_updated.connect(lambda *a: updates.append(a))
    fetcher.on_time_changed(_utc(21, 0))
    assert updates == []


def test_results_replay_at_the_clock_already_set():
    _qapp()
    fetcher = ArchiveAsosFetcher(_utc(0))
    fetcher.on_time_changed(_utc(21, 0))      # the clock moved while loading
    updates = []
    fetcher.station_updated.connect(lambda sid, name, obs: updates.append(sid))
    fetcher._on_results(fetcher._generation, parse_asos_history_csv(_CSV, _ROSTER), {})
    assert sorted(updates) == ["OKC", "OUN"]


def test_observations_by_station_and_state():
    fetcher = _populated_fetcher()
    assert fetcher.has_stations()
    assert set(fetcher.observations_by_station()) == {"OUN", "OKC"}
    assert len(fetcher.observations_by_station()["OUN"]) == 2
    cleared = []
    fetcher.stations_cleared.connect(lambda: cleared.append(True))
    fetcher.clear()
    assert cleared == [True]
    assert not fetcher.has_stations() and fetcher.observations_by_station() == {}


# ── loading ──────────────────────────────────────────────────

def test_a_redrawn_box_drops_the_stale_load(monkeypatch):
    """The first load finishes after the second box is drawn; its results
    never replace the second box's."""
    app = _qapp()
    release_first = threading.Event()
    started_first = threading.Event()
    calls = {"n": 0}

    def fake_fetch(station_ids, roster, day, *, cancel=None):
        calls["n"] += 1
        if calls["n"] == 1:
            started_first.set()
            release_first.wait(timeout=5)
            return {"STALE": [parse_asos_history_csv(_CSV, _ROSTER)["OUN"][0]]}
        return {"FRESH": [parse_asos_history_csv(_CSV, _ROSTER)["OKC"][0]]}

    monkeypatch.setattr(aaf, "load_asos_station_roster", lambda: _ROSTER)
    monkeypatch.setattr(aaf, "fetch_asos_history", fake_fetch)
    fetcher = ArchiveAsosFetcher(_utc(0))
    finished = []
    fetcher.load_finished.connect(finished.append)

    fetcher.set_bbox(-98, 35, -97, 36)
    assert started_first.wait(timeout=5)
    fetcher.set_bbox(-98, 35, -97, 36)        # redraw before the first finishes
    deadline = time.time() + 5
    while not finished and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    release_first.set()
    deadline = time.time() + 0.5
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)

    assert finished == [1]
    assert set(fetcher.observations_by_station()) == {"FRESH"}


def test_a_box_without_stations_finishes_with_zero(monkeypatch):
    _qapp()
    monkeypatch.setattr(aaf, "load_asos_station_roster", lambda: _ROSTER)
    fetcher = ArchiveAsosFetcher(_utc(0))
    started, finished = [], []
    fetcher.load_started.connect(started.append)
    fetcher.load_finished.connect(finished.append)
    fetcher._loading = True
    fetcher._load(fetcher._generation, 0.0, 0.0, 0.01, 0.01, threading.Event())
    assert started == [] and finished == [0]
    assert not fetcher.is_loading()


def test_load_announces_and_caps_the_station_count(monkeypatch):
    _qapp()
    roster = {f"S{i:03d}": {"lat": 35.0, "lon": -97.0, "name": ""} for i in range(450)}
    seen = {}
    monkeypatch.setattr(aaf, "load_asos_station_roster", lambda: roster)
    monkeypatch.setattr(aaf, "fetch_asos_history",
                        lambda ids, roster, day, cancel=None: seen.setdefault("ids", ids) and {})
    fetcher = ArchiveAsosFetcher(_utc(0))
    started = []
    fetcher.load_started.connect(started.append)
    fetcher._load(fetcher._generation, -98, 34, -96, 36, threading.Event())
    assert started == [aaf.MAX_ASOS_STATIONS]
    assert len(seen["ids"]) == aaf.MAX_ASOS_STATIONS


def test_a_roster_failure_is_reported(monkeypatch):
    _qapp()

    def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(aaf, "load_asos_station_roster", boom)
    fetcher = ArchiveAsosFetcher(_utc(0))
    errors = []
    fetcher.error.connect(errors.append)
    fetcher._load(fetcher._generation, -98, 35, -97, 36, threading.Event())
    assert errors and "offline" in errors[0]


def test_a_stale_failure_is_not_reported(monkeypatch):
    _qapp()
    monkeypatch.setattr(aaf, "load_asos_station_roster", lambda: _ROSTER)

    def boom(*a, **kw):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(aaf, "fetch_asos_history", boom)
    fetcher = ArchiveAsosFetcher(_utc(0))
    errors = []
    fetcher.error.connect(errors.append)
    fetcher._load(fetcher._generation - 1, -98, 35, -97, 36, threading.Event())
    assert errors == []
