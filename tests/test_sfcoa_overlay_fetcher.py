from datetime import datetime, timezone

from data.fetchers import sfcoa_overlay_fetcher as sfcoa


def test_times_from_index_accepts_api_runs(monkeypatch):
    monkeypatch.setattr(
        sfcoa,
        "_fetch_json",
        lambda url: {"runs": ["20260507_15", "20260507_14", "not-a-run"]},
    )

    times = sfcoa._times_from_index("https://api.nssl.noaa.gov/data/sfcoa")

    assert [item.time_id for item in times] == ["20260507_15", "20260507_14"]
    assert times[0].label == "15:00Z"
    assert times[0].timestamp == datetime(2026, 5, 7, 15, tzinfo=timezone.utc)


def test_variables_from_metadata_uses_run_metadata(monkeypatch):
    seen_urls = []

    def fake_fetch_json(url):
        seen_urls.append(url)
        return {
            "variables": {
                "tmpc": {"units": "F"},
                "sbcp": {"units": "J kg^-1"},
                "custom": {"label": "Custom Field", "group": "upper"},
            }
        }

    monkeypatch.setattr(sfcoa, "_fetch_json", fake_fetch_json)

    variables = sfcoa._variables_from_metadata(
        "https://api.nssl.noaa.gov/data/sfcoa",
        "20260507_15",
    )

    by_id = {item.variable_id: item for item in variables}
    assert seen_urls == [
        "https://api.nssl.noaa.gov/data/sfcoa/20260507_15/metadata.json"
    ]
    assert by_id["tmpc"].label == "sfc tmp"
    assert by_id["tmpc"].units == "F"
    assert by_id["sbcp"].group == "parcel"
    assert by_id["custom"].label == "Custom Field"
    assert by_id["custom"].group == "upper"


def _cache_file(directory, name, size, age_s, now):
    import os
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age_s, now - age_s))
    return path


def test_prune_cache_drops_old_files_and_caps_size(monkeypatch, tmp_path):
    now = 1_000_000.0
    monkeypatch.setattr(sfcoa, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(sfcoa, "_CACHE_MAX_BYTES", 250)
    stale = _cache_file(tmp_path, "stale.mbtiles", 10, 25 * 3600, now)
    oldest = _cache_file(tmp_path, "a.mbtiles", 100, 3 * 3600, now)
    middle = _cache_file(tmp_path, "b.mbtiles", 100, 2 * 3600, now)
    newest = _cache_file(tmp_path, "c.mbtiles", 100, 1 * 3600, now)

    sfcoa._prune_cache(now=now)

    assert not stale.exists()
    assert not oldest.exists()
    assert middle.exists() and newest.exists()


def test_prune_cache_never_removes_kept_file(monkeypatch, tmp_path):
    now = 1_000_000.0
    monkeypatch.setattr(sfcoa, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(sfcoa, "_CACHE_MAX_BYTES", 50)
    kept = _cache_file(tmp_path, "shown.mbtiles", 100, 30 * 3600, now)
    other = _cache_file(tmp_path, "other.mbtiles", 10, 60, now)

    sfcoa._prune_cache(keep=kept, now=now)

    assert kept.exists()
    assert not other.exists()


def test_prune_cache_without_dir_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(sfcoa, "_CACHE_DIR", tmp_path / "missing")
    sfcoa._prune_cache()
