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
