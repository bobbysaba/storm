"""Tests for one-second FOFS archive observations."""

from datetime import datetime, timedelta, timezone

from archive.fetchers.vehicle_obs_archive_fetcher import (
    ArchiveVehicleObsFetcher,
    _daily_url,
    parse_vehicle_csv,
)


_CSV = """sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon
2.3,215.2,22.82,19.5,977.29,160426,000004,33.24266,-97.60402
2.6,209.2,22.83,19.6,977.30,160426,000005,33.24267,-97.60403
"""


def _utc(second: int) -> datetime:
    return datetime(2026, 4, 16, tzinfo=timezone.utc) + timedelta(seconds=second)


def test_daily_url_uses_vehicle_alias():
    assert _daily_url("lid1", "20260416").endswith(
        "/dltruck/raw/20260416.txt"
    )
    assert _daily_url("p1", "20260416").endswith(
        "/probe1/raw/20260416.txt"
    )


def test_parse_vehicle_csv_maps_observation_fields():
    observations = parse_vehicle_csv(_CSV, "hailcam", "hailcam")

    assert len(observations) == 2
    assert observations[0].timestamp == _utc(4)
    assert observations[0].vehicle_id == "hailcam"
    assert observations[0].icon_type == "hailcam"
    assert observations[0].temperature_c == 22.82
    assert observations[0].dewpoint_c == 19.5
    assert observations[0].wind_speed_ms == 2.3
    assert observations[0].wind_dir_deg == 215.2
    assert observations[0].pressure_mb == 977.29


def test_fetcher_looks_up_and_emits_latest_observation():
    fetcher = ArchiveVehicleObsFetcher(_utc(0))
    observations = parse_vehicle_csv(_CSV, "hailcam")
    fetcher._observations = {"hailcam": observations}
    fetcher._timestamps = {
        "hailcam": [obs.timestamp for obs in observations]
    }
    fetcher._loaded = True
    emitted = []
    fetcher.observation_ready.connect(emitted.append)

    fetcher.on_time_changed(_utc(4))
    fetcher.on_time_changed(_utc(5))

    assert emitted == observations
    assert fetcher.has_fresh_observation("hailcam", _utc(59)) is True
    assert fetcher.has_fresh_observation("hailcam", _utc(66)) is False
    assert fetcher.history("hailcam", _utc(4)) == observations[:1]
