"""[[auth]] (data/auth.py) and the small settings that replaced NSSL's hooks:
[mqtt] shared_items_url, [map] deployment_locations, [archive] vehicle_aliases."""

import dataclasses

import pytest

import settings
from data import auth, deployment_locations
from data.auth import Auth


def _load(tmp_path, text):
    path = tmp_path / "storm.toml"
    path.write_text(text)
    return settings.load(path)


_TABLE = '[[auth]]\nurl = "https://api.example.org/v1/"\napi_key_env = "EXAMPLE_KEY"\nca_cert = "certs/x.pem"\n'


def test_an_auth_table_is_read(tmp_path):
    (a,) = _load(tmp_path, _TABLE).auth
    assert a == Auth(url="https://api.example.org/v1", api_key_env="EXAMPLE_KEY",
                     ca_cert=settings.PROJECT_DIR / "certs" / "x.pem")


@pytest.mark.parametrize("text, message", [
    ('[[auth]]\napi_key_env = "K"\n', "url must be"),
    ('[[auth]]\nurl = "ftp://x.org"\napi_key_env = "K"\n', "url must be"),
    ('[[auth]]\nurl = "https://x.org"\n', "api_key_env, ca_cert or both"),
    ('[[auth]]\nurl = "https://x.org"\napi_key_param = "token"\n', "need api_key_env"),
    ('[[auth]]\nurl = "https://x.org"\napi_key_env = "K"\nheader = "X"\n', "unknown key header"),
    ('[[auth]]\nurl = "https://x.org"\napi_key_env = "K"\n[[auth]]\nurl = "https://x.org/"\napi_key_env = "J"\n',
     "listed twice"),
])
def test_bad_auth_tables_are_settings_errors(tmp_path, text, message):
    assert message in _load(tmp_path, text).error


def test_the_longest_match_wins_and_only_below_its_url(monkeypatch):
    monkeypatch.setenv("WIDE", "w")
    monkeypatch.setenv("NARROW", "n")
    tables = (Auth(url="https://h.org", api_key_env="WIDE"),
              Auth(url="https://h.org/private", api_key_env="NARROW", api_key_param="token"))
    assert auth.request("https://h.org/public/x", tables=tables)[1] == {"X-API-Key": "w"}
    assert auth.request("https://h.org/private/x?a=1", tables=tables)[:2] == (
        "https://h.org/private/x?a=1&token=n", {})
    for elsewhere in ("https://h.org.evil.com/x", "https://h.organic/x", "https://other.org/x"):
        assert auth.request(elsewhere, tables=tables) == (elsewhere, {}, None)


def test_a_source_without_its_own_key_uses_the_auth_table(monkeypatch):
    from data.sources import parse_sources
    monkeypatch.setenv("EXAMPLE_KEY", "s3cret")
    monkeypatch.setattr(settings, "SETTINGS", dataclasses.replace(
        settings.SETTINGS, auth=(Auth(url="https://api.example.org", api_key_env="EXAMPLE_KEY"),)))
    (src,) = parse_sources([{"id": "x", "kind": "overlay", "url": "https://api.example.org/a.geojson"}],
                           settings.PROJECT_DIR)
    assert src.request(src.url) == (src.url, {"X-API-Key": "s3cret"})
    assert src.request("https://stations.example.com/list.csv") == ("https://stations.example.com/list.csv", {})
    monkeypatch.delenv("EXAMPLE_KEY")
    assert src.missing_key() == "EXAMPLE_KEY"


def test_shared_items_url_is_mqtts_and_the_viewer_shares_it(tmp_path):
    s = _load(tmp_path, '[mqtt]\nhost = "b.org"\nshared_items_url = "https://api.org/current.json"\n')
    assert s.mqtt.shared_items_url == s.viewer_mqtt.shared_items_url == "https://api.org/current.json"
    assert "can't set shared_items_url" in _load(
        tmp_path, '[mqtt]\nhost = "b.org"\n[mqtt.viewer]\nshared_items_url = "https://x.org"\n').error
    assert "http(s) URL" in _load(tmp_path, '[mqtt]\nshared_items_url = "current.json"\n').error


def test_deployment_locations_are_read_from_a_csv(tmp_path):
    s = _load(tmp_path, '[map]\ndeployment_locations = "providers/nssl/locs/deployment_locations.csv"\n')
    points = deployment_locations.read(s.map.deployment_locations)
    assert len(points) > 1000 and any(p["rank_abi"] is not None for p in points)
    assert deployment_locations.parse("lat,lon,rqi\n35,-97,0.5\nx,-97,\n36,-98,\n") == [
        {"lat": 35.0, "lon": -97.0, "rank_abi": None, "rank_aoi": None, "rqi": 0.5},
        {"lat": 36.0, "lon": -98.0, "rank_abi": None, "rank_aoi": None, "rqi": None},
    ]


def test_vehicle_aliases_name_the_files_both_ways(tmp_path):
    a = _load(tmp_path, '[archive]\nvehicle_aliases = { P1 = "Probe1" }\n').archive
    assert a.file_vehicle("p1") == "probe1" and a.broker_vehicle("probe1") == "p1"
    assert a.file_vehicle("wx1") == "wx1" and a.broker_vehicle("wx1") == "wx1"
    assert "share a file name" in _load(
        tmp_path, '[archive]\nvehicle_aliases = { p1 = "probe1", p9 = "probe1" }\n').error


def test_a_folder_source_finds_a_vehicles_files_by_its_alias(tmp_path, monkeypatch):
    from datetime import date
    from archive import vehicle_obs
    from headless.storm_headless.obs_columns import FieldMap
    (tmp_path / "probe1").mkdir()
    (tmp_path / "probe1" / "20260506.csv").write_text(
        "time,lat,lon\n2026-05-06T20:00:00Z,35.0,-97.0\n")
    monkeypatch.setattr(settings, "SETTINGS", dataclasses.replace(
        settings.SETTINGS, archive=settings.ArchiveSettings(vehicle_aliases=(("p1", "probe1"),))))
    source = vehicle_obs.FolderVehicleObsSource(tmp_path, "{vehicle}/{date}.csv", FieldMap())
    assert source.vehicles(date(2026, 5, 6)) == {"p1"}
    assert source.relative("p1", date(2026, 5, 6)) == "probe1/20260506.csv"
