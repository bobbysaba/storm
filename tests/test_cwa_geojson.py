import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEOJSON = ROOT / "static" / "cwa_boundaries.geojson"


def _features():
    data = json.loads(GEOJSON.read_text())
    assert data["type"] == "FeatureCollection"
    areas = [f for f in data["features"] if f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
    points = [f for f in data["features"] if f["geometry"]["type"] == "Point"]
    assert len(areas) + len(points) == len(data["features"])
    return areas, points


def test_cwa_geojson_is_bundled_with_labels():
    areas, _points = _features()
    assert len(areas) == 125
    wfos = {f["properties"]["WFO"] for f in areas}
    assert {"OUN", "TSA", "FWD", "ABQ"} <= wfos


def test_one_label_point_per_office_inside_its_area():
    shapely = pytest.importorskip("shapely.geometry")
    areas, points = _features()
    assert sorted(p["properties"]["WFO"] for p in points) == sorted(a["properties"]["WFO"] for a in areas)
    by_wfo = {a["properties"]["WFO"]: shapely.shape(a["geometry"]) for a in areas}
    for p in points:
        assert by_wfo[p["properties"]["WFO"]].contains(shapely.Point(p["geometry"]["coordinates"]))


def test_label_point_uses_the_largest_part():
    geometry = pytest.importorskip("shapely.geometry")
    sys.path.insert(0, str(ROOT / "scripts"))
    from make_cwa_geojson import label_point
    small = geometry.Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    big = geometry.Polygon([(10, 30), (14, 30), (14, 34), (10, 34)])
    lon, lat = label_point(geometry.MultiPolygon([small, big]))
    assert 10 < lon < 14 and 30 < lat < 34


def test_scheme_handler_serves_geojson_mime():
    from ui.map.tile_scheme_handler import _mime_for
    assert _mime_for("cwa_boundaries.geojson") == b"application/geo+json"
