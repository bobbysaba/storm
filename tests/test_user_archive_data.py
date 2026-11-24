"""A user's own archive data: vehicle-obs files read with [obs_columns]
(archive/vehicle_obs.py), the calendar built from the configured sources
(archive/catalog.py), campaigns from settings, and case packages that carry
folder files to another machine."""

import dataclasses
import json
import tomllib
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event

import pytest

import settings
from archive import catalog as cat
from archive import vehicle_obs as vo
from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher
from headless.storm_headless.obs_columns import (FieldMap, ObsColumnsError, parse_obs_columns,
                                                 parse_obs_layouts, row_time)

# NSSL's FOFS logger layout, from its profile
FOFS = dict(parse_obs_layouts(tomllib.loads(
    (settings.PROJECT_DIR / "profiles" / "nssl.toml").read_text())["obs_columns"]))["fofs"]
FOFS_HEADER = "sfc_wspd,sfc_wdir,t_fast,dewpoint,pressure,gps_date,gps_time,lat,lon\n"
ISO = FieldMap(timestamp="time", lat="latitude", lon="longitude", temperature_c="temp",
               dewpoint_c="", wind_speed_ms="", wind_dir_deg="", pressure_mb="", heading_deg="")


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _use(monkeypatch, **archive):
    """Settings with these [archive] keys (and the NSSL provider as conftest left it)."""
    monkeypatch.setattr(settings, "SETTINGS", dataclasses.replace(
        settings.SETTINGS, archive=dataclasses.replace(settings.SETTINGS.archive, **archive)))


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# -- [obs_columns] ---------------------------------------------------------------

def test_obs_columns_default_to_a_plain_csv():
    assert parse_obs_columns({}) == FieldMap()
    assert (FieldMap().timestamp, FieldMap().temperature_c, FieldMap().file) == ("time", "temperature_c", "*{date}.csv")


def test_date_and_time_columns_replace_the_default_timestamp():
    fields = parse_obs_columns({"date": "d", "time": "t"})
    assert (fields.timestamp, fields.date_col, fields.time_col) == ("", "d", "t")


def test_named_layouts():
    layouts = parse_obs_layouts({"truck": {"label": "Truck", "temperature_c": "temp"},
                                 "gps": {"file": "*_????????.txt", "temperature_c": ""}})
    assert [name for name, _ in layouts] == ["truck", "gps"]
    assert layouts[0][1].label == "Truck" and layouts[0][1].daily and not layouts[1][1].daily
    assert parse_obs_layouts({"units": {"temperature_c": "F"}})[0][0] == "default"


@pytest.mark.parametrize("table, message", [
    ({"lat": "x", "gps": {}}, "every key goes in a layout"),
    ({"GPS": {}}, "a layout name is lower-case"),
    ({"gps": {"file": "logs/*.txt"}}, "file must be a file-name pattern"),
    ({"gps": {"file": "{day}.txt"}}, "only placeholder"),
])
def test_bad_named_layouts(table, message):
    with pytest.raises(ObsColumnsError, match=message):
        parse_obs_layouts(table)


def test_nssl_profile_has_both_loggers():
    profile = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml")
    assert [name for name, _ in profile.obs_layouts] == ["fofs", "gps_ka"]
    assert profile.obs_columns == FOFS and profile.obs_layout("gps_ka").lat == "Latitude"
    assert profile.obs_layout("gone") == FOFS


def test_obs_columns_override_and_blank_out_columns():
    fields = parse_obs_columns({"timestamp": "time_utc", "temperature_c": "temp", "pressure_mb": ""})
    assert fields.timestamp == "time_utc" and fields.temperature_c == "temp" and fields.pressure_mb == ""


@pytest.mark.parametrize("table, message", [
    ({"latitude": "lat"}, "unknown key latitude"),
    ({"lat": 3}, "lat must be a string"),
    ({"lon": ""}, "lat and lon"),
    ({"date": "", "timestamp": ""}, "timestamp column, or date and time"),
    ({"date": "d", "time": "t", "date_format": ""}, "date_format"),
])
def test_bad_obs_columns_are_refused(table, message):
    with pytest.raises(ObsColumnsError, match=message):
        parse_obs_columns(table)


