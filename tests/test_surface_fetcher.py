from datetime import datetime, timezone
import sys
import types

if "PyQt6" not in sys.modules:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _Signal:
        def connect(self, *_args, **_kwargs):
            pass

        def emit(self, *_args, **_kwargs):
            pass

    class _QObject:
        def __init__(self, *_args, **_kwargs):
            pass

    class _QTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = _Signal()

        def setInterval(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    qtcore.QObject = _QObject
    qtcore.QTimer = _QTimer
    qtcore.pyqtSignal = lambda *_args, **_kwargs: _Signal()
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore

from pathlib import Path

import pytest

import settings
from data.fetchers import surface_fetcher as sf
from data.fetchers.surface_fetcher import SurfaceFetcher
from data.sources import station_to_mslp

NSSL_PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "nssl.toml"
API = "https://api.nssl.noaa.gov/data/mesonet"
KEY = {"X-API-Key": "test-nssl-key"}


@pytest.fixture
def nssl_fetcher():
    """A SurfaceFetcher with the NSSL profile's mesonets, and its requests:
    fetch(key, {url: body}) -> (items, [(url, headers, has an ssl context)])."""
    sources = settings.load(NSSL_PROFILE).sources_of("station_obs")
    fetcher = SurfaceFetcher(sources=sources)

    def fetch(key, bodies):
        seen = []

        def _http_get(url, ssl_ctx=None, headers=None):
            seen.append((url, headers, ssl_ctx is not None))
            return bodies[url]

        fetcher._http_get = _http_get
        return fetcher._fetch_station_obs(fetcher.sources[key]), seen

    return fetch


def test_asos_station_cache_uses_bundled_data_file():
    assert sf._ASOS_STATIONS_FILE.name == "asos_stations.json"
    assert sf._ASOS_STATIONS_FILE.parent.name == "data"


def test_asos_roster_refreshes_into_the_user_cache(tmp_path, monkeypatch):
    import json, os, time
    shipped = tmp_path / "shipped.json"
    shipped.write_text(json.dumps({"KOLD": {"lat": 1, "lon": 2, "name": "old"}}))
    cache = tmp_path / "cache" / "asos_stations.json"
    monkeypatch.setattr(sf, "_ASOS_STATIONS_FILE", shipped)
    monkeypatch.setattr(sf, "_ASOS_STATIONS_CACHE", cache)
    fresh = {"KNEW": {"lat": 3, "lon": 4, "name": "new"}}
    calls = []
    monkeypatch.setattr(sf, "_fetch_asos_station_roster", lambda: calls.append(1) or fresh)
    assert sf.load_asos_station_roster() == fresh and cache.exists()
    assert sf.load_asos_station_roster() == fresh and len(calls) == 1     # cache is fresh
    old = time.time() - sf.ASOS_ROSTER_MAX_AGE_S - 60
    os.utime(cache, (old, old))

    def offline():
        raise sf.SurfaceFetchError("offline")
    monkeypatch.setattr(sf, "_fetch_asos_station_roster", offline)
    assert sf.load_asos_station_roster() == fresh       # stale copy beats nothing
    cache.unlink()
    assert sf.load_asos_station_roster() == {"KOLD": {"lat": 1, "lon": 2, "name": "old"}}


def test_fetch_iem_batch_uses_utc_valid_timestamp():
    fetcher = SurfaceFetcher()
    fetcher._http_get = lambda _url: b"""{
      "data": [{
        "station": "OKC",
        "name": "OKLAHOMA CITY",
        "utc_valid": "2026-04-18T23:52:00Z",
        "tmpf": "68",
        "dwpf": "50",
        "sknt": "10",
        "drct": "180",
        "mslp": "1012.3",
        "lat": "35.3889",
        "lon": "-97.6006"
      }]
    }"""

    rows = fetcher._fetch_iem_batch(
        ["OKC"],
        {"OKC": {"lat": 35.3889, "lon": -97.6006, "name": "OKLAHOMA CITY"}},
    )

    assert len(rows) == 1
    obs = rows[0]["obs"]
    assert rows[0]["id"] == "surface:asos:OKC"
    assert obs.timestamp == datetime(2026, 4, 18, 23, 52, tzinfo=timezone.utc)


def test_the_nssl_profile_has_the_six_mesonets():
    s = settings.load(NSSL_PROFILE)
    assert s.error == ""
    assert [src.id for src in s.sources_of("station_obs")] == ["ok", "wtm", "ks", "co", "ne", "sd"]
    # the key and CA come from the profile's [[auth]] for the API
    api = next(a for a in s.auth if a.covers(f"{API}/ok_mesonet.json"))
    assert api.api_key_env == "NSSL_API_KEY" and api.ca_cert.is_file()
    assert all(src.url.startswith(api.url) and not src.api_key_env for src in s.sources_of("station_obs"))


def test_ok_mesonet(nssl_fetcher):
    stations = (b"STID,NAME,LAT,LON,PRES\n"
                b"ACME,Acme,34.80833,-98.02325,1008.4\n"
                b"ADAX,Ada,34.79851,-96.66909,1011.2\n")
    data = b"""{
      "time": "2026-04-23T19:25:00+00:00",
      "data": {
        "tair": {"acme": 26.55},
        "tdew": {"acme": 20.11},
        "wspd": {"acme": 11.17},
        "wdir": {"acme": 183.7},
        "pres": {"acme": 958.08}
      }
    }"""
    stations_url = "https://www.mesonet.org/data/public/mesonet/current/current.csv.txt"
    rows, seen = nssl_fetcher("ok", {stations_url: stations, f"{API}/ok_mesonet.json": data})

    # the key and CA are the API's ([[auth]]): the public station list gets neither
    assert seen == [(stations_url, {}, False), (f"{API}/ok_mesonet.json", KEY, True)]
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:ok:acme"
    assert rows[0]["name"] == "Acme"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 4, 23, 19, 25, tzinfo=timezone.utc)
    assert (obs.temperature_c, obs.dewpoint_c, obs.wind_speed_ms, obs.wind_dir_deg) == (26.55, 20.11, 11.17, 183.7)
    # sea-level pressure from the station list, not the station pressure
    assert obs.pressure_mb == 1008.4


