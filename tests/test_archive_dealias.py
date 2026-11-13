"""D26: the radar drawer's DEALIAS toggle and the archive window's wiring."""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from archive.fetchers.radar_archive_fetcher import VelocityOptions
from ui.app.main_window_archive import MainWindowArchiveMixin, _velocity_status_tag
from ui.controls.radar_controls import RadarControls


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controls(app, monkeypatch):
    monkeypatch.setattr(RadarControls, "_refresh_product_availability", lambda self, site: None)
    return RadarControls()


_ARCHIVE_PRODUCTS = [("reflectivity", "Reflectivity (REF)"), ("velocity", "Velocity (VEL)")]


def test_dealias_shows_for_archive_velocity_only(controls):
    assert controls._btn_dealias.isHidden()                     # live
    controls.configure_for_archive(True)
    controls.set_archive_products(_ARCHIVE_PRODUCTS)
    assert controls._btn_dealias.isHidden()                     # reflectivity
    controls.select_product("velocity")
    assert not controls._btn_dealias.isHidden()
    controls.select_product("reflectivity")
    assert controls._btn_dealias.isHidden()


def test_dealias_toggle_emits_and_is_saved(controls):
    seen = []
    controls.dealias_toggled.connect(seen.append)
    controls.set_dealias(True)
    assert seen == [True] and controls.is_dealias()
    assert controls.save_state()["dealias"] is True

    other = RadarControls()
    other.restore_state({"dealias": True})
    assert other.is_dealias()


@pytest.mark.parametrize("notes, tag", [
    ("", ""),
    ("dealiased (region-based, Nyquist 28.2 m/s)", " · dealiased"),
    ("raw: no Nyquist velocity recorded, not dealiased", " · not dealiased"),
    ("raw: dealiasing failed", " · not dealiased"),
])
def test_status_tag_never_calls_raw_velocity_dealiased(notes, tag):
    assert _velocity_status_tag(SimpleNamespace(velocity_processing=notes)) == tag
    assert _velocity_status_tag(SimpleNamespace()) == ""        # live scans have no note


class _Radar:
    def __init__(self):
        self.options = []

    def set_velocity_options(self, options):
        self.options.append(options)


class _ArchiveBar:
    def __init__(self):
        self.status = []

    def set_radar_status(self, text, error=False):
        self.status.append(text)


class _Window(MainWindowArchiveMixin):
    def __init__(self, controls):
        self.radar_controls = controls
        self._archive_radar = _Radar()
        self._archive_controls = _ArchiveBar()


def test_toggling_dealias_sets_the_fetchers_options(controls):
    window = _Window(controls)
    controls.dealias_toggled.connect(window._on_archive_dealias_toggled)
    controls.configure_for_archive(True)
    controls.set_archive_products(_ARCHIVE_PRODUCTS)
    controls.select_product("velocity")
    controls.set_dealias(True)
    controls.set_dealias(False)
    assert window._archive_radar.options == [VelocityOptions(dealias=True), VelocityOptions()]
    assert window._archive_controls.status == ["Radar: dealiasing…", "Radar: loading raw velocity"]


def test_toggling_dealias_on_reflectivity_leaves_the_status_alone(controls):
    window = _Window(controls)
    window._on_archive_dealias_toggled(True)
    assert window._archive_radar.options == [VelocityOptions()]  # button state, not the arg
    assert window._archive_controls.status == []


def test_a_new_fetcher_gets_the_drawers_choice(controls):
    window = _Window(controls)
    controls.set_dealias(True)
    window._apply_archive_velocity_options()
    assert window._archive_radar.options == [VelocityOptions(dealias=True)]
