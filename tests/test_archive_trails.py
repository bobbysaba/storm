"""D27: the trail controls in the VEHICLES panel and the archive window's
trail refresh (sources, storm motion, throttling, empty states)."""
from datetime import datetime, timedelta, timezone

import pytest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from core.observation import Observation
from core.storm_track import StormTrack
from ui.app.main_window_archive_surface import MainWindowArchiveSurfaceMixin
from ui.app.main_window_trails import TRAILS_REBUILD_MS, MainWindowTrailsMixin
from ui.controls.trail_controls import DEFAULT_QUANTITY, TrailControls

T0 = datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _obs(vid, seconds, lat=35.0, lon=-98.0, t=25.0):
    return Observation(vid, lat + seconds * 1e-5, lon, T0 + timedelta(seconds=seconds), temperature_c=t,
                       dewpoint_c=20.0, pressure_mb=950.0, wind_speed_ms=10.0, wind_dir_deg=180.0)


# ── controls ──────────────────────────────────────────────

def test_trails_start_off_and_options_show_with_the_switch(app):
    controls = TrailControls()
    assert not controls.trails_on()
    assert controls._options.isHidden() and controls._quantity.isHidden()
    seen = []
    controls.settings_changed.connect(lambda: seen.append(1))
    controls._chk_show.setChecked(True)
    assert seen and controls.trails_on() and not controls._options.isHidden()
    assert controls.settings() == (DEFAULT_QUANTITY, 15, False, False, False)


def test_storm_relative_options_need_a_track(app):
    controls = TrailControls()
    model = controls._quantity.model()
    sr_row = controls._quantity.findData("radial_wind")
    assert not model.item(sr_row).isEnabled() and not controls._time_to_space.isEnabled()
    assert not controls._needs_track.isHidden()
    controls._wind_barbs.setChecked(True)
    assert not controls._sr_barbs.isEnabled()

    controls.set_track_available(True)
    assert model.item(sr_row).isEnabled() and controls._time_to_space.isEnabled()
    assert controls._sr_barbs.isEnabled() and controls._needs_track.isHidden()
    controls._quantity.setCurrentIndex(sr_row)
    controls._time_to_space.setChecked(True)
    controls._sr_barbs.setChecked(True)
    assert controls.settings() == ("radial_wind", 15, True, True, True)

    controls.set_track_available(False)       # the track was cleared
    quantity, _, time_to_space, barbs, sr_barbs = controls.settings()
    assert quantity == DEFAULT_QUANTITY and not time_to_space and barbs and not sr_barbs


def test_needs_track_link_asks_for_track(app):
    controls = TrailControls()
    asked = []
    controls.track_requested.connect(lambda: asked.append(1))
    controls._needs_track.click()
    assert asked == [1]


def test_scale_and_status(app):
    controls = TrailControls()
    controls.set_scale([330.0, "#111111", 335.0, "#111111", 335.0, "#222222", 340.0, "#222222"], "K")
    assert controls._units.text() == "K" and controls._bar._edges == [330.0, 335.0, 340.0]
    controls.set_scale([], "K")
    assert controls._units.text() == ""
    controls.set_status("No temperature in the last 15 min")
    assert not controls._status.isHidden()
    controls.set_status("")
    assert controls._status.isHidden()


# ── window ────────────────────────────────────────────────

class _Map:
    def __init__(self):
        self.calls = []

    def set_trails(self, geojson, stops=None, units="", label=""):
        self.calls.append((geojson, stops, units, label))


class _Clock:
    def __init__(self, when):
        self.current_time = when


class _Dense:
    def __init__(self, by_vehicle):
        self._by_vehicle = by_vehicle

    def observations_by_vehicle(self):
        return self._by_vehicle


class _Mqtt:
    def __init__(self, by_vehicle):
        self._by_vehicle = by_vehicle

    def vehicle_histories(self):
        return self._by_vehicle


class Host(QWidget, MainWindowTrailsMixin, MainWindowArchiveSurfaceMixin):
    def __init__(self):
        super().__init__()
        self.vehicle_panel = QWidget(self)
        self._vehicle_panel_size_watermark = (400, 300)
        self.layout_requests = 0
        self.map_widget = _Map()
        self._time_ctrl = _Clock(T0 + timedelta(minutes=10))
        self._track = StormTrack()
        self._init_trail_controls(QVBoxLayout(self.vehicle_panel))

    def _request_layout(self):
        self.layout_requests += 1

    def track_storm_motion(self):
        from core.storm_motion import mean_motion
        return mean_motion(self._track.points)


def test_one_second_data_wins_over_mqtt(app):
    host = Host()
    dense = [_obs("p1", s) for s in range(0, 600, 1)]
    host._archive_vehicle_obs = _Dense({"p1": dense})
    host._archive_mqtt = _Mqtt({"p1": [_obs("p1", 0)], "p2": [_obs("p2", 0)], "p3": []})
    found = host._trail_observations()
    assert found["p1"] is dense and set(found) == {"p1", "p2"}


