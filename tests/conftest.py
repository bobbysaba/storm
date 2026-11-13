"""Shared test fixtures."""
import pytest

# QtWebEngine must load before any QApplication exists, and several test
# modules create one at import, so whichever collects first would decide.
import PyQt6.QtWebEngineWidgets  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_fofs_index(monkeypatch, tmp_path):
    """No test reaches the live THREDDS catalog or the user's real index
    cache: loaders see no index (and fall back to each file's usual
    location, which tests fake). Tests that want an index install one."""
    from archive import fofs_index
    monkeypatch.setattr(fofs_index, "CACHE_PATH", tmp_path / "fofs_index.json")
    monkeypatch.setattr(fofs_index, "_current", None)
    monkeypatch.setattr(fofs_index, "get_index", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def isolated_archive_dates(monkeypatch, tmp_path):
    """The launch window's date scan never writes the user's real cache."""
    from archive import catalog
    monkeypatch.setattr(catalog, "_DATE_CACHE_PATH", tmp_path / "archive-dates.json")


LISTED_FAMILY = "Test listing"


@pytest.fixture
def listed_platform(monkeypatch):
    """A made-up source scanned from two flat THREDDS listings (processed
    .nc and raw .txt), for the generic listing scan the index keeps for
    instruments beyond the mesonet. Call it with a name for more than one."""
    from archive import catalog as cat
    real = cat.catalogs_for_platform

    def catalogs(platform):
        if platform.family == LISTED_FAMILY:
            base = f"TEST/{platform.key}"
            return (cat.CatalogSpec(f"{base}/processed", ".nc"), cat.CatalogSpec(f"{base}/raw", ".txt"))
        return real(platform)

    monkeypatch.setattr(cat, "catalogs_for_platform", catalogs)
    monkeypatch.setattr(cat, "_LATEST_OBSERVED_YEAR", {
        **cat._LATEST_OBSERVED_YEAR,
        "LIST-mg1": 2015, "LIST-farfield": 2022, "LIST-probe1": 2026, "LIST-dltruck": 2026,
    })
    return lambda name="probe1": cat.KnownPlatform(f"LIST-{name}", name, LISTED_FAMILY, name, site=name)


@pytest.fixture(autouse=True)
def isolated_clamps_listings():
    """CLAMPS folder listings are kept for the run; no test sees another's."""
    from archive.fetchers import clamps_common
    clamps_common.clear_listings()
    yield
    clamps_common.clear_listings()
