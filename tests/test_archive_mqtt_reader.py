"""Tests for archive MQTT replay helpers."""

import json
from datetime import datetime, timezone

from archive.fetchers.mqtt_reader import ArchiveMQTTReader, _parse_jsonl


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 4, 26, hour, minute, tzinfo=timezone.utc)


def test_parse_jsonl_accepts_scan_sector_timestamp():
    records = _parse_jsonl(json.dumps({
        "vehicle_id": "WX1",
        "active": True,
        "mode": "point",
        "lat": 35.0,
        "lon": -97.0,
        "timestamp": "2026-04-26T12:05:00Z",
    }))

    assert records == [(_utc(12, 5), records[0][1])]


def test_archive_reader_replays_latest_scan_sector_state():
    reader = ArchiveMQTTReader(session_date=_utc(0))
    emitted = []
    reader.scan_sector_received.connect(emitted.append)
    reader._loaded = True
    reader._data["scan_sectors"] = _parse_jsonl("\n".join([
        json.dumps({
            "vehicle_id": "WX1",
            "active": True,
            "mode": "point",
            "lat": 35.0,
            "lon": -97.0,
            "timestamp": "2026-04-26T12:05:00Z",
        }),
        json.dumps({
            "vehicle_id": "WX1",
            "active": True,
            "mode": "sector",
            "lat": 35.1,
            "lon": -97.1,
            "range_m": 8000,
            "azimuth_deg": 270,
            "beam_width_deg": 60,
            "timestamp": "2026-04-26T12:10:00Z",
        }),
        json.dumps({
            "vehicle_id": "WX1",
            "active": False,
            "mode": "sector",
            "lat": 35.1,
            "lon": -97.1,
            "range_m": 8000,
            "azimuth_deg": 270,
            "beam_width_deg": 60,
            "timestamp": "2026-04-26T12:20:00Z",
        }),
    ]))

    reader.on_time_changed(_utc(12, 15))
    assert emitted[-1].vehicle_id == "WX1"
    assert emitted[-1].active is True
    assert emitted[-1].mode == "sector"

    reader.on_time_changed(_utc(12, 25))
    assert emitted[-1].vehicle_id == "WX1"
    assert emitted[-1].active is False


def test_archive_reader_clears_scan_sectors_on_backward_seek():
    reader = ArchiveMQTTReader(session_date=_utc(0))
    cleared = []
    reader.scan_sectors_cleared.connect(lambda: cleared.append(True))
    reader._loaded = True

    reader.on_time_changed(_utc(12, 30))
    reader.on_time_changed(_utc(12, 0))

    assert cleared == [True]