def test_refresh_clears_when_off_and_draws_when_on(app):
    host = Host()
    host._archive_mqtt = _Mqtt({"p1": [_obs("p1", s, t=20 + s / 60) for s in range(0, 600, 10)]})
    host._refresh_trails()
    assert host.map_widget.calls[-1][0] is None             # off: cleared

    host.trail_controls._chk_show.setChecked(True)
    host.trail_controls._quantity.setCurrentIndex(host.trail_controls._quantity.findData("temperature"))
    host._refresh_trails()
    geojson, stops, units, label = host.map_widget.calls[-1]
    assert [f for f in geojson["features"] if f["properties"]["kind"] == "trail"]
    assert len(stops) >= 4 and units == "°C" and label == "Temperature"
    assert host.trail_controls._units.text() == "°C" and host.trail_controls._status.isHidden()


def test_refresh_says_why_nothing_is_drawn(app):
    host = Host()
    host._archive_mqtt = _Mqtt({"p1": [_obs("p1", s) for s in range(0, 600, 10)]})
    host._time_ctrl.current_time = T0 + timedelta(hours=3)  # long after the data
    host.trail_controls._chk_show.setChecked(True)
    host._refresh_trails()
    assert host.map_widget.calls[-1][1] == []
    assert host.trail_controls._status.text() == "No equivalent potential temperature θe in the last 15 min"


def test_storm_motion_reaches_the_controls_and_the_builder(app):
    host = Host()
    host._archive_mqtt = _Mqtt({"p1": [_obs("p1", s) for s in range(0, 600, 10)]})
    host._track.place(T0, 35.0, -98.1)
    host._track.place(T0 + timedelta(minutes=20), 35.0, -98.0)
    motion = host.track_storm_motion()
    host._on_trail_track_changed(motion)
    assert host.trail_controls._time_to_space.isEnabled()
    assert host._trails_timer.isActive()

    host.trail_controls._chk_show.setChecked(True)
    host.trail_controls._time_to_space.setChecked(True)
    host._refresh_trails()
    kinds = {f["properties"]["kind"] for f in host.map_widget.calls[-1][0]["features"]}
    assert {"trail", "ring", "center"} <= kinds

    host._on_trail_track_changed(None)
    assert not host.trail_controls._time_to_space.isEnabled()


class _Asos:
    def __init__(self, by_station):
        self._by_station = by_station

    def observations_by_station(self):
        return self._by_station


def test_asos_stations_join_time_to_space_only(app, monkeypatch):
    """Fixed stations make a path only relative to the storm; their pressure
    is sea-level, so it isn't used as station pressure."""
    host = Host()
    host._archive_mqtt = _Mqtt({"p1": [_obs("p1", s) for s in range(0, 600, 10)]})
    host._archive_asos = _Asos({"OUN": [_obs("surface:asos:OUN", s, lon=-97.9) for s in range(0, 600, 60)],
                                "OKC": []})
    seen = {}
    real_build = host._trail_builder.build

    def build(observations, *args, **kwargs):
        seen["ids"] = set(observations)
        seen["no_station_pressure"] = kwargs.get("no_station_pressure")
        return real_build(observations, *args, **kwargs)

    monkeypatch.setattr(host._trail_builder, "build", build)
    host.trail_controls._chk_show.setChecked(True)
    host._refresh_trails()
    assert seen["ids"] == {"p1"} and seen["no_station_pressure"] == frozenset()

    host._track.place(T0, 35.0, -98.1)
    host._track.place(T0 + timedelta(minutes=20), 35.0, -98.0)
    host._on_trail_track_changed(host.track_storm_motion())
    host.trail_controls._time_to_space.setChecked(True)
    host._refresh_trails()
    assert seen["ids"] == {"p1", "ASOS OUN"}
    assert seen["no_station_pressure"] == frozenset({"ASOS OUN"})


def test_rebuilds_are_coalesced(app):
    host = Host()
    assert host._trails_timer.interval() == TRAILS_REBUILD_MS and host._trails_timer.isSingleShot()
    host._trails_timer.stop()
    host._schedule_trails()
    remaining = host._trails_timer.remainingTime()
    host._schedule_trails(T0)                               # a clock tick doesn't restart it
    assert host._trails_timer.isActive() and host._trails_timer.remainingTime() <= remaining


def test_switch_lets_the_panel_shrink_back(app):
    host = Host()
    before = host.layout_requests
    host.trail_controls._chk_show.setChecked(True)
    assert host.layout_requests > before and host._vehicle_panel_size_watermark == (0, 0)


def test_needs_track_link_opens_track(app):
    from PyQt6.QtWidgets import QToolButton
    host = Host()
    host.btn_track = QToolButton()
    host.btn_track.setCheckable(True)
    host.trail_controls._needs_track.click()
    assert host.btn_track.isChecked()


def test_readers_hand_over_whole_day_histories():
    from archive.fetchers.mqtt_reader import ArchiveMQTTReader
    from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher
    mqtt = ArchiveMQTTReader.__new__(ArchiveMQTTReader)
    mqtt._vehicle_observations = {"p1": [_obs("p1", 0)]}
    assert mqtt.vehicle_histories() == mqtt._vehicle_observations
    dense = ArchiveVehicleObsFetcher.__new__(ArchiveVehicleObsFetcher)
    dense._observations = {"p1": [_obs("p1", 0), _obs("p1", 1)]}
    assert dense.observations_by_vehicle() is dense._observations


def test_trail_layers_sit_under_the_storm_track():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "ui" / "map" / "map_template.html").read_text()
    assert html.index("id: 'obs-trails-barbs'") < html.index("id: 'storm-track-line'")
    assert "window.stormSetTrails = function" in html
