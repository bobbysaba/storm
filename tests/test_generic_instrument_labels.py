"""Core STORM names a provider's instruments only through what the provider
hands it, and reads layer keys and package kinds saved before 3.0."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from core import case_package
from ui import session_state


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settings(tmp_path):
    return QSettings(str(tmp_path / "storm.ini"), QSettings.Format.IniFormat)


# -- names from the provider, generic without one ---------------------------------

def test_the_uas_button_takes_the_providers_name(app):
    from ui.sounding.controls import SoundingControls
    controls = SoundingControls()
    controls.set_uas_available("DRONE", "Our drone ascents")
    assert (controls._btn_uas.text(), controls._btn_uas.toolTip()) == ("DRONE", "Our drone ascents")
    controls.set_uas_available("UAS")
    assert controls._btn_uas.toolTip() == "Uncrewed-aircraft (UAS) ascents on this day"
    controls.set_uas_available(None)
    assert not controls._btn_uas.isVisibleTo(controls)


def test_pill_labels_come_from_the_provider(monkeypatch):
    from ui.sounding import theme
    monkeypatch.setitem(theme._SOURCE_LABELS, "retrieval", "RETRIEVAL")
    monkeypatch.setitem(theme._SOURCE_LABELS, "uas", "UAS")
    theme.set_source_labels({"uas": "DRONE", "other": "x"})
    assert (theme._SOURCE_LABELS["retrieval"], theme._SOURCE_LABELS["uas"]) == ("RETRIEVAL", "DRONE")
    assert "other" not in theme._SOURCE_LABELS


def test_the_vad_tooltip_names_the_providers_instruments(app):
    from ui.controls.radar_controls import RadarControls
    from ui.app.main_window_util import described
    controls = RadarControls()
    controls.set_vad_available(True, described("Lidar wind profiles", SimpleNamespace(description="our trailer")))
    controls.configure_for_archive(True)
    assert controls._btn_vad.toolTip() == "Lidar wind profiles: our trailer"
    assert described("Doppler lidar", object()) == "Doppler lidar"


def test_lidar_names_and_markers_come_from_its_sources():
    from ui.app.main_window_lidar import MainWindowLidarMixin
    named = SimpleNamespace(instrument="VAN", mobile=True, name="Lidar van", marker="Van")
    plain = SimpleNamespace(instrument="L2", mobile=False)
    host = SimpleNamespace(_archive_raw_lidar=SimpleNamespace(sources=[named, plain]), _lidar_locations=[])
    for name in ("_lidar_source_text", "_lidar_marker_label", "_lidar_place"):
        setattr(host, name, getattr(MainWindowLidarMixin, name).__get__(host))
    van = SimpleNamespace(instrument="VAN", number=2, place="")
    site = SimpleNamespace(instrument="L2", number=1, place="the field")
    host._lidar_locations = [van, site]
    assert (host._lidar_marker_label(van), host._lidar_marker_label(site)) == ("Van 2", "L2")
    assert host._lidar_place(van) == "Lidar van · stop 2 of 1"
    assert host._lidar_place(site) == "L2 · the field"


def test_the_nssl_provider_names_its_own():
    from providers.nssl.archive.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES
    from providers.nssl.archive.sounding_archive_fetcher import NsslArchiveSoundingFetcher
    truck = next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == "DLTRUCK1")
    assert (truck.name, truck.marker) == ("Lidar truck", "Truck")
    assert NsslArchiveSoundingFetcher.pill_labels == {"retrieval": "TROPoe"}
    assert not hasattr(NsslArchiveSoundingFetcher, "uas_name")      # CopterSonde withheld for now


# -- keys saved before 3.0 ----------------------------------------------------------

def test_a_saved_session_with_the_old_contours_key(tmp_path):
    s = _settings(tmp_path)
    s.setValue("session/viewer", '{"sfcoa": {"products": ["sbcape"]}, "drawers": ["radar", "sfcoa"]}')
    state = session_state.load("viewer", s)
    assert state == {"contours": {"products": ["sbcape"]}, "drawers": ["radar", "contours"]}


def test_saved_pins_and_layer_order_with_the_old_key(tmp_path, monkeypatch):
    from ui.app.drawer_manager import DrawerManager, _PINNED_KEY
    s = _settings(tmp_path)
    s.setValue(_PINNED_KEY, ["sfcoa", "radar"])
    assert DrawerManager(settings=s)._pinned == {"contours", "radar"}

    from ui.widgets import layer_order_pill
    monkeypatch.setattr(layer_order_pill, "QSettings", lambda: s)
    s.setValue(layer_order_pill._SETTINGS_KEY, ["sfcoa", "radar"])
    assert layer_order_pill.load_layer_order()[:2] == ["contours", "radar"]


def test_the_old_contours_icon_name_still_reads():
    import tomllib
    import settings
    from data.sources import parse_sources
    table = tomllib.loads('[[sources]]\nid = "m"\nkind = "contours"\nurl = "https://x.test/m"\nicon = "sfcoa"\n')
    (src,) = parse_sources(table["sources"], settings.PROJECT_DIR)
    assert src.icon == "contours"


def test_old_package_kinds_keep_their_labels():
    assert case_package.kind_label("clamps profiles") == case_package.DATA_TYPES["instrument profiles"]
    assert case_package.kind_label("coptersonde") == "UAS profiles"
    assert case_package.kind_label("our radiometer") == "our radiometer"
