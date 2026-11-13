from ui.map.widget import MapWidget


class _FakeMapWidget:
    def __init__(self):
        self.scripts = []

    def run_js(self, script):
        self.scripts.append(script)


def test_set_sfcoa_overlay_uses_supplied_tile_url_directly():
    widget = _FakeMapWidget()

    MapWidget.set_sfcoa_overlay(
        widget,
        "tmpc",
        "storm://app/sfcoatiles/20260507_15-tmpc/{z}/{x}/{y}.pbf",
        "tmpc_20260507_15",
        -116,
        28,
        -82,
        49,
        3,
        9,
        "F",
    )

    assert len(widget.scripts) == 1
    assert "stormSetSfcoaOverlay" in widget.scripts[0]
    assert "storm://app/sfcoatiles/20260507_15-tmpc/{z}/{x}/{y}.pbf" in widget.scripts[0]


def test_add_vehicle_passes_multiline_hover_text():
    widget = _FakeMapWidget()

    MapWidget.add_vehicle(
        widget,
        "p1",
        35.0,
        -97.0,
        "#ffffff",
        "mesonet",
        "p1\n1s 25 mph | 15s avg 23 mph",
    )

    assert len(widget.scripts) == 1
    assert "stormAddVehicle" in widget.scripts[0]
    assert "\\n1s 25 mph" in widget.scripts[0]


def test_no_result_ends_scripts_with_undefined():
    from ui.map.widget import _no_result

    # a trailing line comment must not swallow the terminator
    wrapped = _no_result("map.setLayoutProperty('a', 'visibility', 'none') // hide")
    assert wrapped.endswith("\n;void 0;")
    assert wrapped.startswith("map.setLayoutProperty")


def test_track_setters_call_the_page():
    widget = _FakeMapWidget()
    MapWidget.set_track_geojson(widget, {"type": "FeatureCollection", "features": []})
    MapWidget.set_track_edit_mode(widget, True)
    MapWidget.set_track_layers_visible(widget, True, False)
    MapWidget.set_track_marker(widget, {"lat": 35.0, "lon": -97.0, "label": "M1"})
    MapWidget.set_track_marker(widget, None)
    assert "stormSetTrackGeoJSON(\"{\\\"type\\\"" in widget.scripts[0]
    assert "stormSetTrackEditMode(true)" in widget.scripts[1]
    assert "stormSetTrackLayersVisible(true, false)" in widget.scripts[2]
    assert '\\"label\\": \\"M1\\"' in widget.scripts[3]
    assert "stormSetTrackMarker(null)" in widget.scripts[4]