def test_row_times_from_split_columns_iso_and_custom_formats():
    assert row_time({"gps_date": "60526", "gps_time": "13004"}, FOFS) == _utc(2026, 5, 6, 1, 30, 4)
    iso = FieldMap(timestamp="t")
    assert row_time({"t": "2026-05-06T21:30:04Z"}, iso) == _utc(2026, 5, 6, 21, 30, 4)
    assert row_time({"t": "2026-05-06 16:30:04-05:00"}, iso) == _utc(2026, 5, 6, 21, 30, 4)
    assert row_time({"t": "2026-05-06 21:30:04"}, iso) == _utc(2026, 5, 6, 21, 30, 4)   # no zone: UTC
    assert row_time({"t": "soon"}, iso) is None and row_time({}, iso) is None
    custom = FieldMap(timestamp="", date_col="d", time_col="t", date_format="%Y-%m-%d", time_format="%H:%M:%S")
    assert row_time({"d": "2026-05-06", "t": "21:30:04"}, custom) == _utc(2026, 5, 6, 21, 30, 4)


def test_storm_toml_and_headless_read_the_same_obs_columns(tmp_path):
    from headless.storm_headless.settings import SettingsError, load_settings
    table = '[obs_columns]\ntimestamp = "time_utc"\n'
    (tmp_path / "storm.toml").write_text(table)
    (tmp_path / "headless.toml").write_text(f'vehicle_id = "wx1"\ndata_dir = "."\n{table}')
    assert settings.load(tmp_path / "storm.toml").obs_columns.timestamp == "time_utc"
    assert load_settings(tmp_path / "headless.toml").obs_columns.timestamp == "time_utc"
    (tmp_path / "headless.toml").write_text('vehicle_id = "wx1"\ndata_dir = "."\n[obs_columns]\nlon = ""\n')
    with pytest.raises(SettingsError, match="lat and lon"):
        load_settings(tmp_path / "headless.toml")


def test_live_tail_reads_an_iso_timestamp_column(tmp_path):
    from headless.storm_headless.obs_tail import ObsFileTail
    _write(tmp_path, "wx1_20260506.csv", "time,latitude,longitude,temp\n2026-05-06T21:30:04Z,35.2,-97.4,27.5\n")
    tail = ObsFileTail(str(tmp_path), "wx1", field_map=ISO, now_fn=lambda: _utc(2026, 5, 6, 22))
    obs = tail.poll()
    assert obs.timestamp == _utc(2026, 5, 6, 21, 30, 4) and obs.temperature_c == 27.5


# -- [archive] settings ----------------------------------------------------------

def test_archive_vehicle_obs_and_campaign_settings(tmp_path):
    table = tomllib.loads('''
vehicle_obs = ["vehicle-obs", "https://obs.example.org/root", "nssl"]
vehicle_obs_path = "{vehicle}_{date}.csv"
[[campaigns]]
name = "Spring"
years = [2026, 2025, 2026]
''')
    archive = settings._parse_archive(table)
    assert archive.vehicle_obs == ("vehicle-obs", "https://obs.example.org/root", "nssl")
    assert archive.vehicle_obs_path == "{vehicle}_{date}.csv"
    assert archive.campaigns == (("Spring", (2025, 2026)),)


@pytest.mark.parametrize("table, message", [
    ({"vehicle_obs": "folder"}, "vehicle_obs must be a list"),
    ({"vehicle_obs_path": "{vehicle}.txt"}, "with {vehicle} and {date}"),
    ({"vehicle_obs_path": "/abs/{vehicle}/{date}.txt"}, "relative path"),
    ({"vehicle_obs_path": "../{vehicle}/{date}.txt"}, "relative path"),
    ({"vehicle_obs_path": "{vehicle}/{date}/{other}.txt"}, "only use"),
    ({"campaigns": [{"name": "X"}]}, "years must be a list"),
    ({"campaigns": [{"name": "X", "years": [26]}]}, "years must be a list"),
    ({"campaigns": [{"years": [2026]}]}, "name must be"),
    ({"campaigns": [{"name": "X", "years": [2026], "start": 1}]}, "unknown key start"),
])
def test_bad_archive_settings_are_refused(table, message):
    with pytest.raises(settings.SettingsError, match=message):
        settings._parse_archive(table)


