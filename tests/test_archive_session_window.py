"""The archive session's playable span: its UTC day into the next morning,
ended by the day's activity."""

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from archive.session import activity_end, last_moving_time, session_bounds
from archive.time_controller import TimeController
from core.observation import Observation
from ui.app.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _track(start: datetime, seconds: int, *, speed_deg_per_s: float, lat: float = 35.0) -> list[Observation]:
    return [Observation("probe1", lat + i * speed_deg_per_s, -97.0, start + timedelta(seconds=i))
            for i in range(seconds)]


def test_session_spans_its_utc_day_and_the_next_morning_to_06z():
    assert session_bounds(_utc(2024, 4, 27, 20, 15)) == (_utc(2024, 4, 27), _utc(2024, 4, 28, 6))
    # a naive start time is UTC, not local time
    assert session_bounds(datetime(2024, 4, 27, 20, 15)) == (_utc(2024, 4, 27), _utc(2024, 4, 28, 6))


def test_parked_vehicle_logging_overnight_is_not_activity():
    driving = _track(_utc(2024, 4, 28, 1, 0), 120, speed_deg_per_s=3e-4)   # ~33 m/s
    parked = _track(_utc(2024, 4, 28, 1, 2), 3 * 3600, speed_deg_per_s=0.0, lat=driving[-1].lat)

    # moving is judged over a minute, so it reads as moving until a minute
    # after the stop -- immaterial next to the half-hour margin
    stopped = last_moving_time(driving + parked)
    assert _utc(2024, 4, 28, 1, 1, 59) <= stopped <= _utc(2024, 4, 28, 1, 3)
    assert last_moving_time(parked) is None


def test_session_ends_half_an_hour_after_the_last_activity():
    opened = _utc(2024, 4, 27, 18)
    assert activity_end(opened, [_utc(2024, 4, 28, 2, 33)]) == _utc(2024, 4, 28, 3, 3)


def test_session_can_end_before_00z_on_a_quiet_evening():
    opened = _utc(2024, 4, 27, 18)
    assert activity_end(opened, [_utc(2024, 4, 27, 21, 10)]) == _utc(2024, 4, 27, 21, 40)


def test_session_end_is_capped_at_06z_next_day():
    opened = _utc(2024, 4, 27, 18)
    assert activity_end(opened, [_utc(2024, 4, 28, 5, 50)]) == _utc(2024, 4, 28, 6)


def test_session_never_ends_before_the_time_it_was_opened_at():
    opened = _utc(2024, 4, 27, 23)
    assert activity_end(opened, [_utc(2024, 4, 27, 19)]) == _utc(2024, 4, 27, 23, 30)


def test_no_activity_evidence_leaves_the_decision_to_the_caller():
    assert activity_end(_utc(2024, 4, 27, 18), []) is None
    assert activity_end(_utc(2024, 4, 27, 18), [None]) is None


def test_clock_plays_past_00z_but_stops_at_the_session_end():
    tc = TimeController(_utc(2024, 4, 27, 23, 59, 50))
    tc.step(30)
    assert tc.current_time == _utc(2024, 4, 28, 0, 0, 20)

    tc.set_window_end(_utc(2024, 4, 28, 0, 10))
    tc.step(3600)
    assert tc.current_time == _utc(2024, 4, 28, 0, 9, 59)


def test_shrinking_the_window_pulls_the_clock_back_inside():
    tc = TimeController(_utc(2024, 4, 27, 20))
    tc.set_time(_utc(2024, 4, 28, 3))
    emitted = []
    tc.window_changed.connect(lambda start, end: emitted.append(end))

    tc.set_window_end(_utc(2024, 4, 27, 21, 40))

    assert emitted == [_utc(2024, 4, 27, 21, 40)]
    assert tc.current_time == _utc(2024, 4, 27, 21, 39, 59)
    assert tc.window_seconds() == int(timedelta(hours=21, minutes=40).total_seconds())


