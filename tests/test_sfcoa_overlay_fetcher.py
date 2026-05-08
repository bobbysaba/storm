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