def test_nssl_profile_carries_the_campaigns():
    profile = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml")
    assert not profile.error
    assert dict(profile.archive.campaigns)["TORUS"] == (2019, 2022, 2023)


# -- reading files -----------------------------------------------------------------

def test_csv_rows_get_the_generic_sanity_checks():
    text = FOFS_HEADER + "\n".join([
        "2.3,215,22.8,19.5,977.3,060526,213004,35.2,-97.4",     # fine
        "999,999,-999,40,977.3,060526,213005,35.2,-97.4",       # missing codes; dewpoint over temperature
        "2.3,215,22.8,19.5,977.3,060526,213006,0.0,0.0",        # null island
        "2.3,215,22.8,19.5,977.3,bad,213007,35.2,-97.4",        # no time
    ]) + "\n"
    rows = vo.parse_obs_csv(text, "wx1", "car", FOFS)
    assert [r.timestamp.second for r in rows] == [4, 5]
    assert rows[0].temperature_c == 22.8 and rows[0].dewpoint_c == 19.5 and rows[0].icon_type == "car"
    second = rows[1]
    assert (second.wind_speed_ms, second.wind_dir_deg, second.temperature_c) == (None, None, None)
    assert second.dewpoint_c == 40.0          # no temperature to check it against


def test_a_folder_source_reads_the_day_the_next_morning_and_the_local_day_before(tmp_path):
    _write(tmp_path, "wx1/20260505.txt", FOFS_HEADER + "1,1,20,10,970,060526,020000,35.2,-97.4\n"
                                         "1,1,20,10,970,050526,230000,35.2,-97.4\n")   # local day before
    _write(tmp_path, "wx1/20260506.txt", FOFS_HEADER + "1,1,20,10,970,060526,213000,35.3,-97.4\n")
    _write(tmp_path, "wx1/20260507.txt", FOFS_HEADER + "1,1,20,10,970,070526,050000,35.4,-97.4\n"
                                         "1,1,20,10,970,070526,070000,35.5,-97.4\n")   # past 06Z
    source = vo.FolderVehicleObsSource(tmp_path, "{vehicle}/{date}.txt", FOFS)
    rows = vo.in_session(source.load("wx1", None, date(2026, 5, 6)), date(2026, 5, 6))
    assert [r.timestamp for r in rows] == [_utc(2026, 5, 6, 2), _utc(2026, 5, 6, 21, 30), _utc(2026, 5, 7, 5)]
    assert source.load("wx2", None, date(2026, 5, 6)) is None
    assert source.vehicles(date(2026, 5, 6)) == {"wx1"}
    assert source.dates() == {"wx1": {date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 7)}}


def test_a_flat_path_pattern_with_its_own_columns(tmp_path):
    _write(tmp_path, "truck-a_20260506.csv", "time,latitude,longitude,temp\n2026-05-06T21:30:04Z,35.2,-97.4,27.5\n")
    _write(tmp_path, "notes.csv", "not a vehicle file\n")
    source = vo.FolderVehicleObsSource(tmp_path, "{vehicle}_{date}.csv", ISO)
    assert source.vehicles(date(2026, 5, 6)) == {"truck-a"}
    [obs] = source.load("truck-a", None, date(2026, 5, 6))
    assert obs.temperature_c == 27.5 and obs.vehicle_id == "truck-a"


def test_a_url_source_reads_files_and_its_dates_file(monkeypatch):
    files = {
        "https://obs.test/root/wx1/20260506.txt": (FOFS_HEADER + "1,1,20,10,970,060526,213000,35.3,-97.4\n").encode(),
        "https://obs.test/root/dates.json": json.dumps({"vehicles": {"wx1": ["20260506"], "wx2": ["20250101"]}}).encode(),
    }
    monkeypatch.setattr(vo, "fetch_url", lambda url: files.get(url))
    source = vo.UrlVehicleObsSource("https://obs.test/root/", "{vehicle}/{date}.txt", FOFS)
    assert len(source.load("wx1", None, date(2026, 5, 6))) == 1
    assert source.vehicles(date(2026, 5, 6)) == {"wx1"}
    files.pop("https://obs.test/root/dates.json")
    assert vo.UrlVehicleObsSource("https://obs.test/root", "{vehicle}/{date}.txt", FOFS).vehicles(
        date(2026, 5, 6)) is None