def test_slider_positions_are_seconds_since_the_sessions_midnight():
    tc = TimeController(_utc(2024, 4, 27, 12))
    tc.set_seconds_since_start(int(timedelta(hours=25, minutes=30).total_seconds()))
    assert tc.current_time == _utc(2024, 4, 28, 1, 30)
    assert tc.seconds_since_start() == int(timedelta(hours=25, minutes=30).total_seconds())


def test_the_previous_evenings_tail_does_not_end_the_session():
    # 2025-06-06: the DL truck's only driving was 00:30-00:57 UTC -- the
    # evening of 5 June local time -- then it sat parked. That must not end
    # a session opened at 20Z at 20:30.
    opened = _utc(2025, 6, 6, 20)
    assert activity_end(opened, [_utc(2025, 6, 6, 0, 57)]) is None


def test_activity_from_12z_on_still_ends_the_session():
    opened = _utc(2025, 6, 6, 18)
    assert activity_end(opened, [_utc(2025, 6, 6, 0, 57), _utc(2025, 6, 6, 21, 10)]) == _utc(2025, 6, 6, 21, 40)


def test_clock_pauses_when_playback_reaches_the_session_end():
    tc = TimeController(_utc(2024, 4, 27, 21, 39, 50))
    tc.set_window_end(_utc(2024, 4, 27, 21, 40))
    tc.play()
    tc._on_tick()   # 10x: five archive seconds per tick
    assert tc.is_playing
    tc._on_tick()
    assert not tc.is_playing and tc.current_time == _utc(2024, 4, 27, 21, 39, 59)


def test_jumps_outside_the_window_are_clamped():
    tc = TimeController(_utc(2024, 4, 27, 20))
    tc.set_time(_utc(2024, 4, 26, 23))
    assert tc.current_time == _utc(2024, 4, 27)
    tc.set_time(_utc(2024, 4, 28, 9))
    assert tc.current_time == _utc(2024, 4, 28, 5, 59, 59)
    assert tc.window == (_utc(2024, 4, 27), _utc(2024, 4, 28, 6))


# --- fetchers read the next morning ----------------------------------------

def _s3_listing(*keys: str) -> str:
    contents = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    return f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{contents}</ListBucketResult>'


def test_radar_index_lists_both_days_clipped_to_the_window(monkeypatch):
    import archive.fetchers.radar_archive_fetcher as raf

    listings = {
        "2024/04/27/KTLX/": _s3_listing("2024/04/27/KTLX/KTLX20240427_235800_V06"),
        "2024/04/28/KTLX/": _s3_listing("2024/04/28/KTLX/KTLX20240428_013000_V06",
                                        "2024/04/28/KTLX/KTLX20240428_070000_V06"),
    }
    requested = []

    def fake_get(url, timeout=15):
        prefix = url.split("prefix=")[1].split("&")[0]
        requested.append(prefix)
        return SimpleNamespace(text=listings[prefix], raise_for_status=lambda: None)

    monkeypatch.setattr(raf.requests, "get", fake_get)
    fetcher = raf.ArchiveRadarFetcher("KTLX", _utc(2024, 4, 27, 20))
    try:
        fetcher._fetch_index()
        assert requested == ["2024/04/27/KTLX/", "2024/04/28/KTLX/"]
        assert fetcher._index == [_utc(2024, 4, 27, 23, 58), _utc(2024, 4, 28, 1, 30)]
        assert fetcher._index_keys[_utc(2024, 4, 28, 1, 30)] == "2024/04/28/KTLX/KTLX20240428_013000_V06"
    finally:
        fetcher.shutdown()


