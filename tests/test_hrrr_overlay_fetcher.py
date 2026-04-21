from data.fetchers.hrrr_overlay_fetcher import _payload_from_catalog, _short_label


def test_payload_from_live_thredds_catalog_shape():
    catalog = {
        "cycle_time": "2026-04-20T18:00:00Z",
        "run": "20260420_18",
        "forecast_hours": [0, 1, 3],
        "fields": {
            "refc": "Composite Reflectivity",
            "tmp2m": "2 m Temperature",
        },
    }

    payload = _payload_from_catalog(
        catalog,
        "refc",
        "20260420_17",
        {"20260420_17": [0, 2]},
    )

    assert payload["cycle"] == "17"
    assert payload["cycle_time"] == "2026-04-20T17:00:00Z"
    assert payload["field"] == "refc"
    assert payload["label"] == "Composite Reflectivity"
    assert payload["run"] == "20260420_17"
    assert payload["items"] == [
        {
            "forecast_hour": 0,
            "metadata": "20260420_17/f00/metadata.json",
            "image_png": "20260420_17/f00/refc.png",
            "image_webp": "20260420_17/f00/refc.webp",
            "grid_bin": "20260420_17/f00/refc.bin",
        },
        {
            "forecast_hour": 2,
            "metadata": "20260420_17/f02/metadata.json",
            "image_png": "20260420_17/f02/refc.png",
            "image_webp": "20260420_17/f02/refc.webp",
            "grid_bin": "20260420_17/f02/refc.bin",
        },
    ]


def test_short_label_abbreviates_updraft_helicity():
    assert _short_label("0-2 km Max Updraft Helicity") == "0-2 km Max UH"
