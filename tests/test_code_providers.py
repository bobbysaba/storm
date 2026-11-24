"""Code providers: a group's own package, loaded by [providers.<name>]
(settings/code_providers.py), adding sources and archive sources."""
import textwrap
from datetime import datetime, timezone

import pytest

import settings
from data.fetchers.overlay_fetcher import fetch_overlay
from data.fetchers.source_sounding_fetcher import fetch_sounding_set
from data.sources import SourceError, code_source, read_station_obs
from settings import code_providers

PACKAGE = '''
from datetime import datetime, timezone
from data.sources import code_source

def _obs():
    return [{"station": "A1", "lat": 35.0, "lon": -97.0, "temperature_c": 20.0,
             "time": datetime(2026, 5, 20, 21, tzinfo=timezone.utc)}]

def _shape():
    return {"type": "Point", "coordinates": [-97.0, 35.0]}

def _sondes():
    levels = [{"pressure_mb": 1000 - 100 * i, "height_m": 100 + 1000 * i,
               "temperature_c": 25 - 6 * i, "dewpoint_c": 15 - 8 * i,
               "wind_dir_deg": 180, "wind_speed_ms": 10} for i in range(6)]
    return [{"time": "2026-05-20T21:00:00Z", "lat": 35.0, "lon": -97.0, "levels": levels}]

def sources(options):
    return [
        code_source("station_obs", "acme_obs", _obs, name=options.get("label", "Acme")),
        code_source("overlay", "acme_area", _shape, archive=True),
        code_source("sounding", "acme_sonde", _sondes, short="ACME"),
        "not a source",
    ]

class History:
    name = "acme history"
    def fetch(self, kind, date_str):
        return None

def history_source(options):
    return History()
'''


@pytest.fixture(autouse=True)
def _fresh_loader(monkeypatch):
    monkeypatch.setattr(code_providers, "_modules", {})
    monkeypatch.setattr(code_providers, "_sources", {})
    monkeypatch.setattr(code_providers, "errors", {})


def _package(folder, body=PACKAGE):
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(textwrap.dedent(body))
    return folder


def _settings(tmp_path, extra=""):
    pkg = _package(tmp_path / "acme_pkg")
    path = tmp_path / "storm.toml"
    path.write_text(f'[providers.acme]\nenabled = true\npath = "{pkg}"\nlabel = "Acme Net"\n{extra}')
    return settings.load(path)


def test_a_code_provider_adds_its_sources(tmp_path):
    s = _settings(tmp_path)
    assert s.error == "" and s.providers == {"acme"}
    provider, builtin = s.code_providers
    assert (builtin.name, builtin.builtin) == ("nssl", True)
    assert provider.options_dict() == {"label": "Acme Net"}
    obs = s.sources_of("station_obs")
    assert [(x.id, x.name, x.provider) for x in obs] == [("acme_obs", "Acme Net", "acme")]
    assert [x.id for x in s.sources_of("overlay")] == ["acme_area"]
    assert s.sources_of("overlay")[0].archive is True
    assert [x.id for x in s.sources_of("sounding")] == ["acme_sonde"]


def test_its_sources_read_through_storms_readers(tmp_path):
    s = _settings(tmp_path)
    [obs] = s.sources_of("station_obs")
    [item] = read_station_obs(obs, obs.fetch())
    assert item["id"] == "surface:acme_obs:a1" and item["obs"].temperature_c == 20.0
    [area] = s.sources_of("overlay")
    assert fetch_overlay(area)["type"] == "FeatureCollection"
    [sonde] = s.sources_of("sounding")
    sset = fetch_sounding_set(sonde, now=datetime(2026, 5, 20, 22, tzinfo=timezone.utc))
    assert sset.source == "source:acme_sonde" and len(sset.soundings[0].pressure) == 6


def test_a_config_source_keeps_its_id(tmp_path):
    s = _settings(tmp_path, '\n[[sources]]\nid = "acme_area"\nkind = "overlay"\n'
                            'url = "https://x.test/a.geojson"\n')
    overlays = s.sources_of("overlay")
    assert [(x.id, x.provider) for x in overlays] == [("acme_area", "")]


def test_a_provider_folder_in_providers_is_the_default(tmp_path):
    path = tmp_path / "storm.toml"
    path.write_text("[providers.no_such_group]\nenabled = true\n")
    s = settings.load(path)
    assert "no provider package at" in s.error
    assert str(settings.PROJECT_DIR / "providers" / "no_such_group") in s.error


def test_a_broken_provider_is_left_out(tmp_path):
    pkg = _package(tmp_path / "broken", "raise RuntimeError('boom')\n")
    path = tmp_path / "storm.toml"
    path.write_text(f'[providers.broken]\nenabled = true\npath = "{pkg}"\n')
    s = settings.load(path)
    assert s.error == "" and s.sources_of("overlay") == ()
    assert "boom" in code_providers.errors["broken"]


def test_its_history_source_is_listed_by_name(tmp_path, monkeypatch):
    from archive import history
    s = _settings(tmp_path)
    monkeypatch.setattr(settings, "SETTINGS", s)
    sources = history.build_sources(("acme",))
    assert [x.name for x in sources] == ["acme history"]
    # without [archive] history: the providers that are on, then recordings
    names = [x.name for x in history.build_sources()]
    assert names[0] == "acme history" and names[-1].startswith("folder ")


@pytest.mark.parametrize("call, message", [
    (lambda: code_source("radar", "x", lambda: None), "kind must be"),
    (lambda: code_source("overlay", "x", None), "must be a function"),
    (lambda: code_source("overlay", "Bad Id", lambda: None), "lower-case"),
    (lambda: code_source("sounding", "x", lambda: None, where="toolbar"), "unknown option where"),
    (lambda: code_source("overlay", "x", lambda: None, style={"color": "red"}), "hex color"),
])
def test_code_source_checks_its_options(call, message):
    with pytest.raises(SourceError, match=message):
        call()
