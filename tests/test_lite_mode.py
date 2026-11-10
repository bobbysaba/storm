"""Tests for lite mode and the Chromium flag builder (PERFORMANCE_PLAN.md steps 2 and 3)."""

from types import SimpleNamespace

import runtime_flags as rf
from ui.layers.surface_plot_layer import thin_stations


def setup_function():
    rf.reset_flags()


def test_chromium_flags_defaults_match_previous_behaviour():
    assert rf.chromium_flags("darwin") == "--no-sandbox --in-process-gpu"
    assert rf.chromium_flags("win32") == "--no-sandbox --in-process-gpu --ignore-gpu-blocklist"
    rf.FLAGS.runtime_safe = True
    assert rf.chromium_flags("win32") == "--no-sandbox --disable-gpu --disable-gpu-compositing"


def test_chromium_flags_lite_and_user_flags_without_duplicates():
    rf.apply_overrides(lite_mode=True, webengine_flags="--disable-gpu-compositing --in-process-gpu")
    assert rf.chromium_flags("linux") == (
        "--no-sandbox --in-process-gpu --renderer-process-limit=1 --disable-gpu-compositing"
    )


def _item(sid, lat, lon):
    return {"id": sid, "obs": SimpleNamespace(lat=lat, lon=lon)}


def test_thin_stations_keeps_one_per_cell_deterministically():
    items = [_item("B", 35.01, -97.01), _item("A", 35.10, -97.10), _item("C", 35.30, -97.01)]
    kept = thin_stations(items, 0.25)
    assert [it["id"] for it in kept] == ["A", "C"]
    assert [it["id"] for it in thin_stations(list(reversed(items)), 0.25)] == ["A", "C"]
    assert thin_stations(items, 0) is items


def test_apply_lite_mode_caps_radar_grid(monkeypatch):
    from ui.map import radar_overlay as ro

    for name in ("RENDER_GRID_SIZE", "MAX_RENDER_GRID_SIZE", "ADAPTIVE_GRID_STEPS"):
        monkeypatch.setattr(ro, name, getattr(ro, name))
    ro.apply_lite_mode()
    assert ro.RENDER_GRID_SIZE == rf.LITE_RENDER_GRID_START
    assert max(ro.ADAPTIVE_GRID_STEPS) == rf.LITE_RENDER_GRID_MAX
    ro.set_render_grid_size(1536)
    assert ro.RENDER_GRID_SIZE == rf.LITE_RENDER_GRID_MAX
