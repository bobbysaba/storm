"""The map right-click menu's contents per launch mode and feature (no Qt)."""
import pytest

from ui.session_state import ARCHIVE, MONITOR, VEHICLE, VIEWER
from ui.widgets.map_context_menu import (
    DRAW_TYPES, PLACE_TYPES, MenuContext, build_menu, build_track_menu, find,
)

RAOB = {"station_id": "OUN", "name": "Norman", "lat": 35.2, "lon": -97.4,
        "elev": 357, "distance_km": 12.0}
SITES = (("KTLX", 20.0), ("KVNX", 140.0), ("KINX", 190.0), ("KFDR", 200.0))


def _ctx(mode=VEHICLE, **kw):
    base = dict(lat=35.0, lon=-97.0, mode=mode, hrrr=True, raob=RAOB,
                radar_sites=SITES, annotations=True, cones=True)
    base.update(kw)
    return MenuContext(**base)


def _actions(items):
    out = []
    for item in items:
        if item.action:
            out.append(item.action)
        out.extend(_actions(item.children))
    return out


def _labels(items):
    return [i.label for i in items if not i.is_separator]


def test_empty_map_in_vehicle_mode_has_every_point_item():
    items = build_menu(_ctx())
    assert _labels(items) == [
        "Sounding here", "Route to here", "Route from here", "Measure from here",
        "Place annotation", "Draw", "Nearest radar site", "Add private pin",
        "Copy 35.00000, -97.00000",
    ]
    sounding = next(i for i in items if i.label == "Sounding here")
    assert [c.action for c in sounding.children] == ["sounding_hrrr", "sounding_raob"]
    place = next(i for i in items if i.label == "Place annotation")
    assert [c.args[0] for c in place.children] == list(PLACE_TYPES)
    draw = next(i for i in items if i.label == "Draw")
    assert [c.args[0] for c in draw.children] == list(DRAW_TYPES)
    assert "polygon" in DRAW_TYPES and "cold_front" in DRAW_TYPES


def test_nearest_radar_sites_lists_the_closest_three():
    radar = next(i for i in build_menu(_ctx()) if i.label == "Nearest radar site")
    assert [c.args for c in radar.children] == [("KTLX",), ("KVNX",), ("KINX",)]
    assert radar.children[0].label.startswith("KTLX")


def test_clamps_only_when_in_range_and_live():
    assert find(build_menu(_ctx(clamps=True)), "sounding_clamps") is not None
    assert find(build_menu(_ctx()), "sounding_clamps") is None
    assert find(build_menu(_ctx(ARCHIVE, clamps=True)), "sounding_clamps") is None


def test_no_sounding_menu_without_a_source():
    items = build_menu(_ctx(hrrr=False, raob=None))
    assert "Sounding here" not in _labels(items)


def test_viewer_has_no_routing():
    actions = _actions(build_menu(_ctx(VIEWER)))
    assert "route_to" not in actions and "route_from" not in actions
    # viewers can still annotate locally, as from the toolbar
    assert "annotation_place" in actions and "measure_from" in actions


@pytest.mark.parametrize("mode", [MONITOR, ARCHIVE])
def test_routing_kept_where_the_route_button_is(mode):
    actions = _actions(build_menu(_ctx(mode)))
    assert "route_to" in actions and "route_from" in actions


def test_archive_has_no_annotation_tools():
    actions = _actions(build_menu(_ctx(ARCHIVE)))
    assert "annotation_place" not in actions and "draw_from" not in actions


def test_annotation_tools_need_the_annotation_service():
    actions = _actions(build_menu(_ctx(annotations=False)))
    assert "annotation_place" not in actions and "draw_from" not in actions


def test_hazard_offers_open_text_and_keeps_point_items():
    features = ({"source": "nws-warnings", "properties": {"event": "Tornado Warning"}},)
    items = build_menu(_ctx(feature={"kind": "hazard", "features": list(features)}))
    assert items[0].action == "title"
    assert items[1].action == "hazard_text" and items[1].args == (features,)
    assert items[2].is_separator
    assert "measure_from" in _actions(items)


@pytest.mark.parametrize("kind,prefix", [
    ("annotation", "annotation"), ("drawing", "drawing"), ("cone", "cone"),
])
def test_map_objects_offer_edit_move_delete(kind, prefix):
    items = build_menu(_ctx(feature={"kind": kind, "id": "abc", "type_key": "debris"}))
    assert [i.action for i in items[1:4]] == [f"{prefix}_edit", f"{prefix}_move", f"{prefix}_delete"]
    assert all(i.args == ("abc",) for i in items[1:4])


def test_fork_cannot_be_moved():
    items = build_menu(_ctx(feature={"kind": "annotation", "id": "f", "type_key": "fork"}))
    assert "annotation_move" not in _actions(items)
    assert "annotation_delete" in _actions(items)


@pytest.mark.parametrize("kind", ["annotation", "drawing", "cone"])
def test_map_objects_are_read_only_in_archive(kind):
    items = build_menu(_ctx(ARCHIVE, feature={"kind": kind, "id": "abc"}))
    assert items[0].action != "title"


def test_vehicle_items_follow_what_is_possible():
    items = build_menu(_ctx(feature={"kind": "vehicle", "id": "STORM1"},
                            vehicle_followable=True, vehicle_timeseries=True))
    assert [i.action for i in items[1:4]] == ["vehicle_follow", "vehicle_center", "vehicle_timeseries"]
    items = build_menu(_ctx(feature={"kind": "vehicle", "id": "STORM2"}))
    assert items[1].action == "vehicle_center" and items[2].is_separator


def test_surface_station_offers_nearest_raob():
    items = build_menu(_ctx(feature={"kind": "surface_station", "id": "surface:asos:KOKC",
                                     "label": "KOKC", "lat": 35.4, "lon": -97.6}))
    assert items[0].label == "KOKC"
    assert items[1].label == "Sounding from nearest RAOB" and items[1].args == ("OUN",)


def test_track_menu_without_a_marker_offers_only_to_add_one():
    items = build_track_menu(_ctx(ARCHIVE), None)
    assert _actions(items) == ["track_marker_add"]
    assert items[0].args == (35.0, -97.0)


def test_track_menu_on_a_point_with_a_marker():
    items = build_track_menu(_ctx(ARCHIVE, feature={"kind": "track_point", "id": 4}), "M1")
    assert _actions(items) == ["title", "track_point_select", "track_point_delete",
                               "track_marker_add", "track_marker_rename", "track_marker_remove"]
    assert find(items, "track_point_delete").args == (4,)
    assert find(items, "track_marker_add").label == "Move marker here"


def test_track_menu_on_the_marker_names_it():
    items = build_track_menu(_ctx(ARCHIVE, feature={"kind": "track_marker"}), "meso A")
    assert items[0].label == "Marker meso A" and items[0].action == "title"