def test_wtm_reduces_station_pressure_with_the_site_elevation(nssl_fetcher):
    sites = b"""{"results": [{"mesonet_id": "TEST", "latitude": 32.0, "longitude": -101.0,
                              "elevation": 3000, "name": "Test WTM"}]}"""
    data = b"""{
      "results": [{
        "mid": "test",
        "utc": "2026-04-23T19:25:00+00:00",
        "temp1p5m": 22.1,
        "dp1p5m": 10.2,
        "wspd10m": 8.3,
        "wdir10m": 190.0,
        "pres": 905.2
      }]
    }"""
    rows, _ = nssl_fetcher("wtm", {"https://api.mesonet.ttu.edu/mesoweb/sites/": sites,
                                   f"{API}/wtx_mesonet.json": data})
    assert rows[0]["id"] == "surface:wtm:test"
    assert rows[0]["name"] == "Test WTM"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 4, 23, 19, 25, tzinfo=timezone.utc)
    # 3000 ft = 914.4 m
    assert round(obs.pressure_mb, 1) == round(station_to_mslp(905.2, 914.4, 22.1), 1)
    assert 1000 < obs.pressure_mb < 1020


def test_ks_mesonet_dewpoint_from_rh_and_slp_in_kpa(nssl_fetcher):
    data = b"""{
      "time": "2026-05-07T19:10:00+00:00",
      "results": [{
        "station": "Alma 5SE",
        "timestamp": "2026-05-07T19:05:00+00:00",
        "TEMP2MAVG": "22.1",
        "RELHUM2MAVG": "50.0",
        "PRESSUREAVG": "96.32",
        "SLPAVG": "101.32",
        "WDIR10M": "212.74",
        "WSPD10MAVG": "9.54",
        "WSPD10MMAX": "12.58",
        "PRECIP": "0.0",
        "meta": {
          "name": "Alma 5SE",
          "abbr": "Alma 5SE",
          "lat": 38.96615,
          "lon": -96.2063,
          "elevation": 428.0
        }
      }]
    }"""
    rows, seen = nssl_fetcher("ks", {f"{API}/ks_mesonet.json": data})

    assert seen == [(f"{API}/ks_mesonet.json", KEY, True)]
    assert rows[0]["id"] == "surface:ks:alma_5se"
    assert rows[0]["source"] == "ks"
    assert rows[0]["name"] == "Alma 5SE"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 5, 7, 19, 5, tzinfo=timezone.utc)
    assert obs.temperature_c == 22.1
    assert round(obs.dewpoint_c, 1) == 11.2
    assert obs.wind_speed_ms == 9.54
    assert obs.wind_dir_deg == 212.74
    assert round(obs.pressure_mb, 1) == 1013.2


def test_co_mesonet_keyed_rows_in_us_units(nssl_fetcher):
    meta = b"""{
      "alt01": {
        "station": "alt01",
        "name": "Ault",
        "lat": 40.569,
        "lon": -104.7195
      }
    }"""
    data = b"""{
      "which": "qc",
      "timezone": "utc",
      "units": "us",
      "stations": ["alt01"],
      "alt01": {
        "time": "2026-05-20T17:45",
        "t": 52.63,
        "rh": 0.645,
        "dewpt": 41.0,
        "windSpeed": 3.6,
        "windDir": 148.2,
        "gustSpeed": -999
      }
    }"""
    rows, seen = nssl_fetcher("co", {f"{API}/co_metadata.json": meta, f"{API}/co_mesonet.json": data})

    assert seen == [(f"{API}/co_metadata.json", KEY, True), (f"{API}/co_mesonet.json", KEY, True)]
    assert len(rows) == 1
    assert rows[0]["id"] == "surface:co:alt01"
    assert rows[0]["name"] == "Ault"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 5, 20, 17, 45, tzinfo=timezone.utc)
    assert round(obs.temperature_c, 1) == 11.5
    assert round(obs.dewpoint_c, 1) == 5.0
    assert round(obs.wind_speed_ms, 2) == 1.61
    assert obs.wind_dir_deg == 148.2
    assert obs.pressure_mb is None


