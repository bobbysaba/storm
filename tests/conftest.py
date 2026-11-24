"""Shared test fixtures."""
import pytest

import qt_compat
qt_compat.use_windows_icu()

# QtWebEngine must load before any QApplication exists, and several test
# modules create one at import, so whichever collects first would decide.
import PyQt6.QtWebEngineWidgets  # noqa: F401


@pytest.fixture(autouse=True)
def test_api_keys(monkeypatch):
    """Tests never depend on the developer's local .env: the NSSL API key is
    a fixed fake, so header checks pass with or without a real key."""
    import config
    # what the NSSL profile's [[auth]] for its API reads
    monkeypatch.setenv("NSSL_API_KEY", "test-nssl-key")
    monkeypatch.setattr(config, "ORS_API_KEY", "test-ors-key")
    # base64(salt):base64(key) that no passphrase matches
    monkeypatch.setattr(config, "ADMIN_PASSPHRASE_HASH", "dGVzdC1zYWx0:dGVzdC1rZXk=")


@pytest.fixture(autouse=True)
def nssl_provider_on(monkeypatch):
    """Tests run with the NSSL provider on, with the NSSL profile's [[auth]]
    and vehicle aliases but otherwise the default [archive], [map] and
    [obs_columns] and no [[sources]], whatever the developer's storm.toml
    says; tests of the generic setup turn the provider off (nssl_provider_off)."""
    import dataclasses
    import settings
    from headless.storm_headless.obs_columns import FieldMap
    profile = _nssl_profile()
    nssl = settings.CodeProvider("nssl", settings.PROJECT_DIR / "providers" / "nssl")
    monkeypatch.setattr(settings, "SETTINGS",
                        dataclasses.replace(settings.SETTINGS, providers=frozenset({"nssl"}),
                                            code_providers=(nssl,),
                                            archive=settings.ArchiveSettings(
                                                vehicle_aliases=profile.archive.vehicle_aliases),
                                            map=settings.MapSettings(),
                                            obs_layouts=(("default", FieldMap()),), sources=(),
                                            auth=profile.auth))


_profile = None


def _nssl_profile():
    global _profile
    if _profile is None:
        import settings
        _profile = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml")
        assert not _profile.error, _profile.error
    return _profile


@pytest.fixture
def nssl_provider_off(monkeypatch):
    import dataclasses
    import settings
    monkeypatch.setattr(settings, "SETTINGS",
                        dataclasses.replace(settings.SETTINGS, providers=frozenset(), code_providers=(),
                                            auth=(), archive=settings.ArchiveSettings()))


@pytest.fixture(autouse=True)
def isolated_fofs_index(monkeypatch, tmp_path):
    """No test reaches NSSL's published index or the user's real index
    cache: loaders see no index (and fall back to each file's usual
    location, which tests fake). Tests that want an index install one."""
    from providers.nssl import indexes
    monkeypatch.setattr(indexes, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(indexes.FOFS, "get", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def isolated_archive_dates(monkeypatch, tmp_path):
    """The launch window's date scan never writes the user's real cache."""
    from archive import catalog
    monkeypatch.setattr(catalog, "_DATE_CACHE_PATH", tmp_path / "archive-dates.json")


LISTED_FAMILY = "Test listing"

# the scan-order hints the listed platforms below carry (newest first)
_LISTED_LATEST_YEAR = {"mg1": 2015, "farfield": 2022, "probe1": 2026, "dltruck": 2026}


@pytest.fixture
def listed_platform():
    """A made-up source scanned from two flat THREDDS listings (processed
    .nc and raw .txt), for the generic listing scan the index keeps for
    instruments beyond the mesonet. Call it with a name for more than one."""
    from archive import catalog as cat
    from providers.nssl.archive import catalog as ncat

    def platform(name="probe1"):
        base = f"TEST/{name}"
        return cat.KnownPlatform(f"LIST-{name}", name, LISTED_FAMILY, name, site=name,
                                 catalogs=(ncat.CatalogSpec(f"{base}/processed", ".nc"),
                                           ncat.CatalogSpec(f"{base}/raw", ".txt")),
                                 latest_year=_LISTED_LATEST_YEAR.get(name))
    return platform


@pytest.fixture(autouse=True)
def isolated_clamps_listings():
    """CLAMPS folder listings are kept for the run; no test sees another's."""
    from providers.nssl.archive import clamps_common
    clamps_common.clear_listings()
    yield
    clamps_common.clear_listings()
