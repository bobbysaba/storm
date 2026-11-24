"""[[sources]] in storm.toml (data/sources.py): parsing, reading a
station_obs response, overlays, and [obs_columns.units]."""
import json
import tomllib
from datetime import datetime, timezone

import pytest

import settings
from data.fetchers.overlay_fetcher import OverlayError, feature_collection
from data.sources import SourceError, get_path, parse_sources, read_station_obs, read_stations
from headless.storm_headless.obs_columns import ObsColumnsError, parse_obs_columns

ROWS = '''
[[sources]]
id = "acme"
kind = "station_obs"
name = "Acme Mesonet"
short = "ACME"
url = "https://acme.test/obs.json"
'''


def _sources(text):
    return parse_sources(tomllib.loads(text).get("sources", []), settings.PROJECT_DIR)


def _one(text):
    return _sources(text)[0]


# --- parsing -------------------------------------------------------------------

def test_a_minimal_station_obs_source():
    src = _one(ROWS + 'fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\n')
    assert (src.id, src.kind, src.name, src.short) == ("acme", "station_obs", "Acme Mesonet", "ACME")
    assert (src.shape, src.format, src.where, src.refresh_min, src.fresh_min) == \
        ("rows", "json", "drawer", 5.0, (5.0, 10.0))
    assert src.missing_key() == "" and src.request(src.url) == (src.url, {})


def test_settings_reads_sources(tmp_path):
    path = tmp_path / "storm.toml"
    path.write_text(ROWS + 'fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\n'
                    '\n[[sources]]\nid = "roads"\nkind = "overlay"\nurl = "https://x.test/r.geojson"\n')
    s = settings.load(path)
    assert s.error == ""
    assert [x.id for x in s.sources_of("station_obs")] == ["acme"]
    assert [x.id for x in s.sources_of("overlay")] == ["roads"]


@pytest.mark.parametrize("extra, message", [
    ('fields = { lat = "lat", lon = "lon", temperature_c = "t" }', "needs a station field"),
    ('fields = { station = "id", lat = "lat", temperature_c = "t" }', "needs a lon field"),
    ('fields = { station = "id", lat = "lat", lon = "lon" }', "has no readings"),
    ('fields = { station = "id", lat = "lat", lon = "lon", station_pressure_mb = "p" }',
     "needs an elevation_m field"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temp = "t" }', "unknown field temp"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\nshape = "grid"',
     "shape must be one of"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\ntimezone = "Mars/Base"',
     "not a time zone"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\nunits = { temperature_c = "R" }',
     "must be one of c, f, k"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\nwhere = "toolbar"',
     "needs an icon"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\napi_key_param = "token"',
     "need api_key_env"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\nfresh_min = [10, 5]',
     "fresh_min"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\nstation_fields = { lat = "y" }',
     "needs stations_url"),
    ('fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\nstyle = {}',
     "unknown key style"),
])
def test_bad_station_obs_tables(extra, message):
    with pytest.raises(SourceError, match=message):
        _one(ROWS + extra + "\n")


@pytest.mark.parametrize("text, message", [
    ('[[sources]]\nkind = "overlay"\nurl = "https://x.test"\n', "id is required"),
    ('[[sources]]\nid = "Roads"\nkind = "overlay"\nurl = "https://x.test"\n', "lower-case"),
    ('[[sources]]\nid = "asos"\nkind = "overlay"\nurl = "https://x.test"\n', "lower-case"),
    ('[[sources]]\nid = "r"\nkind = "radar"\nurl = "https://x.test"\n', "kind must be one of"),
    ('[[sources]]\nid = "r"\nkind = "overlay"\nurl = "ftp://x.test"\n', "http"),
    ('[[sources]]\nid = "r"\nkind = "overlay"\nurl = "https://x.test"\n'
     '[[sources]]\nid = "r"\nkind = "overlay"\nurl = "https://y.test"\n', "used twice"),
    ('[[sources]]\nid = "r"\nkind = "overlay"\nurl = "https://x.test"\nstyle = { color = "red" }\n', "hex color"),
    ('[[sources]]\nid = "r"\nkind = "overlay"\nurl = "https://x.test"\nstyle = { glow = 1 }\n', "unknown key glow"),
    ('[[sources]]\nid = "r"\nkind = "overlay"\nurl = "https://x.test"\nshape = "rows"\n', "unknown key shape"),
])
def test_bad_tables(text, message):
    with pytest.raises(SourceError, match=message):
        _sources(text)


def test_a_bad_source_is_a_settings_error(tmp_path):
    path = tmp_path / "storm.toml"
    path.write_text('[[sources]]\nid = "r"\nkind = "radar"\nurl = "https://x.test"\n')
    s = settings.load(path)
    assert "kind must be one of" in s.error and s.sources == ()


