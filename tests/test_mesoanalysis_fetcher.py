import json
import sys
import types


if "PyQt6.QtCore" not in sys.modules:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    def pyqtSignal(*args, **kwargs):
        class Signal:
            def emit(self, *args, **kwargs):
                pass
        return Signal()

    qtcore.QObject = QObject
    qtcore.pyqtSignal = pyqtSignal
    sys.modules.setdefault("PyQt6", pyqt6)
    sys.modules.setdefault("PyQt6.QtCore", qtcore)

from data.fetchers.mesoanalysis_fetcher import (
    MESOANALYSIS_PRODUCTS,
    MesoanalysisTime,
    _clip_to_mbtiles_bounds,
    _parse_bounds,
    _product_units,
    _source_layer,
    _time_label,
)


def test_curated_mesoanalysis_products_match_requested_list():
    assert [product_id for product_id, _label in MESOANALYSIS_PRODUCTS] == [
        "temp",
        "stpc",
        "srh01",
        "srh03",
        "sfclfc",
        "sfclcl",
        "sfccape",
        "sfccinh",
        "scp",
        "mulfc",
        "mulcl",
        "mucinh",
        "mucape",
        "mslp",
        "mllfc",
        "mllcl",
        "mlcinh",
        "mlcape",
        "dwpt",
        "cape03",
    ]


def test_metadata_helpers_parse_satsquatch_shape():
    metadata = {
        "bounds": "-128.948400,21.301900,-68.847900,42.120400",
        "json": json.dumps({
            "vector_layers": [
                {"id": "mlcape_20260420_200000geojson"},
            ],
        }),
    }

    assert _parse_bounds(metadata["bounds"]) == [
        -128.9484,
        21.3019,
        -68.8479,
        42.1204,
    ]
    assert _source_layer(metadata, "mlcape", "20260420_200000") == (
        "mlcape_20260420_200000geojson"
    )
    assert _time_label("20260420_200000") == "20:00Z"


def test_recent_times_are_latest_four_when_sliced_like_fetcher():
    times = [
        MesoanalysisTime(time_id=f"20260420_{hour:02d}0000", label=f"{hour:02d}:00Z")
        for hour in range(12, 18)
    ]

    assert [item.time_id for item in times[-4:]] == [
        "20260420_140000",
        "20260420_150000",
        "20260420_160000",
        "20260420_170000",
    ]


def test_bounds_are_clipped_to_mbtiles_domain(monkeypatch):
    monkeypatch.setattr(
        "data.fetchers.mesoanalysis_fetcher._load_mbtiles_bounds",
        lambda: (-104.0, 28.0, -88.0, 39.0),
    )

    assert _clip_to_mbtiles_bounds([-128.9484, 21.3019, -68.8479, 42.1204]) == [
        -104.0,
        28.0,
        -88.0,
        39.0,
    ]


def test_product_units_cover_label_formatting_groups():
    assert _product_units("temp") == "degF"
    assert _product_units("mlcape") == "J/kg"
    assert _product_units("srh03") == "m2/s2"
    assert _product_units("stpc") == ""