def test_ne_mesonet_falls_back_to_the_3_m_wind(nssl_fetcher):
    data = b"""{
      "data": [{
        "id": 54,
        "name": "Holdrege 5N",
        "latitude": 40.5045,
        "longitude": -99.360083,
        "timestamp": "2026-05-20T20:21:00.000000Z",
        "data": {
          "temperature": {"twoMeter": 12.91},
          "dewPoint": {"twoMeter": 6.43},
          "pressure": {"seaLevel": 1023.71},
          "wind": {
            "threeMeter": {
              "speed": {"avg": 2.4851},
              "direction": {"avg": 65.2785}
            },
            "tenMeter": {
              "speed": {"avg": null},
              "direction": {"avg": null}
            }
          }
        }
      }]
    }"""
    rows, _ = nssl_fetcher("ne", {f"{API}/ne_mesonet.json": data})
    assert rows[0]["id"] == "surface:ne:54"
    assert rows[0]["name"] == "Holdrege 5N"
    obs = rows[0]["obs"]
    assert obs.timestamp == datetime(2026, 5, 20, 20, 21, tzinfo=timezone.utc)
    assert (obs.temperature_c, obs.dewpoint_c, obs.pressure_mb) == (12.91, 6.43, 1023.71)
    assert (obs.wind_speed_ms, obs.wind_dir_deg) == (2.4851, 65.2785)


def test_sd_mesonet_top_level_list(nssl_fetcher):
    data = b"""[{
      "nwsid": "BRKS2",
      "name": "Brookings",
      "lat": 44.31,
      "lon": -96.79,
      "elev [m]": 500,
      "date_time [ISO8601]": "2026-05-20T20:20:00Z",
      "TA [C]": 18.2,
      "TD [C]": 9.1,
      "US [m/s]": 4.2,
      "UD [deg]": 300,
      "PA [mbar]": -999
    }]"""
    rows, _ = nssl_fetcher("sd", {f"{API}/sd_mesonet.json": data})
    assert rows[0]["id"] == "surface:sd:brks2"
    obs = rows[0]["obs"]
    assert (obs.temperature_c, obs.dewpoint_c, obs.wind_speed_ms, obs.wind_dir_deg) == (18.2, 9.1, 4.2, 300.0)
    assert obs.pressure_mb is None


def test_a_station_list_is_read_once_per_stations_refresh_min(nssl_fetcher):
    meta = b"""{"alt01": {"station": "alt01", "name": "Ault", "lat": 40.569, "lon": -104.7195}}"""
    data = b"""{"stations": ["alt01"], "alt01": {"time": "2026-05-20T17:45", "t": 52.6}}"""
    bodies = {f"{API}/co_metadata.json": meta, f"{API}/co_mesonet.json": data}
    nssl_fetcher("co", bodies)
    _, seen = nssl_fetcher("co", bodies)
    assert [url for url, _, _ in seen] == [f"{API}/co_mesonet.json"]


def test_no_rows_is_an_error(nssl_fetcher):
    with pytest.raises(sf.SurfaceFetchError, match="no station rows"):
        nssl_fetcher("ne", {f"{API}/ne_mesonet.json": b'{"data": []}'})


def test_sources_are_fetched_when_due(nssl_fetcher):
    fetcher = SurfaceFetcher(sources=settings.load(NSSL_PROFILE).sources_of("station_obs"))
    calls = []
    fetcher._fetch_station_obs = lambda src: calls.append(src.id) or []
    fetcher._networks_enabled["ks"] = True
    fetcher._start = lambda force: None
    fetcher._force = True
    fetcher._fetch_worker()
    fetcher._fetch_worker()          # a minute later: not due yet
    assert calls == ["ks"]
    fetcher._force = True            # fetch_now(): due or not
    fetcher._fetch_worker()
    assert calls == ["ks", "ks"]


def test_diagnostics_snapshot_tracks_valid_and_fetch_times():
    fetcher = SurfaceFetcher(sources=settings.load(NSSL_PROFILE).sources_of("station_obs"))
    attempt = datetime(2026, 4, 23, 19, 30, tzinfo=timezone.utc)
    valid = datetime(2026, 4, 23, 19, 25, tzinfo=timezone.utc)

    class _Obs:
        timestamp = valid

    fetcher._networks_enabled["ok"] = True
    fetcher._update_source_diag("ok", "OK", attempt, [{"obs": _Obs()}], False, None)

    snap = fetcher._diagnostics_snapshot()
    assert snap["ok"]["label"] == "OK"
    assert snap["ok"]["last_attempt"] == attempt
    assert snap["ok"]["last_success"] == attempt
    assert snap["ok"]["valid_time"] == valid
    assert snap["ok"]["count"] == 1
    assert snap["ok"]["stale"] is False
