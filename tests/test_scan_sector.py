"""Tests for core.scan_sector geometry and serialization."""

from datetime import datetime, timezone

from core.scan_sector import ScanSector, feature_collection


def _scan(**kwargs):
    base = {
        "vehicle_id": "WX1",
        "active": True,
        "mode": "circle",
        "lat": 35.0,
        "lon": -97.0,
        "timestamp": datetime(2026, 4, 17, 2, 0, tzinfo=timezone.utc),
        "range_m": 1000,
    }
    base.update(kwargs)
    return ScanSector(**base)


class TestScanSector:
    def test_point_builds_point_feature(self):
        scan = _scan(mode="point", range_m=None)
        gj = scan.build_geojson()
        feat = gj["features"][0]
        assert feat["geometry"]["type"] == "Point"
        assert feat["properties"]["mode"] == "point"

    def test_circle_builds_closed_polygon(self):
        scan = _scan(mode="circle")
        feat = scan.build_geojson()["features"][0]
        ring = feat["geometry"]["coordinates"][0]
        assert feat["geometry"]["type"] == "Polygon"
        assert ring[0] == ring[-1]
        assert len(ring) >= 72

    def test_circle_inner_range_builds_hole(self):
        scan = _scan(mode="circle", inner_range_m=250)
        rings = scan.build_geojson()["features"][0]["geometry"]["coordinates"]
        assert len(rings) == 2
        assert rings[0][0] == rings[0][-1]
        assert rings[1][0] == rings[1][-1]

    def test_sector_builds_wedge(self):
        scan = _scan(mode="sector", azimuth_deg=270, beam_width_deg=60)
        ring = scan.build_geojson()["features"][0]["geometry"]["coordinates"][0]
        assert ring[0] == [-97.0, 35.0]
        assert ring[0] == ring[-1]

    def test_sector_inner_range_builds_hollow_wedge(self):
        scan = _scan(
            mode="sector",
            inner_range_m=100,
            azimuth_deg=180,
            beam_width_deg=40,
        )
        ring = scan.build_geojson()["features"][0]["geometry"]["coordinates"][0]
        assert ring[0] != [-97.0, 35.0]
        assert ring[0] == ring[-1]

    def test_inactive_has_no_features(self):
        scan = _scan(active=False)
        assert scan.build_geojson()["features"] == []

    def test_roundtrip(self):
        scan = _scan(mode="sector", inner_range_m=50, azimuth_deg=90, beam_width_deg=30)
        restored = ScanSector.from_dict(scan.to_dict())
        assert restored.vehicle_id == scan.vehicle_id
        assert restored.mode == "sector"
        assert restored.inner_range_m == 50
        assert restored.azimuth_deg == 90

    def test_feature_collection_combines_active_features(self):
        fc = feature_collection([_scan(vehicle_id="A"), _scan(vehicle_id="B", active=False)])
        assert len(fc["features"]) == 1
