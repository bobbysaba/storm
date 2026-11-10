import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOJSON = ROOT / "static" / "cwa_boundaries.geojson"


def test_cwa_geojson_is_bundled_with_labels():
    data = json.loads(GEOJSON.read_text())
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 125
    wfos = {f["properties"]["WFO"] for f in data["features"]}
    assert {"OUN", "TSA", "FWD", "ABQ"} <= wfos
    for f in data["features"]:
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_scheme_handler_serves_geojson_mime():
    from ui.map.tile_scheme_handler import _mime_for
    assert _mime_for("cwa_boundaries.geojson") == b"application/geo+json"