def test_dates_file_format():
    days, vehicles = vo.parse_dates_file('{"dates": ["20260506", "bad"], "vehicles": {"wx1": ["20260507"]}}')
    assert days == {date(2026, 5, 6)} and vehicles == {"wx1": {date(2026, 5, 7)}}
    with pytest.raises(ValueError):
        vo.parse_dates_file("[]")


def test_default_sources_are_the_providers_that_are_on(nssl_provider_off, tmp_path):
    assert vo.build_sources() == []
    sources = vo.build_sources(("obs", "https://obs.test", "nssl"))
    assert [type(s).__name__ for s in sources] == ["FolderVehicleObsSource", "UrlVehicleObsSource"]  # nssl off: skipped


def test_a_url_root_whose_key_isnt_set_is_skipped(monkeypatch):
    from data import auth
    monkeypatch.setattr(auth, "missing_key", lambda url: "OBS_KEY" if url == "https://keyed.test" else "")
    sources = vo.build_sources(("https://keyed.test", "https://open.test"))
    assert [s.root for s in sources] == ["https://open.test"]


def test_with_the_provider_on_fofs_is_the_default_source():
    from providers.nssl.archive.vehicle_obs_archive_fetcher import FofsVehicleObsSource
    assert [type(s) for s in vo.build_sources()] == [FofsVehicleObsSource]


# -- playback ----------------------------------------------------------------------

class _Source:
    def __init__(self, name, rows=None, vehicles=None, error=None):
        self.name, self.rows, self._vehicles, self.error, self.asked = name, rows or {}, vehicles, error, []

    def vehicles(self, day):
        return self._vehicles

    def load(self, vehicle_id, icon_type, day):
        self.asked.append(vehicle_id)
        if self.error:
            raise self.error
        return self.rows.get(vehicle_id)


def _track(vehicle_id, *hours):
    from core.observation import Observation
    return [Observation(vehicle_id, 35.2 + h * 0.01, -97.4, _utc(2026, 5, 6, h)) for h in hours]


def test_the_first_source_with_rows_for_a_vehicle_wins(nssl_provider_off):
    first = _Source("first", {"wx1": _track("wx1", 20)}, vehicles={"wx1"})
    second = _Source("second", {"wx1": _track("wx1", 21), "wx2": _track("wx2", 22)}, vehicles={"wx2"})
    fetcher = ArchiveVehicleObsFetcher(_utc(2026, 5, 6), sources=[first, second])
    fetcher._load_all({"wx3": "car"})
    assert fetcher.roster_size == 3
    assert {v: [o.timestamp.hour for o in obs] for v, obs in fetcher.observations_by_vehicle().items()} == {
        "wx1": [20], "wx2": [22]}


def test_a_failed_source_falls_through_but_fails_the_vehicle_if_nothing_else_has_it(nssl_provider_off):
    broken = _Source("broken", error=OSError("offline"))
    fetcher = ArchiveVehicleObsFetcher(_utc(2026, 5, 6), sources=[broken, _Source("ok", {"wx1": _track("wx1", 20)})])
    assert len(fetcher._fetch_vehicle("wx1", None)) == 1
    with pytest.raises(RuntimeError, match="offline"):
        fetcher._fetch_vehicle("wx2", None)


def test_without_sources_archive_doesnt_start_vehicle_obs(nssl_provider_off):
    assert not ArchiveVehicleObsFetcher(_utc(2026, 5, 6)).has_sources


def test_a_users_folder_plays_back_without_nssl(nssl_provider_off, monkeypatch, tmp_path):
    # a plain CSV, read with the default [obs_columns]
    _write(tmp_path, "wx1/20260506.csv", "time,lat,lon\n2026-05-06T21:30:00Z,35.3,-97.4\n")
    _use(monkeypatch, vehicle_obs=(str(tmp_path),))
    fetcher = ArchiveVehicleObsFetcher(_utc(2026, 5, 6))
    finished = []
    fetcher.load_finished.connect(finished.append)
    fetcher._load_all({})
    assert finished == [{"wx1"}]


