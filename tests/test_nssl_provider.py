"""The NSSL provider boundary (providers/nssl/): [providers.nssl] in
storm.toml turns it on, NSSL_API_KEY adds the API layers, and without it
STORM runs on public data alone."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import providers.nssl as nssl
import settings
from data.fetchers.surface_fetcher import SurfaceFetcher


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _load(tmp_path, text):
    path = tmp_path / "storm.toml"
    path.write_text(text)
    return settings.load(path)


# --- storm.toml --------------------------------------------------------------

def test_no_providers_by_default(tmp_path):
    assert settings.load(tmp_path / "storm.toml").providers == frozenset()
    assert _load(tmp_path, '[mqtt]\nhost = "h"\n').providers == frozenset()


def test_the_nssl_provider_is_turned_on_in_storm_toml(tmp_path):
    assert _load(tmp_path, "[providers.nssl]\nenabled = true\n").providers == {"nssl"}
    assert _load(tmp_path, "[providers.nssl]\nenabled = false\n").providers == frozenset()


@pytest.mark.parametrize("text, message", [
    ("[providers.acme]\nenabled = true\n", "no provider package"),
    ('[providers.nssl]\nenabled = "yes"\n', "true or false"),
    ("providers = 1\n", "must be a table"),
])
def test_a_bad_providers_table_is_a_settings_error(tmp_path, text, message):
    s = _load(tmp_path, text)
    assert message in s.error and s.providers == frozenset()


# --- what turns on -----------------------------------------------------------

def test_the_switch_turns_on_the_thredds_layers_and_the_key_the_api(monkeypatch):
    assert nssl.enabled() and nssl.api_enabled()          # conftest: on, with a fake key
    monkeypatch.delenv("NSSL_API_KEY")
    assert nssl.enabled() and not nssl.api_enabled()
    assert nssl.api_headers() == {}


def test_a_key_alone_turns_nothing_on(nssl_provider_off):
    assert os.environ["NSSL_API_KEY"] and not nssl.enabled() and not nssl.api_enabled()


def test_the_key_goes_to_the_api_only():
    from data import auth
    assert nssl.api_headers() == {"X-API-Key": "test-nssl-key"}
    assert auth.request(f"{nssl.API_ROOT}/current.json")[1] == {"X-API-Key": "test-nssl-key"}
    _, headers, context = auth.request("https://data.nssl.noaa.gov/thredds/fileServer/FOFS/x")
    assert headers == {} and context is not None          # THREDDS: the CA, no key
    assert auth.request("https://api.nssl.noaa.gov.example.com/x")[1] == {}


# --- surface obs ---------------------------------------------------------------

def test_generic_surface_obs_are_asos_only(app):
    fetcher = SurfaceFetcher()          # conftest: no [[sources]]
    assert fetcher.sources == {}
    assert fetcher._diagnostics_snapshot() == {}
    assert not fetcher._source_enabled("ok")


def test_the_nssl_mesonets_are_off_without_the_key(app, monkeypatch, tmp_path):
    from ui.controls.surface_controls import SurfaceControls
    monkeypatch.delenv("NSSL_API_KEY")
    sources = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml").sources_of("station_obs")
    controls = SurfaceControls(sources=sources)
    buttons = controls._source_buttons()
    assert list(buttons) == ["ok", "wtm", "ks", "co", "ne", "sd"]
    assert not any(b.isEnabled() for b in buttons.values())
    assert "NSSL_API_KEY" in buttons["ok"].toolTip()
    assert controls._btn_asos.isEnabled()
    # a session saved with the key doesn't turn them on
    controls.restore_state({"sources": ["ok", "ks"]})
    assert controls.save_state()["sources"] == []


# --- soundings -----------------------------------------------------------------

def test_the_nssl_sounding_source_hides_without_the_provider(app):
    from ui.sounding.controls import SoundingControls
    controls = SoundingControls()
    controls.set_provider_available(None)
    assert controls._btn_provider.isHidden() and controls._provider_div.isHidden()
    controls.set_provider_available("NSSL")
    assert not controls._btn_provider.isHidden() and controls._btn_provider.text() == "NSSL"


# --- built in: there without a table, out with enabled = false ----------------

def test_nssl_loads_without_a_table_but_isnt_a_team_source(tmp_path):
    s = _load(tmp_path, "[map]\nzoom = 5\n")
    assert [(p.name, p.builtin) for p in s.code_providers] == [("nssl", True)]
    # not one of the team's default [archive] history / vehicle_obs sources
    assert s.providers == frozenset()


def test_enabled_false_leaves_nssl_out(tmp_path):
    s = _load(tmp_path, "[providers.nssl]\nenabled = false\n")
    assert s.code_providers == () and s.providers == frozenset()


def test_the_providers_ca_covers_thredds_and_storm_toml_wins(monkeypatch, tmp_path):
    import dataclasses
    from data import auth
    builtin = settings.CodeProvider("nssl", settings.PROJECT_DIR / "providers" / "nssl", builtin=True)
    monkeypatch.setattr(settings, "SETTINGS", dataclasses.replace(settings.SETTINGS, auth=(),
                                                                  code_providers=(builtin,)))
    found = auth.for_url("https://data.nssl.noaa.gov/thredds/fileServer/FRDD/x.cdf")
    assert found is not None and found.ca_cert.name.startswith("sectigo")
    assert auth.for_url("https://api.nssl.noaa.gov/x") is None      # the key stays in the profile
    ca = tmp_path / "mine.pem"
    ca.write_text("x")
    mine = auth.Auth(url="https://data.nssl.noaa.gov", ca_cert=ca)
    monkeypatch.setattr(settings, "SETTINGS", dataclasses.replace(settings.SETTINGS, auth=(mine,)))
    assert auth.for_url("https://data.nssl.noaa.gov/thredds/x").ca_cert == ca