def test_mqtt_reader_adds_the_next_mornings_records_to_the_cap(monkeypatch):
    from archive.fetchers import mqtt_reader as mr

    def record(ts: str) -> str:
        return json.dumps({"vehicle_id": "WX1", "active": True, "mode": "point",
                           "lat": 35.0, "lon": -97.0, "timestamp": ts})

    files = {
        ("scan_sectors", "20240427"): record("2024-04-27T23:00:00Z"),
        ("scan_sectors", "20240428"): "\n".join([record("2024-04-28T01:30:00Z"),
                                                 record("2024-04-28T07:00:00Z")]),
    }
    monkeypatch.setattr(mr, "_fetch_topic_text",
                        lambda topic, date_str: (files.get((topic, date_str)), "test"))
    reader = mr.ArchiveMQTTReader(session_date=_utc(2024, 4, 27, 20))
    reader._fetch_all()

    assert [ts for ts, _ in reader._data["scan_sectors"]] == [_utc(2024, 4, 27, 23), _utc(2024, 4, 28, 1, 30)]


def test_a_failed_next_morning_file_does_not_fail_the_session(monkeypatch):
    from archive.fetchers import mqtt_reader as mr

    def fetch(topic, date_str):
        if date_str == "20240428":
            raise TimeoutError("timed out")
        return None, "test"

    monkeypatch.setattr(mr, "_fetch_topic_text", fetch)
    reader = mr.ArchiveMQTTReader(session_date=_utc(2024, 4, 27, 20))
    errors = []
    reader.error.connect(errors.append)
    reader._fetch_all()

    assert errors == []


def test_mqtt_activity_is_the_last_crew_change_and_last_drive():
    from archive.fetchers import mqtt_reader as mr

    reader = mr.ArchiveMQTTReader(session_date=_utc(2024, 4, 27, 20))
    reader._data["annotations"] = [(_utc(2024, 4, 27, 22), {}), (_utc(2024, 4, 28, 0, 40), {})]
    reader._vehicle_observations = {
        "WX1": _track(_utc(2024, 4, 27, 23), 120, speed_deg_per_s=3e-4),
        "WX2": _track(_utc(2024, 4, 27, 23), 600, speed_deg_per_s=0.0),
    }

    times = reader.activity_times()

    assert _utc(2024, 4, 28, 0, 40) in times
    assert len(times) == 2   # the parked WX2 adds nothing


def test_satellite_lists_the_next_mornings_hours(monkeypatch):
    import archive.fetchers.satellite_archive_fetcher as saf

    prefixes = []

    def fake_get(url, timeout=None):
        prefixes.append(url.split("prefix=")[1])
        return SimpleNamespace(content=_s3_listing().encode(), raise_for_status=lambda: None)

    monkeypatch.setattr(saf.requests, "get", fake_get)
    fetcher = saf.ArchiveSatelliteFetcher(_utc(2024, 12, 31, 20))
    fetcher._list_day_refs("conus", "ABI-L1b-RadC")

    assert len(prefixes) == 30
    assert prefixes[0].endswith("2024/366/00/") and prefixes[-1].endswith("2025/001/05/")


# --- the window sized once the day's activity is in --------------------------

class _Host:
    _update_archive_session_end = MainWindow._update_archive_session_end

    def __init__(self, mqtt_times, obs_times=None):
        self._archive_time = _utc(2024, 4, 27, 20)
        self._time_ctrl = TimeController(self._archive_time)
        self._archive_mqtt = SimpleNamespace(_loaded=True, activity_times=lambda: list(mqtt_times))
        self._archive_vehicle_obs_started = obs_times is not None
        self._archive_vehicle_obs_loaded = False
        self._archive_vehicle_obs = (SimpleNamespace(activity_times=lambda: list(obs_times))
                                     if obs_times is not None else None)


def test_window_waits_for_one_second_tracks_before_ending():
    host = _Host([_utc(2024, 4, 27, 21)], obs_times=[_utc(2024, 4, 28, 2)])
    host._update_archive_session_end()
    assert host._time_ctrl.window[1] == _utc(2024, 4, 28, 6)   # not yet sized

    host._archive_vehicle_obs_loaded = True
    host._update_archive_session_end()
    assert host._time_ctrl.window[1] == _utc(2024, 4, 28, 2, 30)


def test_window_keeps_the_full_utc_day_without_activity():
    host = _Host([])
    host._update_archive_session_end()
    assert host._time_ctrl.window[1] == _utc(2024, 4, 28)
