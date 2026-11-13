"""C20 in the window: the LIDAR drawer, the map's lidar layers and the archive
window's lidar handlers (ui/app/main_window_lidar.py)."""
import gc
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from archive.fetchers.raw_lidar_archive_fetcher import KNOWN_RAW_LIDAR_SOURCES, LidarAsset
from core.lidar_scans import classify_scans
from ui.app.main_window_lidar import MainWindowLidarMixin
from ui.controls.raw_lidar_controls import RawLidarControls
from ui.map.widget import MapWidget       # before any QApplication (QtWebEngine)

UTC = timezone.utc
T0 = datetime(2023, 6, 9, 20, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _source(instrument):
    return next(s for s in KNOWN_RAW_LIDAR_SOURCES if s.instrument == instrument and s.product == "ppi")


def _rays(instrument="CLAMPS1", starts=(0, 600), lat=35.0, provenance=None):
    """PPI sweeps of 8 rays at the given seconds after T0."""
    t, az = [], []
    for start in starts:
        for i, a in enumerate(range(0, 360, 45)):
            t.append(T0.timestamp() + start + 2 * i)
            az.append(float(a))
    n = len(t)
    scan_number = np.repeat(np.arange(len(starts), dtype=float), 8)
    rays = SimpleNamespace(
        source=_source(instrument), time_epoch=np.array(t), azimuth_deg=np.array(az),
        elevation_deg=np.full(n, 2.0), scan_number=scan_number,
        latitude=np.full(n, lat), longitude=np.full(n, -97.0),
        fields={"velocity": {"units": "m/s"}, "intensity": {"units": ""}},
        provenance=provenance or {"north_referenced": True})
    rays.scans = classify_scans(rays)
    return rays


# -- drawer ------------------------------------------------------------------------------

def test_drawer_fields_keep_the_choice_and_offer_intensity_only_when_recorded(app):
    controls = RawLidarControls()
    picked = []
    controls.field_selected.connect(picked.append)
    controls.set_fields({"backscatter": "sr-1", "velocity": "m/s"})
    assert controls.current_field() == "velocity" and picked == ["velocity"]   # velocity by default
    assert controls._intensity_slider.isEnabled() is False
    controls._field_combo.setCurrentIndex(controls._field_combo.findData("backscatter"))
    assert picked == ["velocity", "backscatter"]
    controls.set_fields({"backscatter": "sr-1", "intensity": "", "velocity": "m/s"})
    assert controls.current_field() == "backscatter" and len(picked) == 2   # kept, nothing sent
    assert controls._intensity_slider.isEnabled()
    controls.set_fields({"velocity": "m/s"})                                # gone: back to velocity
    assert picked[-1] == "velocity"


def test_drawer_intensity_and_status(app):
    controls = RawLidarControls()
    assert controls.min_intensity() is None
    controls._set_intensity_available(True)
    controls._intensity_slider.setValue(12)
    assert controls.min_intensity() == 1.012 and controls._intensity_value.text() == "1.012"
    controls._intensity_slider.setValue(0)
    assert controls.min_intensity() is None and controls._intensity_value.text() == "off"
    controls.set_status("No lidar data this date")
    assert controls._location_button.text() == "No lidar data this date"
    controls.set_location("Lidar truck · stop 1 of 2", "details")
    controls.set_status("Pick a lidar")
    assert controls._location_button.text() == "Lidar truck · stop 1 of 2"
    requested = []
    controls.location_requested.connect(lambda: requested.append(1))
    controls._location_button.click()
    assert requested == [1]


def test_drawer_heading_notice(app):
    controls = RawLidarControls()
    controls.set_heading_notice({"heading_missing_in_file": True, "azimuth_reference": "estimated"})
    assert controls._heading_warning.isVisibleTo(controls)
    assert controls._heading_warning.toolTip() == "estimated"
    controls.set_heading_notice({"north_referenced": False})
    assert "#F87171" in controls._heading_warning.styleSheet()
    controls.set_heading_notice(None)
    assert not controls._heading_warning.isVisibleTo(controls)


# -- map ---------------------------------------------------------------------------------

def test_map_widget_sends_lidar_locations_and_images(app):
    stored = []
    widget = SimpleNamespace(scripts=[], scheme_handler=SimpleNamespace(
        set_lidar_png=lambda image_id, png: stored.append((image_id, png))))
    widget.run_js = widget.scripts.append
    MapWidget.set_lidar_locations(widget, [{"key": "CLAMPS1-1", "lat": 35.0, "lon": -97.0,
                                            "label": "CLAMPS1", "hover": "a\nb", "selected": True}])
    arg = widget.scripts[-1].split("stormSetLidarLocations(", 1)[1].rsplit(");", 1)[0]
    data = json.loads(json.loads(arg))
    assert data["features"][0]["properties"] == {"key": "CLAMPS1-1", "label": "CLAMPS1",
                                                 "hover": "a\nb", "selected": True}
    MapWidget.set_lidar_image(widget, b"png", [-98, 34, -96, 36])
    MapWidget.set_lidar_image(widget, b"png2", [-98, 34, -96, 36])
    assert stored == [("1", b"png"), ("2", b"png2")]
    assert 'storm://app/lidar/2.png' in widget.scripts[-1] and "-98,34,-96,36" in widget.scripts[-1]


def test_scheme_handler_serves_only_the_current_lidar_image(app):
    from ui.map.tile_scheme_handler import StormSchemeHandler
    handler = StormSchemeHandler.__new__(StormSchemeHandler)
    import threading
    handler._sat_lock = threading.Lock()
    handler.set_lidar_png("3", b"png")
    replies, failures = [], []
    handler._reply = lambda job, mime, data: replies.append(data)
    job = SimpleNamespace(fail=lambda error: failures.append(error))
    handler._serve_lidar_png(job, "3.png")
    handler._serve_lidar_png(job, "2.png")
    assert replies == [b"png"] and len(failures) == 1


# -- the window's handlers ---------------------------------------------------------------

class _Map(QObject):
    lidar_location_clicked = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.locations, self.images, self.cleared, self.flown = [], [], 0, None

    def set_lidar_locations(self, markers):
        self.locations = markers

    def set_lidar_image(self, png, bounds):
        self.images.append((png, bounds))

    def clear_lidar_image(self):
        self.cleared += 1

    def fly_to(self, lat, lon):
        self.flown = (lat, lon)


class _Clock:
    def __init__(self):
        self.current_time = T0 - timedelta(hours=1)
        self.window = (T0 - timedelta(hours=8), T0 + timedelta(hours=6))
        self.paused = False

    def pause(self):
        self.paused = True

    def set_time(self, when):
        self.current_time = when


class _Toolbar:
    def __init__(self, parent):
        self.parent = parent

    def add_button(self, label, tooltip, icon, overflow=False):
        button = QToolButton(self.parent)
        button.setCheckable(True)
        return button


class _Host(QObject, MainWindowLidarMixin):
    _lidar_render_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._map_container = QWidget()        # owns every widget, so teardown is one delete
        self.map_widget = _Map()
        self._init_lidar_toolbar(_Toolbar(self._map_container))
        self._time_ctrl = _Clock()
        self._archive_time = T0
        self.status_msg_label = QLabel(self._map_container)
        self.coverage = None
        self._archive_controls = SimpleNamespace(set_coverage=lambda spans, *a: setattr(self, "coverage", spans))
        self.activities = {}
        self._archive_loading = SimpleNamespace(
            begin=lambda key, text: self.activities.__setitem__(key, text),
            end=lambda key: self.activities.pop(key, None),
            startup_pending=lambda: False)
        self._radar_station_sites = [{"site_id": "KTLX", "lat": 35.333, "lon": -97.278}]
        self._archive_radar = SimpleNamespace(station="KTLX")
        self.surveyed, self.loaded = [], []
        self._archive_raw_lidar = SimpleNamespace(survey=self.surveyed.append,
                                                  load_files=lambda i, a: self.loaded.append((i, a)))
        self.renders = []

    def _layout_overlays(self):
        pass

    def _later(self, ms, callback):
        callback()

    def _request_archive_clamps_wind(self):
        pass

    def _bg_render_lidar_sweep(self, scan, field, min_intensity, generation):   # synchronous stand-in
        self.renders.append((scan, field, min_intensity, generation))


@pytest.fixture
def host(app):
    """Torn down here, not whenever the cyclic GC gets to it: Qt widgets
    destroyed in the middle of a later test's paint event crash it."""
    h = _Host()
    yield h
    h._map_container.deleteLater()
    QApplication.processEvents()
    del h
    gc.collect()


def test_lidar_opens_only_when_the_date_has_files(host):
    clamps1 = KNOWN_RAW_LIDAR_SOURCES[4].platform_id
    host._on_lidar_assets_ready({})
    assert not host.btn_raw_lidar.isEnabled()
    assert host.raw_lidar_controls._location_button.text() == "No lidar data this date"
    host._on_lidar_assets_ready({clamps1: None})
    assert host.btn_raw_lidar.toolTip() == "Couldn't check for lidar data"
    asset = LidarAsset(_source("CLAMPS1"), "a.cdf")
    host._on_lidar_assets_ready({"CLAMPS1-PPI": [asset]})
    assert host.btn_raw_lidar.isEnabled() and host.surveyed == [{"CLAMPS1": [asset]}]
    assert "lidar-survey" in host.activities


def test_the_survey_waits_for_the_vehicle_tracks(host):
    waits = []
    host._later = lambda ms, callback: waits.append(callback)
    host._archive_vehicle_obs_started, host._archive_vehicle_obs_loaded = True, False
    host._lidar_assets = {"CLAMPS1": ["a"]}
    host._start_lidar_survey(since=__import__("time").monotonic())
    assert host.surveyed == [] and len(waits) == 1
    host._archive_vehicle_obs_loaded = True
    waits[0]()
    assert host.surveyed == [{"CLAMPS1": ["a"]}]


def test_locations_come_from_the_loaded_files_and_show_while_lidar_is_open(host):
    host._lidar_sites = {"CLAMPS1": {"lat": 34.98, "lon": -97.52, "description": "NWC"}}
    host._on_lidar_file_ready("CLAMPS1", _rays())
    host._on_lidar_file_ready("DLTRUCK1", _rays("DLTRUCK1", starts=(0, 3600), lat=35.5))
    assert [loc.key for loc in host._lidar_locations] == ["CLAMPS1-1", "DLTRUCK1-1"]
    assert host.map_widget.locations == []                       # LIDAR closed
    host.btn_raw_lidar.setChecked(True)
    markers = {m["key"]: m for m in host.map_widget.locations}
    assert markers["CLAMPS1-1"]["label"] == "CLAMPS1" and markers["DLTRUCK1-1"]["label"] == "Truck 1"
    assert "CLAMPS1 trailer · NWC" in markers["CLAMPS1-1"]["hover"]
    assert "fixed site" in markers["CLAMPS1-1"]["hover"]
    assert "Scanning 20:00–20:10Z · 2 PPI scans" in markers["CLAMPS1-1"]["hover"]
    host._on_lidar_survey_done()
    assert host.raw_lidar_controls._location_button.text() == "Pick a lidar on the map (2 locations)"
    host.btn_raw_lidar.setChecked(False)
    assert host.map_widget.locations == []


def test_scans_before_12z_belong_to_the_previous_evening(host):
    early = _rays(starts=(-9 * 3600,))                           # 11:00Z
    host._on_lidar_file_ready("CLAMPS1", early)
    assert host._lidar_locations == []


def test_picking_a_lidar_goes_to_its_first_scan_and_draws_it(host):
    host._on_lidar_file_ready("CLAMPS1", _rays())
    host._lidar_large_by["CLAMPS1"] = ["big"]
    host.btn_raw_lidar.setChecked(True)
    host.map_widget.lidar_location_clicked.emit("CLAMPS1-1")
    loc = host._lidar_location
    assert loc.key == "CLAMPS1-1" and host._time_ctrl.paused
    assert host._time_ctrl.current_time == loc.start == T0
    assert host.loaded == [("CLAMPS1", ["big"])]                # held-back files follow
    assert host.coverage == [(T0, T0 + timedelta(seconds=614))]
    assert host.raw_lidar_controls._location_button.text() == "CLAMPS1 trailer · stop 1 of 1"
    assert "km from KTLX" in host.raw_lidar_controls._location_button.toolTip()
    assert [m["selected"] for m in host.map_widget.locations] == [True]
    [(scan, field, intensity, generation)] = host.renders
    assert scan is loc.scans[0] and field == "velocity" and intensity is None
    assert "lidar-location" in host.activities
    host._on_lidar_render_ready({"generation": generation, "png": b"p", "bounds": [1, 2, 3, 4],
                                 "metadata": {"scan": "PPI", "field": "velocity", "vmin": -30,
                                              "vmax": 30, "units": "m/s"}})
    assert host.map_widget.images == [(b"p", [1, 2, 3, 4])]
    assert "lidar-location" in host.activities                    # its held-back files are loading
    host._on_lidar_instrument_loaded("CLAMPS1", 1)
    assert "lidar-location" not in host.activities


def test_the_sweep_follows_the_clock_but_isnt_redrawn_needlessly(host):
    host._on_lidar_file_ready("CLAMPS1", _rays())
    host._on_lidar_location_clicked("CLAMPS1-1")
    first = host.renders[-1]
    host._on_lidar_render_ready({"generation": first[3], "error": "x", "quiet": True})
    host._time_ctrl.current_time = T0 + timedelta(seconds=30)    # same scan held
    host._on_lidar_time_changed(None)
    assert len(host.renders) == 1
    host._time_ctrl.current_time = T0 + timedelta(seconds=605)   # the next scan
    host._on_lidar_time_changed(None)
    assert host.renders[-1][0] is host._lidar_scans[1]
    # a change while a render is in flight waits for it, then draws the latest
    host.raw_lidar_controls._set_intensity_available(True)
    host._on_lidar_min_intensity_changed(1.01)
    assert len(host.renders) == 2 and host._lidar_render_pending
    stale = host.renders[-1][3]
    host._on_lidar_render_ready({"generation": stale, "png": b"old", "bounds": []})
    assert host.map_widget.images == []                          # superseded: not drawn
    assert host.renders[-1][2] == 1.01


def test_hiding_the_scans_clears_the_map(host):
    host._on_lidar_file_ready("CLAMPS1", _rays())
    host._on_lidar_location_clicked("CLAMPS1-1")
    cleared = host.map_widget.cleared
    host.raw_lidar_controls.set_data_on(False)
    assert host.map_widget.cleared == cleared + 1
    generation = host.renders[-1][3]
    host._on_lidar_render_ready({"generation": generation, "png": b"p", "bounds": []})
    assert host.map_widget.images == []
    host.raw_lidar_controls.set_data_on(True)
    assert len(host.renders) == 2


def test_a_truck_with_an_estimated_heading_says_so(host):
    rays = _rays("DLTRUCK1", provenance={"north_referenced": True, "heading_missing_in_file": True,
                                         "truck_heading_estimate_deg": 92.0,
                                         "azimuth_reference": "estimated from the compass"})
    host._on_lidar_file_ready("DLTRUCK1", rays)
    host._on_lidar_location_clicked("DLTRUCK1-1")
    assert "estimated from the truck compass (~92°)" in host.raw_lidar_controls._location_button.toolTip()
    assert host.raw_lidar_controls._heading_warning.toolTip() == "estimated from the compass"


def test_a_failed_file_of_the_picked_lidar_is_reported(host):
    host._on_lidar_file_ready("CLAMPS1", _rays())
    host._on_lidar_location_clicked("CLAMPS1-1")
    host._on_lidar_error("DLTRUCK1: a.cdf: down")
    assert "a.cdf" not in host.status_msg_label.text()
    host._on_lidar_error("CLAMPS1: b.cdf: b.cdf is over 768 MB; not downloaded")
    assert host.status_msg_label.text() == "Lidar: b.cdf: b.cdf is over 768 MB; not downloaded"


def test_the_truck_track_prefers_one_second_data(host):
    dense = [SimpleNamespace(lat=1)]
    host._archive_vehicle_obs = SimpleNamespace(observations_by_vehicle=lambda: {"lid1": dense})
    host._archive_mqtt = SimpleNamespace(vehicle_histories=lambda: {"lid1": ["mqtt"]})
    assert host._lidar_truck_track() == dense
    host._archive_vehicle_obs = SimpleNamespace(observations_by_vehicle=lambda: {})
    assert host._lidar_truck_track() == ["mqtt"]
