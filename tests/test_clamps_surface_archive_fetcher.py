"""C18: CLAMPS trailer surface observations -- the met tower and the MWR's
surface fields -- and their playback on the archive clock."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from PyQt6.QtWidgets import QApplication

from archive.fetchers import clamps_surface_archive_fetcher as sfc
from archive.fetchers.clamps_surface_playback import ClampsSurfacePlayback
from core.observation import Observation
from data import thredds_paths as paths
from tests.clamps_fakes import FakeThredds, surface_file

UTC = timezone.utc


def _epoch(*args):
    return int(datetime(*args, tzinfo=UTC).timestamp())


def _mwr(tmp_path, **kw):
    args = dict(base_time=_epoch(2022, 5, 25), time_offset=[0.0, 3600.0], sfc_temp=[25.0, 20.0],
                sfc_rh=[50.0, 100.0], sfc_pres=[1005.0, 1004.0], sfc_wspd=[3.0, 5.0],
                sfc_wdir=[190.0, 200.0])
    args.update(kw)
    return surface_file(tmp_path, **args)


def test_dewpoint_from_rh():
    assert sfc._dewpoint_c_from_rh(25.0, 50.0) == pytest.approx(13.86, abs=0.05)
    assert sfc._dewpoint_c_from_rh(20.0, 100.0) == pytest.approx(20.0, abs=0.05)
    assert sfc._dewpoint_c_from_rh(20.0, 0.0) is None
    assert sfc._dewpoint_c_from_rh(20.0, 101.0) is None


def test_parse_maps_fields(tmp_path):
    obs = sfc.parse_clamps_surface_netcdf(_mwr(tmp_path), "CLAMPS2")
    assert [o.timestamp.hour for o in obs] == [0, 1]
    first = obs[0]
    assert (first.vehicle_id, first.icon_type) == ("CLAMPS2", "lidar")
    assert (first.lat, first.lon) == pytest.approx((29.4, -95.0), abs=1e-4)
    assert first.temperature_c == 25.0
    assert first.dewpoint_c == pytest.approx(13.86, abs=0.05)
    assert (first.wind_speed_ms, first.wind_dir_deg, first.pressure_mb) == (3.0, 190.0, 1005.0)


def test_parse_drops_rows_without_temperature_and_fill_values(tmp_path):
    data = _mwr(tmp_path, time_offset=[0.0, 60.0, 120.0], sfc_temp=[25.0, -999.0, 24.0],
                sfc_rh=[-999.0, 50.0, 50.0], sfc_pres=[1005.0, 1005.0, -999.0],
                sfc_wspd=[-999.0, 3.0, 3.0], sfc_wdir=[-999.0, 180.0, 180.0])
    first, last = sfc.parse_clamps_surface_netcdf(data, "CLAMPS1")
    assert (first.dewpoint_c, first.wind_speed_ms, first.wind_dir_deg) == (None, None, None)
    assert last.pressure_mb is None and last.temperature_c == 24.0


def test_mwr_direction_with_the_heading_added_is_wrapped(tmp_path):
    """CLAMPS1 MWR, 2021-07-29: direction plus heading, unwrapped (360-718°)."""
    data = _mwr(tmp_path, time_offset=[0.0, 60.0, 120.0], sfc_temp=[20.0] * 3, sfc_rh=[50.0] * 3,
                sfc_pres=[830.0] * 3, sfc_wspd=[2.0] * 3, sfc_wdir=[423.0, 360.0, 800.0])
    assert [o.wind_dir_deg for o in sfc.parse_clamps_surface_netcdf(data, "CLAMPS1")] == [63.0, 0.0, None]


def test_met_tower_direction_is_left_out(tmp_path):
    obs = sfc.parse_clamps_surface_netcdf(_mwr(tmp_path), "CLAMPS2", trust_wind_direction=False)
    assert all(o.wind_dir_deg is None for o in obs)
    assert all(o.wind_speed_ms is not None for o in obs)


def test_met_tower_positions_per_record_from_classic_netcdf(tmp_path):
    data = _mwr(tmp_path, lat=[35.0, 0.0], lon=[-97.0, 0.0], classic=True)
    assert data.startswith(b"CDF")
    (obs,) = sfc.parse_clamps_surface_netcdf(data, "CLAMPS2", trust_wind_direction=False)
    assert (obs.lat, obs.lon) == pytest.approx((35.0, -97.0))          # (0, 0) dropped


def test_kelvin_under_a_celsius_label_is_converted(tmp_path):
    """CLAMPS1 MWR files in 2023 hold sfc_temp in kelvin."""
    data = _mwr(tmp_path, sfc_temp=[304.27, -999.0], sfc_rh=[50.0, 50.0])
    (obs,) = sfc.parse_clamps_surface_netcdf(data, "CLAMPS1")
    assert obs.temperature_c == pytest.approx(31.12, abs=0.01)


def test_celsius_files_are_left_alone(tmp_path):
    obs = sfc.parse_clamps_surface_netcdf(_mwr(tmp_path, sfc_temp=[45.0, 30.0]), "CLAMPS1")
    assert [o.temperature_c for o in obs] == [45.0, 30.0]


def test_sources_prefer_the_met_tower_for_clamps2():
    order = [(s.platform_id, s.kind) for s in sfc.KNOWN_CLAMPS_SURFACE_SOURCES]
    assert order.index(("CLAMPS2", "met_tower")) < order.index(("CLAMPS2", "mwr"))
    assert {s.platform_id for s in sfc.KNOWN_CLAMPS_SURFACE_SOURCES} == {"CLAMPS1", "CLAMPS2"}


def _folder(platform_id, kind):
    source = next(s for s in sfc.KNOWN_CLAMPS_SURFACE_SOURCES
                  if (s.platform_id, s.kind) == (platform_id, kind))
    return source.datastream, paths.clamps_ingested(source.platform_dir, source.datastream)


def test_files_come_from_the_listing_whatever_their_start_time(monkeypatch, tmp_path):
    """Most MWR days have no .000000 file; restarts write a second one."""
    fake = FakeThredds(monkeypatch)
    stream, folder = _folder("CLAMPS1", "mwr")
    fake.add(folder, f"{stream}.20190309.000646.cdf", _mwr(tmp_path, name="a.cdf"))
    # a restart's file overlaps the first by one record
    fake.add(folder, f"{stream}.20190309.010000.cdf",
             _mwr(tmp_path, time_offset=[3600.0, 7200.0], name="b.cdf"))
    fake.add(folder, f"{stream}.20190310.000000.cdf", b"another day")
    rows = sfc.fetch_clamps_surface_observations(datetime(2019, 3, 9, tzinfo=UTC))
    assert [o.timestamp.hour for o in rows] == [0, 1, 2]
    assert not any("20190310" in u for u in fake.file_requests())


def test_each_trailer_takes_its_first_source_with_data(monkeypatch, tmp_path):
    fake = FakeThredds(monkeypatch)
    met_stream, met_folder = _folder("CLAMPS2", "met_tower")
    fake.add(met_folder, f"{met_stream}.20210729.000033.cdf",          # all fill that day
             _mwr(tmp_path, sfc_temp=[-999.0, -999.0], name="met.cdf"))
    for platform in ("CLAMPS1", "CLAMPS2"):
        stream, folder = _folder(platform, "mwr")
        fake.add(folder, f"{stream}.20210729.000000.cdf", _mwr(tmp_path, name=f"{platform}.cdf"))
    rows = sfc.fetch_clamps_surface_observations(datetime(2021, 7, 29, tzinfo=UTC))
    assert sorted({o.vehicle_id for o in rows}) == ["CLAMPS1", "CLAMPS2"]
    assert len(rows) == 4


def test_nothing_and_a_failed_listing_raises(monkeypatch):
    fake = FakeThredds(monkeypatch)
    _, folder = _folder("CLAMPS1", "mwr")
    fake.broken_folders.add(folder)
    with pytest.raises(Exception, match="timed out"):
        sfc.fetch_clamps_surface_observations(datetime(2021, 7, 29, tzinfo=UTC))


def test_no_files_that_day_is_empty(monkeypatch):
    FakeThredds(monkeypatch)
    assert sfc.fetch_clamps_surface_observations(datetime(2021, 7, 29, tzinfo=UTC)) == []


# -- playback --------------------------------------------------------------------------

def test_playback_never_shows_the_future_or_stale_reports():
    t = datetime(2026, 6, 10, tzinfo=UTC)
    first = Observation("CLAMPS1", 35, -97, t, temperature_c=20)
    later = Observation("CLAMPS1", 35, -97, t + timedelta(minutes=10), temperature_c=21)
    other = Observation("CLAMPS2", 36, -96, t, temperature_c=22)
    playback = ClampsSurfacePlayback()
    playback.install([later, first, other])
    assert playback.at("CLAMPS1", t - timedelta(seconds=1)) is None
    assert playback.at("CLAMPS1", t + timedelta(minutes=4)) is first
    assert playback.at("CLAMPS1", t + timedelta(minutes=6)) is None
    assert playback.history("CLAMPS1", t) == [first]
    assert playback.at("CLAMPS2", t) is other
    assert first.icon_type == "lidar"
    assert playback.at("CLAMPS9", t) is None and playback.history("CLAMPS9", t) == []


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(app, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_playback_loads_the_session_span(monkeypatch, app):
    asked = []

    def fetch(day):
        asked.append(day.date().isoformat())
        return [Observation("CLAMPS1", 35, -97, day + timedelta(hours=h)) for h in (5, 7)]

    monkeypatch.setattr(sfc, "fetch_clamps_surface_observations", fetch)
    playback = ClampsSurfacePlayback()
    loaded = []
    playback.loaded.connect(loaded.append)
    playback.load(datetime(2024, 4, 27, 20, tzinfo=UTC))
    _wait(app, lambda: loaded)
    assert asked == ["2024-04-27", "2024-04-28"]
    # the next morning's 07Z is past the session's cap
    assert [o.timestamp.strftime("%d %H") for o in loaded[0]] == ["27 05", "27 07", "28 05"]


def test_playback_reports_errors_and_nothing_after_shutdown(monkeypatch, app):
    def down(day):
        raise TimeoutError("down")

    monkeypatch.setattr(sfc, "fetch_clamps_surface_observations", down)
    playback = ClampsSurfacePlayback()
    errors = []
    playback.error.connect(errors.append)
    playback.load(datetime(2024, 4, 27, tzinfo=UTC))
    _wait(app, lambda: errors)
    assert errors == ["down"]
    playback.shutdown()
    playback.load(datetime(2024, 4, 27, tzinfo=UTC))
    _wait(app, lambda: False, timeout=0.3)
    assert errors == ["down"]
