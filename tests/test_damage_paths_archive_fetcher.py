"""Archive damage paths (archive/fetchers/damage_paths_archive_fetcher.py),
ported from the storm_ty_edits fork's tests."""
import gzip
import json
import threading
import time
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtWidgets import QApplication

from archive.fetchers import damage_paths_archive_fetcher as dpf
from archive.fetchers.damage_paths_archive_fetcher import (
    ArchiveDamagePathsFetcher,
    _arcgis_query_url,
    _date_within_tolerance,
    _feature_intersects_bbox,
    _localize_ncei_time,
    _ncei_row_to_feature,
    _ncei_timezone_offset_hours,
    _normalize_ef,
    _normalize_feature,
    fetch_dat_damage_paths,
    fetch_ncei_storm_events_damage_paths,
)
from archive.session import session_bounds


def _qapp():
    return QApplication.instance() or QApplication(["storm"])


def _line_feature(coords, **props):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}


def _wait_for(app, cond, timeout=5.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture(autouse=True)
def _empty_ncei_cache(monkeypatch):
    monkeypatch.setattr(dpf, "_ncei_year_cache", {})


# ── normalizing ──────────────────────────────────────────────

def test_normalize_ef_recognizes_ratings():
    assert _normalize_ef({"efscale": "EF3"}) == "EF3"
    assert _normalize_ef({"efscale": "N/A"}) == "N/A"
    assert _normalize_ef({"efscale": "UNKNOWN"}) == "UNKNOWN"
    assert _normalize_ef({}) == "UNKNOWN"
    assert _normalize_ef({"efscale": None, "efnum": -99}) == "UNKNOWN"
    assert _normalize_ef({"efscale": "2"}) == "EF2"
    assert _normalize_ef({"efscale": "F4"}) == "F4"


def test_normalize_feature_assigns_label_color_and_source():
    f = _line_feature([[-98.0, 35.0], [-97.9, 35.1]], efscale="EF3", length=16.2, width=4576.0)
    props = _normalize_feature(f, source="NOAA DAT")["properties"]
    assert props["ef_label"] == "EF3"
    assert props["ef_color"] == "#e69800"
    assert props["damage_source"] == "NOAA DAT"


def test_normalize_feature_treats_dat_sentinel_as_missing():
    f = _line_feature([[-98.0, 35.0], [-97.9, 35.1]], efscale="N/A", width=-99, maxwind=-99, length=5.57)
    props = _normalize_feature(f, source="NOAA DAT")["properties"]
    assert props["width"] is None and props["maxwind"] is None
    assert props["length"] == 5.57


# ── box and date ─────────────────────────────────────────────

def test_a_line_crossing_the_box_with_both_ends_outside_is_kept():
    f = _line_feature([[-100.0, 35.0], [-95.0, 35.0]])
    assert _feature_intersects_bbox(f, -98.0, 34.5, -97.0, 35.5) is True


def test_a_line_entirely_outside_is_dropped():
    f = _line_feature([[-100.0, 40.0], [-99.0, 41.0]])
    assert _feature_intersects_bbox(f, -98.0, 34.5, -97.0, 35.5) is False


def test_multilinestrings_are_checked_too():
    f = {"type": "Feature", "properties": {},
         "geometry": {"type": "MultiLineString",
                      "coordinates": [[[-90, 40], [-89, 40]], [[-97.5, 35.0], [-97.4, 35.1]]]}}
    assert _feature_intersects_bbox(f, -98.0, 34.5, -97.0, 35.5) is True


def test_date_tolerance_includes_the_sessions_next_morning():
    _, cap = session_bounds(datetime(2024, 4, 27, tzinfo=timezone.utc))
    at_cap = {"properties": {"starttime": cap.timestamp() * 1000}}
    assert _date_within_tolerance(at_cap, date(2024, 4, 27), 1)
    week_later = {"properties": {"starttime": datetime(2024, 5, 4, tzinfo=timezone.utc).timestamp() * 1000}}
    assert not _date_within_tolerance(week_later, date(2024, 4, 27), 1)
    assert _date_within_tolerance({"properties": {}}, date(2024, 4, 27), 1)   # undated stays


def test_dat_query_filters_by_box_and_date_on_the_server():
    """The layer answers at most 2000 features, so the date goes in `where`."""
    url = _arcgis_query_url(-98.5, 35.0, -97.0, 36.0, date(2013, 5, 20), 1)
    assert url.startswith(dpf.endpoints.DAT_DAMAGE_LINES + "/query?")
    q = parse_qs(urlparse(url).query)
    assert "starttime >= TIMESTAMP '2013-05-19 00:00:00'" in q["where"][0]
    assert "starttime < TIMESTAMP '2013-05-22 00:00:00'" in q["where"][0]
    assert "starttime IS NULL AND stormdate >= TIMESTAMP" in q["where"][0]
    assert json.loads(q["geometry"][0])["xmin"] == -98.5
    assert q["f"] == ["geojson"]


def test_fetch_dat_normalizes_and_filters(monkeypatch):
    inside = _line_feature([[-97.5, 35.2], [-97.4, 35.3]], efscale="EF4", maxwind=-99,
                           starttime=datetime(2013, 5, 20, 20, tzinfo=timezone.utc).timestamp() * 1000)
    outside = _line_feature([[-90.0, 40.0], [-89.9, 40.1]], efscale="EF1")
    payload = json.dumps({"type": "FeatureCollection", "features": [inside, outside]}).encode()
    kinds = []
    monkeypatch.setattr(dpf.package_sources, "read_url",
                        lambda kind, request, opener, **kw: kinds.append(kind) or payload)
    fc = fetch_dat_damage_paths(-98.0, 35.0, -97.0, 36.0, date(2013, 5, 20))
    assert kinds == ["damage paths"]
    assert len(fc["features"]) == 1
    props = fc["features"][0]["properties"]
    assert props["ef_color"] == "#e60000" and props["maxwind"] is None


def test_fetch_dat_rejects_a_non_geojson_answer(monkeypatch):
    monkeypatch.setattr(dpf.package_sources, "read_url",
                        lambda *a, **kw: b'{"error": {"code": 400}}')
    with pytest.raises(RuntimeError):
        fetch_dat_damage_paths(-98.0, 35.0, -97.0, 36.0, date(2013, 5, 20))


# ── NCEI Storm Events ────────────────────────────────────────

def test_ncei_timezone_offset_reads_the_combined_format():
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": "CST-6"}) == -6.0
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": "CDT-5"}) == -5.0
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": "CST"}) == -6.0
    assert _ncei_timezone_offset_hours({"CZ_TIMEZONE": ""}) is None


def test_localize_ncei_time_to_utc():
    assert _localize_ncei_time("17-NOV-13 13:14:00", {"CZ_TIMEZONE": "CST-6"}) == \
        datetime(2013, 11, 17, 19, 14, tzinfo=timezone.utc)
    assert _localize_ncei_time("17-NOV-13 13:14:00", {}) == \
        datetime(2013, 11, 17, 13, 14, tzinfo=timezone.utc)
    assert _localize_ncei_time("garbage", {}) is None


def test_ncei_row_becomes_a_begin_to_end_line():
    row = {"EVENT_ID": "123", "BEGIN_LAT": "35.5", "BEGIN_LON": "-98.0", "END_LAT": "35.6",
           "END_LON": "-97.8", "BEGIN_DATE_TIME": "31-MAY-13 18:03:00",
           "END_DATE_TIME": "31-MAY-13 18:43:00", "CZ_TIMEZONE": "CST-6", "TOR_F_SCALE": "EF3",
           "TOR_LENGTH": "16.2", "TOR_WIDTH": "4576"}
    f = _ncei_row_to_feature(row)
    assert f["geometry"]["coordinates"] == [[-98.0, 35.5], [-97.8, 35.6]]
    props = f["properties"]
    assert props["ef_label"] == "EF3" and props["damage_source"] == "NCEI Storm Events"
    assert props["starttime"] == datetime(2013, 6, 1, 0, 3, tzinfo=timezone.utc).timestamp() * 1000
    assert _ncei_row_to_feature({"BEGIN_LAT": ""}) is None


_NCEI_CSV = (
    "EVENT_ID,EVENT_TYPE,BEGIN_LAT,BEGIN_LON,END_LAT,END_LON,BEGIN_DATE_TIME,END_DATE_TIME,"
    "CZ_TIMEZONE,TOR_F_SCALE\n"
    "1,Tornado,35.5,-98.0,35.6,-97.8,31-MAY-13 18:03:00,31-MAY-13 18:43:00,CST-6,EF3\n"
    "2,Hail,35.5,-98.0,,,31-MAY-13 18:03:00,31-MAY-13 18:03:00,CST-6,\n"
    "3,Tornado,41.0,-90.0,41.1,-89.9,31-MAY-13 18:03:00,31-MAY-13 18:10:00,CST-6,EF1\n"
    "4,Tornado,35.5,-98.0,35.6,-97.8,10-APR-13 18:03:00,10-APR-13 18:10:00,CST-6,EF0\n"
)


def _fake_ncei(monkeypatch):
    requests = []
    listing = b'<a href="StormEvents_details-ftp_v1.0_d2013_c20250101.csv.gz">' \
              b'<a href="StormEvents_details-ftp_v1.0_d2013_c20260323.csv.gz">'

    def read_url(kind, request, opener, **kw):
        requests.append(request.full_url)
        if request.full_url.endswith("/"):
            return listing
        return gzip.compress(_NCEI_CSV.encode())

    monkeypatch.setattr(dpf.package_sources, "read_url", read_url)
    return requests


def test_ncei_reads_the_newest_csv_and_keeps_tornadoes_in_box_and_date(monkeypatch):
    requests = _fake_ncei(monkeypatch)
    fc = fetch_ncei_storm_events_damage_paths(-98.5, 35.0, -97.0, 36.0, date(2013, 5, 31))
    assert [f["properties"]["event_id"] for f in fc["features"]] == ["1"]
    assert requests[-1].endswith("d2013_c20260323.csv.gz")


def test_ncei_year_is_downloaded_once(monkeypatch):
    requests = _fake_ncei(monkeypatch)
    fetch_ncei_storm_events_damage_paths(-98.5, 35.0, -97.0, 36.0, date(2013, 5, 31))
    n = len(requests)
    fc = fetch_ncei_storm_events_damage_paths(-91.0, 40.5, -89.0, 41.5, date(2013, 5, 31))
    assert len(requests) == n              # another box, same year: no new download
    assert [f["properties"]["event_id"] for f in fc["features"]] == ["3"]


def test_ncei_spans_both_years_at_new_year(monkeypatch):
    years = []
    monkeypatch.setattr(dpf, "_ncei_tornadoes", lambda year: years.append(year) or [])
    fetch_ncei_storm_events_damage_paths(-98, 35, -97, 36, date(2013, 12, 31))
    assert years == [2013, 2014]


# ── the fetcher ──────────────────────────────────────────────

def test_a_redrawn_box_drops_the_stale_load(monkeypatch):
    release_first = threading.Event()
    started_first = threading.Event()
    calls = {"n": 0}

    def fake_dat(west, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            started_first.set()
            release_first.wait(timeout=5)
            return {"type": "FeatureCollection", "features": [{"stale": True}]}
        return {"type": "FeatureCollection", "features": [{"fresh": True}]}

    monkeypatch.setattr(dpf, "fetch_dat_damage_paths", fake_dat)
    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)

    fetcher.set_bbox(-98, 35, -97, 36)
    assert started_first.wait(timeout=5)
    fetcher.set_bbox(-90, 30, -89, 31)
    _wait_for(app, lambda: received)
    release_first.set()
    _wait_for(app, lambda: False, timeout=0.4)

    assert [r["features"] for r in received] == [[{"fresh": True}]]


def test_cancel_drops_the_load(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(dpf, "fetch_dat_damage_paths",
                        lambda *a, **kw: release.wait(5) and {"type": "FeatureCollection", "features": [1]})
    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)
    fetcher.set_bbox(-98, 35, -97, 36)
    fetcher.cancel()
    release.set()
    _wait_for(app, lambda: False, timeout=0.4)
    assert received == []


def test_falls_back_to_ncei_only_when_dat_has_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(dpf, "fetch_dat_damage_paths",
                        lambda *a, **kw: calls.append("dat") or {"type": "FeatureCollection", "features": []})
    monkeypatch.setattr(dpf, "fetch_ncei_storm_events_damage_paths",
                        lambda *a, **kw: calls.append("ncei") or {"type": "FeatureCollection", "features": [{"id": 1}]})
    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)
    fetcher.set_bbox(-98, 35, -97, 36)
    _wait_for(app, lambda: received)
    assert calls == ["dat", "ncei"]
    assert received[0]["features"] == [{"id": 1}]