def test_api_keys_from_the_environment(monkeypatch):
    src = _one(ROWS + 'api_key_env = "ACME_KEY"\n'
               'fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\n')
    monkeypatch.delenv("ACME_KEY", raising=False)
    assert src.missing_key() == "ACME_KEY" and src.request(src.url) == (src.url, {})
    monkeypatch.setenv("ACME_KEY", "s3cret")
    assert src.missing_key() == "" and src.request(src.url) == (src.url, {"X-API-Key": "s3cret"})

    param = _one(ROWS + 'api_key_env = "ACME_KEY"\napi_key_param = "token"\n'
                 'fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\n')
    assert param.request("https://acme.test/obs.json?x=1") == ("https://acme.test/obs.json?x=1&token=s3cret", {})


# --- reading -------------------------------------------------------------------

def test_get_path():
    data = {"a": {"b": [{"c": 1}]}, "TA [C]": 2, "x.y": 3}
    assert get_path(data, "a.b.0.c") == 1
    assert get_path(data, "TA [C]") == 2
    assert get_path(data, "x.y") == 3
    assert get_path(data, "a.zz") is None and get_path(data, "") is data


def test_a_csv_feed_in_local_time_and_us_units():
    src = _one(ROWS + '''format = "csv"
time_format = "%Y-%m-%d %H:%M"
timezone = "America/Chicago"
missing = ["M", -99]
fields = { station = "STN", name = "Name", lat = "Lat", lon = "Lon", time = "Time", temperature_c = "TempF", dewpoint_c = "DewF", wind_speed_ms = "WindKt", wind_dir_deg = "Dir", pressure_mb = "Altim" }
units = { temperature_c = "F", dewpoint_c = "F", wind_speed_ms = "kt", pressure_mb = "inHg" }
''')
    from data.sources import decode
    rows = decode(b"STN,Name,Lat,Lon,Time,TempF,DewF,WindKt,Dir,Altim\n"
                  b"Farm 1,Farm One,35.2,-97.4,2026-05-20 14:05,86,M,20,180,29.92\n"
                  b"Farm 2,,35.3,-97.5,2026-05-20 14:05,-99,60,5,-99,30.00\n"
                  b"nowhere,,0,0,2026-05-20 14:05,70,60,5,90,30.00\n", "csv")
    items = read_station_obs(src, rows)
    assert [i["id"] for i in items] == ["surface:acme:farm_1", "surface:acme:farm_2"]
    one, two = (i["obs"] for i in items)
    assert items[0]["name"] == "Farm One" and items[1]["name"] == "FARM_2"
    assert one.timestamp == datetime(2026, 5, 20, 19, 5, tzinfo=timezone.utc)   # CDT
    assert one.temperature_c == pytest.approx(30.0) and one.dewpoint_c is None
    assert one.wind_speed_ms == pytest.approx(10.29, abs=0.01)
    assert one.pressure_mb == pytest.approx(1013.2, abs=0.1)
    assert two.temperature_c is None and two.wind_dir_deg is None
    assert two.dewpoint_c == pytest.approx(15.6, abs=0.1)


