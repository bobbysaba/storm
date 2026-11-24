"""kind = "contours" [[sources]] (NSSL: SFCOA): parsing, the fetcher
(data/fetchers/contour_fetcher.py), serving its tiles, and the drawer."""
import json
import os
import sqlite3
import tomllib

import pytest
from PyQt6.QtWidgets import QApplication

import settings
from core import pmtiles
from data.fetchers import contour_fetcher as cf
from data.sources import SourceError, code_source, parse_sources
from tests.test_pmtiles import write as write_pmtiles

TABLE = '''
[[sources]]
id = "meso"
kind = "contours"
name = "Mesoanalysis"
short = "MESO"
url = "https://api.example.org/data/meso/"
'''
BASE = "https://api.example.org/data/meso"


def _sources(text):
    return parse_sources(tomllib.loads(text).get("sources", []), settings.PROJECT_DIR)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


# --- parsing -------------------------------------------------------------------

def test_a_minimal_contours_source():
    (src,) = _sources(TABLE)
    assert (src.kind, src.where, src.icon, src.tiles, src.run_format) == \
        ("contours", "toolbar", "contours", "pmtiles", "%Y%m%d_%H")
    (src,) = _sources(TABLE + 'tiles = "mbtiles"\nrun_format = "%Y-%m-%dT%H%M"\nicon = "map"\n')
    assert (src.tiles, src.run_format, src.icon) == ("mbtiles", "%Y-%m-%dT%H%M", "map")


@pytest.mark.parametrize("extra, message", [
    ('tiles = "geojson"\n', "tiles must be one of pmtiles, mbtiles"),
    ('run_format = "latest"\n', "needs date codes"),
    ('where = "drawer"\n', "unknown key where"),
    ('refresh_min = 5\n', "unknown key refresh_min"),
    ('icon = "kites"\n', "needs an icon"),
    (TABLE.replace('"meso"', '"meso2"'), "only one contours source"),
])
def test_bad_contours_tables(extra, message):
    with pytest.raises(SourceError, match=message):
        _sources(TABLE + extra)


def test_a_code_provider_cant_make_a_contours_source():
    with pytest.raises(SourceError, match="kind must be one of"):
        code_source("contours", "x", lambda: None)


def test_the_nssl_profile_has_sfcoa_as_a_contours_source():
    profile = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml")
    (src,) = profile.sources_of("contours")
    assert (src.id, src.short, src.url) == ("sfcoa", "SFCOA", "https://api.nssl.noaa.gov/data/sfcoa")


# --- the fetcher ---------------------------------------------------------------

def test_runs_read_with_the_run_format_oldest_first():
    (src,) = _sources(TABLE)
    runs = cf.runs_from_index(src, {"runs": ["20260507_15", "20260507_14", "not-a-run", 7]})
    assert [r.time_id for r in runs] == ["20260507_14", "20260507_15"]
    assert runs[1].label == "15:00Z" and runs[1].timestamp.hour == 15


def test_variables_take_labels_from_metadata_or_their_id():
    variables = cf.variables_from_metadata({"variables": {
        "tmpc": {"label": "sfc tmp", "group": "thermo", "units": "C", "long_name": "Surface temperature"},
        "xyz": {"units": "m"},
    }})
    by_id = {v.variable_id: v for v in variables}
    assert by_id["tmpc"] == cf.ContourVariable("tmpc", "sfc tmp", "Surface temperature", "C", "thermo")
    assert (by_id["xyz"].label, by_id["xyz"].group) == ("xyz", "other")


class _Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.data


def _serve(monkeypatch, files):
    """Answer urlopen from *files* ({url: bytes}); returns the requests made."""
    seen = []

    def urlopen(req, timeout=None, context=None):
        seen.append(req)
        return _Response(files[req.full_url])
    monkeypatch.setattr(cf, "urlopen", urlopen)
    return seen


def _mbtiles(path, tile: bytes):
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE metadata (name TEXT, value TEXT);"
                       "CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB);")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("bounds", "-105,30,-90,40"), ("minzoom", "3"), ("maxzoom", "7"),
        ("json", json.dumps({"vector_layers": [{"id": "tmpc"}]}))])
    conn.execute("INSERT INTO tiles VALUES (3, 1, 4, ?)", (tile,))   # XYZ 3/1/3
    conn.commit()
    conn.close()