def test_a_dat_failure_also_falls_back(monkeypatch):
    def boom(*a, **kw):
        raise OSError("timed out")

    monkeypatch.setattr(dpf, "fetch_dat_damage_paths", boom)
    monkeypatch.setattr(dpf, "fetch_ncei_storm_events_damage_paths",
                        lambda *a, **kw: {"type": "FeatureCollection", "features": [{"id": 2}]})
    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)
    fetcher.set_bbox(-98, 35, -97, 36)
    _wait_for(app, lambda: received)
    assert received[0]["features"] == [{"id": 2}]


def test_skips_ncei_when_dat_has_paths(monkeypatch):
    monkeypatch.setattr(dpf, "fetch_dat_damage_paths",
                        lambda *a, **kw: {"type": "FeatureCollection", "features": [{"id": 1}]})

    def fail_ncei(*a, **kw):
        raise AssertionError("NCEI queried although DAT had paths")

    monkeypatch.setattr(dpf, "fetch_ncei_storm_events_damage_paths", fail_ncei)
    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    received = []
    fetcher.paths_ready.connect(received.append)
    fetcher.set_bbox(-98, 35, -97, 36)
    _wait_for(app, lambda: received)
    assert received[0]["features"] == [{"id": 1}]


def test_both_sources_failing_is_an_error(monkeypatch):
    def boom(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(dpf, "fetch_dat_damage_paths", boom)
    monkeypatch.setattr(dpf, "fetch_ncei_storm_events_damage_paths", boom)
    app = _qapp()
    fetcher = ArchiveDamagePathsFetcher(datetime(2013, 5, 31, tzinfo=timezone.utc))
    errors = []
    fetcher.error.connect(errors.append)
    fetcher.set_bbox(-98, 35, -97, 36)
    _wait_for(app, lambda: errors)
    assert errors and "offline" in errors[0]
