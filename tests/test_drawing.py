"""Tests for core.drawing — DrawingAnnotation model and type lookups."""

from core.drawing import (
    DrawingAnnotation,
    FRONT_TYPES, CUSTOM_TYPES, ALL_DRAWING_TYPES,
    DRAWING_TYPE_MAP, FRONT_TYPE_KEYS, CUSTOM_TYPE_KEYS,
)


class TestDrawingTypes:
    def test_all_types_is_union(self):
        assert len(ALL_DRAWING_TYPES) == len(FRONT_TYPES) + len(CUSTOM_TYPES)

    def test_keys_unique(self):
        keys = [t["key"] for t in ALL_DRAWING_TYPES]
        assert len(keys) == len(set(keys))

    def test_front_and_custom_disjoint(self):
        assert FRONT_TYPE_KEYS & CUSTOM_TYPE_KEYS == set()

    def test_map_contains_all(self):
        for t in ALL_DRAWING_TYPES:
            assert t["key"] in DRAWING_TYPE_MAP

    def test_known_fronts(self):
        expected = {"cold_front", "warm_front", "stationary_front", "occluded_front", "dryline"}
        assert FRONT_TYPE_KEYS == expected

    def test_known_custom(self):
        expected = {"polyline", "polygon"}
        assert CUSTOM_TYPE_KEYS == expected


class TestDrawingAnnotation:
    def test_new_factory_default_title(self):
        coords = [[-97.0, 35.0], [-96.5, 35.5]]
        da = DrawingAnnotation.new("cold_front", coords)
        assert da.drawing_type == "cold_front"
        assert da.title == "Cold Front"
        assert da.coordinates == coords
        assert da.flipped is False
        assert len(da.id) == 8

    def test_new_factory_custom_title(self):
        da = DrawingAnnotation.new("polyline", [], title="My line")
        assert da.title == "My line"

    def test_flipped(self):
        da = DrawingAnnotation.new("warm_front", [], flipped=True)
        assert da.flipped is True

    def test_to_dict_roundtrip(self):
        coords = [[-97.0, 35.0], [-96.0, 36.0]]
        da = DrawingAnnotation.new("dryline", coords, title="DL", flipped=True)
        d = da.to_dict()
        restored = DrawingAnnotation.from_dict(d)
        assert restored.id == da.id
        assert restored.drawing_type == "dryline"
        assert restored.title == "DL"
        assert restored.coordinates == coords
        assert restored.flipped is True

    def test_from_dict_defaults(self):
        d = {
            "id": "abc",
            "drawing_type": "polygon",
        }
        da = DrawingAnnotation.from_dict(d)
        assert da.coordinates == []
        assert da.title == ""
        assert da.creator == "unknown"
        assert da.flipped is False