def test_keyed_rows_with_positions_from_a_station_list():
    src = _one(ROWS + '''shape = "keyed"
rows = "obs"
fields = { temperature_c = "t", time = "when" }
stations_url = "https://acme.test/stations.json"
stations_shape = "keyed"
station_fields = { lat = "pos.0", lon = "pos.1", name = "label" }
''')
    stations = read_stations({"A1": {"pos": [35.0, -97.0], "label": "Alpha"}}, src.stations)
    data = {"obs": {"A1": {"t": 20, "when": 1779300000}, "B2": {"t": 21}}}
    items = read_station_obs(src, data, stations, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert [i["id"] for i in items] == ["surface:acme:a1"]     # B2 has no position
    assert items[0]["name"] == "Alpha"
    assert items[0]["obs"].timestamp == datetime.fromtimestamp(1779300000, tz=timezone.utc)


def test_an_unexpected_response_is_an_error():
    src = _one(ROWS + 'rows = "results"\nfields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }\n')
    with pytest.raises(ValueError, match="no list of rows at results"):
        read_station_obs(src, {"data": []})


# --- overlays ------------------------------------------------------------------

def test_an_overlay_source():
    src = _one('[[sources]]\nid = "targets"\nkind = "overlay"\nname = "Target areas"\nshort = "TGT"\n'
               'url = "https://x.test/t.geojson"\nwhere = "toolbar"\nicon = "map"\n'
               'style = { color = "#ff8800", width = 3, fill_opacity = 0.2, label = "name" }\n')
    assert (src.kind, src.refresh_min, src.where, src.icon) == ("overlay", 10.0, "toolbar", "map")
    assert src.style == {"color": "#ff8800", "width": 3, "fill_opacity": 0.2, "label": "name"}


def test_geojson_becomes_a_feature_collection():
    point = {"type": "Point", "coordinates": [-97, 35]}
    assert feature_collection(point)["features"][0]["geometry"] == point
    feature = {"type": "Feature", "geometry": point, "properties": {"name": "x"}}
    assert feature_collection(feature)["features"] == [feature]
    fc = {"type": "FeatureCollection", "features": [feature]}
    assert feature_collection(fc) is fc
    with pytest.raises(OverlayError):
        feature_collection({"type": "Topology"})
    with pytest.raises(OverlayError):
        feature_collection([1, 2])


def test_the_map_page_has_the_overlay_functions():
    html = (settings.PROJECT_DIR / "ui" / "map" / "map_template.html").read_text("utf-8")
    assert "window.stormSetSourceOverlay = function" in html
    assert "window.stormSetSourceOverlayVisible = function" in html


# --- [obs_columns.units] ---------------------------------------------------------

def test_obs_columns_units_convert_logger_files():
    from archive.vehicle_obs import parse_obs_csv
    fields = parse_obs_columns({"timestamp": "time", "temperature_c": "temp", "dewpoint_c": "",
                                "wind_speed_ms": "wspd", "pressure_mb": "",
                                "units": {"temperature_c": "F", "wind_speed_ms": "mph"}})
    assert fields.units == (("temperature_c", "f"), ("wind_speed_ms", "mph"))
    obs = parse_obs_csv("time,lat,lon,temp,wspd,sfc_wdir,compass_dir\n"
                        "2026-05-20T20:00:00Z,35,-97,50,10,180,90\n", "p1", None, fields)
    assert obs[0].temperature_c == pytest.approx(10.0)
    assert obs[0].wind_speed_ms == pytest.approx(4.4704)


@pytest.mark.parametrize("units, message", [
    ({"heading_deg": "rad"}, "not a field with units"),
    ({"temperature_c": "R"}, "must be one of"),
])
def test_bad_obs_columns_units(units, message):
    with pytest.raises(ObsColumnsError, match=message):
        parse_obs_columns({"units": units})


def test_headless_reads_units_too(tmp_path):
    from headless.storm_headless.obs_tail import ObsFileTail
    fields = parse_obs_columns({"units": {"temperature_c": "F"}})
    tail = ObsFileTail(str(tmp_path), "p1", field_map=fields)
    obs = tail._parse_row({"lat": "35", "lon": "-97", "time": "2026-05-20T20:00:00Z",
                           "temperature_c": "212"})
    assert obs.temperature_c == pytest.approx(100.0)


# --- display -------------------------------------------------------------------

def test_fresh_minutes_by_source():
    from ui import palette as P
    from core.observation import Observation
    from ui.layers.surface_plot_layer import SurfacePlotLayer, set_fresh_minutes
    set_fresh_minutes("slow", (30.0, 60.0))
    now = datetime(2026, 5, 20, 20, 0, tzinfo=timezone.utc)
    obs = Observation(vehicle_id="x", lat=35, lon=-97, timestamp=datetime(2026, 5, 20, 19, 40, tzinfo=timezone.utc))
    assert SurfacePlotLayer._obs_age_color(obs, "surface:slow:a", now) == P.OK
    assert SurfacePlotLayer._obs_age_color(obs, "surface:other:a", now) == P.ERROR
    assert SurfacePlotLayer._obs_age_color(obs, "surface:asos:KOKC", now) == P.OK


def _check_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_data_sources", settings.PROJECT_DIR / "scripts" / "check_data_sources.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_check_script_probes_the_settings_sources_only():
    mod = _check_script()
    assert mod.source_endpoints() == []              # conftest: no [[sources]]
    profile = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml").sources
    urls = [u for _, u in mod.source_endpoints(profile)]
    assert "https://api.nssl.noaa.gov/data/mesonet/ok_mesonet.json" in urls
    assert "https://api.nssl.noaa.gov/data/sfcoa/index.json" in urls     # a contours source's index
    assert len(urls) == len(set(urls))
    assert json.dumps(urls)


def test_the_check_script_asks_a_server_with_its_auth_table():
    mod = _check_script()
    request, context = mod._request("https://api.nssl.noaa.gov/current.json")     # conftest: NSSL's [[auth]]
    assert request.get_header("X-api-key") == "test-nssl-key" and context is not None
    request, context = mod._request("https://thredds.ucar.edu/thredds/catalog")
    assert request.get_header("X-api-key") is None and context is None