@pytest.mark.parametrize("tiles", ["pmtiles", "mbtiles"])
def test_a_field_is_fetched_cached_and_described(monkeypatch, tmp_path, app, tiles):
    monkeypatch.setattr(cf, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("MESO_KEY", "s3cret")
    (src,) = _sources(TABLE + f'tiles = "{tiles}"\napi_key_env = "MESO_KEY"\n')
    tile_file = tmp_path / f"tmpc.{tiles}"
    if tiles == "pmtiles":
        write_pmtiles(tile_file, {(3, 1, 3): b"pbf"}, metadata={"vector_layers": [{"id": "tmpc"}]},
                      zooms=(3, 7))
    else:
        _mbtiles(tile_file, b"pbf")
    meta = {"bounds": [-120, 20, -60, 50], "variables": {"tmpc": {"label": "sfc tmp", "units": "C"}}}
    seen = _serve(monkeypatch, {
        f"{BASE}/20260507_15/metadata.json": json.dumps(meta).encode(),
        f"{BASE}/20260507_15/tmpc.{tiles}": tile_file.read_bytes(),
    })
    fetcher = cf.ContourFetcher(src)
    ready, errors = [], []
    fetcher.overlay_ready.connect(ready.append)
    fetcher.fetch_error.connect(errors.append)

    fetcher._fetch_overlay_worker("tmpc", "20260507_15")
    fetcher._fetch_overlay_worker("tmpc", "20260507_15")

    assert errors == []
    # the tile file once: the second time comes from the cache
    assert [r.full_url.rsplit("/", 1)[1] for r in seen] == ["metadata.json", f"tmpc.{tiles}", "metadata.json"]
    assert all(r.get_header("X-api-key") == "s3cret" for r in seen)
    out = ready[-1]
    assert out["tile_url"] == "storm://app/contourtiles/meso-20260507_15-tmpc/{z}/{x}/{y}.pbf"
    assert (out["label"], out["units"], out["source_layer"]) == ("sfc tmp", "C", "tmpc")
    assert out["bounds"] == pytest.approx([-105, 30, -90, 40])   # the tile file's own
    assert (out["minzoom"], out["maxzoom"], out["valid_time"]) == (3, 7, "2026-05-07T15:00:00Z")
    assert out["tile_path"].endswith(f"meso-20260507_15-tmpc.{tiles}")

    from ui.map.tile_scheme_handler import StormSchemeHandler
    served = out["tile_path"] if tiles == "mbtiles" else pmtiles.PMTilesFile(out["tile_path"])
    assert StormSchemeHandler._contour_tile(served, 3, 1, 3) == b"pbf"
    assert StormSchemeHandler._contour_tile(served, 3, 0, 0) is None


def test_a_missing_index_is_reported(monkeypatch, app):
    (src,) = _sources(TABLE)
    _serve(monkeypatch, {f"{BASE}/index.json": b'{"runs": []}'})
    fetcher = cf.ContourFetcher(src)
    errors = []
    fetcher.fetch_error.connect(errors.append)
    fetcher._fetch_times_worker()
    assert errors == ["index: index.json lists no runs"]


def _cache_file(directory, name, size, age_s, now):
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age_s, now - age_s))
    return path


def test_prune_cache_drops_old_files_and_caps_size(monkeypatch, tmp_path):
    now = 1_000_000.0
    monkeypatch.setattr(cf, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cf, "_CACHE_MAX_BYTES", 250)
    stale = _cache_file(tmp_path, "stale.pmtiles", 10, 25 * 3600, now)
    oldest = _cache_file(tmp_path, "a.pmtiles", 100, 3 * 3600, now)
    middle = _cache_file(tmp_path, "b.pmtiles", 100, 2 * 3600, now)
    newest = _cache_file(tmp_path, "c.pmtiles", 100, 1 * 3600, now)

    cf._prune_cache(now=now)

    assert not stale.exists() and not oldest.exists()
    assert middle.exists() and newest.exists()


def test_prune_cache_never_removes_kept_file(monkeypatch, tmp_path):
    now = 1_000_000.0
    monkeypatch.setattr(cf, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cf, "_CACHE_MAX_BYTES", 50)
    kept = _cache_file(tmp_path, "shown.pmtiles", 100, 30 * 3600, now)
    other = _cache_file(tmp_path, "other.pmtiles", 10, 60, now)

    cf._prune_cache(keep=kept, now=now)

    assert kept.exists() and not other.exists()


# --- the drawer ----------------------------------------------------------------

def test_fields_in_other_groups_still_get_buttons(app):
    from ui.controls.contour_controls import ContourControls
    drawer = ContourControls()
    drawer.set_products([cf.ContourVariable("tmpc", "sfc tmp", group="thermo"),
                         cf.ContourVariable("xyz", "xyz"),
                         cf.ContourVariable("ozone", "ozone", group="chemistry")])
    assert set(drawer._product_buttons) == {"tmpc", "xyz", "ozone"}


def test_fields_keep_the_order_metadata_lists_them_in(app):
    from ui.controls.contour_controls import ContourControls
    variables = cf.variables_from_metadata({"variables": {
        "sbcp": {"group": "parcel"}, "dwpc": {"group": "thermo"}, "tmpc": {"group": "thermo"}}})
    assert [v.variable_id for v in variables] == ["sbcp", "dwpc", "tmpc"]
    drawer = ContourControls()
    drawer.set_products(variables)
    grid = drawer._product_grid
    def at(row, col):
        item = grid.itemAtPosition(row, col)
        return item.widget().text() if item else None
    # column 0 is thermo (header, then its fields as listed), column 1 parcel
    assert [at(1, 0), at(2, 0), at(1, 1)] == ["dwpc", "tmpc", "sbcp"]