# -- calendar ----------------------------------------------------------------------

def test_without_nssl_the_calendar_has_no_fofs_vehicles(nssl_provider_off):
    platforms = cat.all_platforms()
    assert not [p for p in platforms if p.platform_id.startswith("FOFS-")]
    assert [p.group for p in platforms] == [cat.RECORDED]       # the recordings folder


def test_with_nssl_the_fofs_vehicles_come_from_its_source():
    ids = {p.platform_id for p in cat.all_platforms()}
    assert "FOFS-probe1" in ids and "FOFS-other" in ids


def test_calendar_lists_recordings_and_vehicle_folders(nssl_provider_off, monkeypatch, tmp_path):
    recordings, obs = tmp_path / "rec", tmp_path / "obs"
    _write(recordings, "storm.vehicles.20260506", "{}\n")
    _write(recordings, "storm.cones.20260507.jsonl", "{}\n")
    _write(recordings, "other.vehicles.20250101", "{}\n")
    _write(obs, "wx1/20260506.csv", FOFS_HEADER)
    _write(obs, "wx2/20250601.csv", FOFS_HEADER)
    _use(monkeypatch, history=(str(recordings),), vehicle_obs=(str(obs),))
    index = cat.AvailabilityIndex(cat.all_platforms(), pace=0, cache_path=tmp_path / "cache.json")
    snapshot = list(index.scan(Event()))[-1]
    by_site = {site: frozenset(d for p in plats for d in snapshot.platforms[p.platform_id].dates)
               for site, plats in cat.platforms_by_site(index.platforms).items()}
    assert by_site == {"Recorded broker traffic": {date(2026, 5, 6), date(2026, 5, 7)},
                       "wx1": {date(2026, 5, 6)}, "wx2": {date(2025, 6, 1)}}
    assert not (tmp_path / "cache.json").exists()               # local folders aren't cached


def test_calendar_reads_url_roots_dates_files(nssl_provider_off, monkeypatch, tmp_path):
    _use(monkeypatch, history=("https://rec.test",), vehicle_obs=("https://obs.test",))
    pages = {"https://rec.test/dates.json": '{"dates": ["20260506"]}',
             "https://obs.test/dates.json": '{"vehicles": {"wx1": ["20260507"]}}'}
    index = cat.AvailabilityIndex(cat.all_platforms(), lambda url, _: pages[url], pace=0)
    assert list(index.scan(Event()))[-1].dates == {date(2026, 5, 6), date(2026, 5, 7)}


def test_a_url_root_without_a_dates_file_shades_nothing(nssl_provider_off, monkeypatch):
    _use(monkeypatch, history=("https://rec.test",))
    index = cat.AvailabilityIndex(cat.all_platforms(), lambda url, _: "", pace=0)
    result = list(index.scan(Event()))[-1]
    assert result.dates == set() and all(r.complete for r in result.platforms.values())


def test_campaigns_come_from_settings(monkeypatch):
    _use(monkeypatch, campaigns=(("Spring", (2026,)),))
    assert cat.campaign_years() == {"Spring": (2026,)}


# -- case packages ------------------------------------------------------------------

def test_a_package_serves_folder_files_made_on_another_machine(tmp_path, monkeypatch):
    from core import package_sources
    entries = {"file:///Users/someone/storm/recordings/storm.vehicles.20260506": b"packed"}
    monkeypatch.setattr(package_sources, "_entries", {u: {} for u in entries})
    monkeypatch.setattr(package_sources, "get", lambda url: entries.get(url))
    here = tmp_path / "recordings" / "storm.vehicles.20260506"
    assert package_sources.get_local(here, "storm.vehicles.20260506") == b"packed"
    assert package_sources.get_local(here, "storm.cones.20260506") is None
    entries["file:///elsewhere/storm.vehicles.20260506"] = b"other"
    monkeypatch.setattr(package_sources, "_entries", {u: {} for u in entries})
    assert package_sources.get_local(here, "storm.vehicles.20260506") is None    # ambiguous
