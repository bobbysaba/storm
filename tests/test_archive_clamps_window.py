"""C18 / C21 in the window: the sounding drawer's COPTERSONDE, the sounding
viewer and VAD window with CLAMPS data, and the archive window's CLAMPS
handlers (ui/app/main_window_clamps.py)."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.observation import Observation
from core.sounding import Sounding, SoundingSet
from core.vad import VADProfile, VADSet
from ui.app.main_window_clamps import MainWindowClampsMixin
from ui.app.main_window_sounding import MainWindowSoundingMixin
from ui.app.main_window_trails import MainWindowTrailsMixin

UTC = timezone.utc
T0 = datetime(2023, 3, 3, 4, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _sounding(when, lat=34.0, wind=True, n=6):
    p = np.linspace(1000.0, 500.0, n)
    t = np.linspace(25.0, -10.0, n)
    u = np.linspace(2.0, 20.0, n) if wind else np.full(n, np.nan)
    return Sounding(lat, -90.0, when, 0, "x", p, t, t - 5.0, u, u * 0.5 if wind else u,
                    np.linspace(100.0, 5500.0, n))


def _set(source, times, wind=True, **kw):
    soundings = [_sounding(t, wind=wind) for t in times]
    for i, s in enumerate(soundings):
        s.slot_offset = i
    return SoundingSet(34.0, -90.0, 100.0, times[-1], soundings, source=source, **kw)


# -- sounding drawer ---------------------------------------------------------------------

def test_coptersonde_button_is_archive_only(app):
    from ui.sounding.controls import SoundingControls
    controls = SoundingControls()
    assert not controls._btn_copter.isVisibleTo(controls)
    controls.set_hrrr_available(False)
    controls.set_coptersonde_available(True)
    assert controls._btn_copter.isVisibleTo(controls)
    assert not controls._copter_combo.isVisibleTo(controls)     # nothing listed yet


def test_coptersonde_sites_are_listed_and_picked(app):
    from ui.sounding.controls import SoundingControls
    controls = SoundingControls()
    controls.set_coptersonde_available(True)
    modes, picked = [], []
    controls.mode_changed.connect(modes.append)
    controls.coptersonde_selected.connect(picked.append)
    controls._btn_copter.setChecked(True)
    assert modes == ["coptersonde"] and controls.active_mode == "coptersonde"
    a = _set("coptersonde", [T0], station_id="A")
    b = _set("coptersonde", [T0], station_id="B")
    controls.set_coptersondes({"B": b, "A": a})
    assert [controls._copter_combo.itemText(i) for i in range(2)] == ["A", "B"]
    assert controls._copter_combo.isVisibleTo(controls)
    assert picked == [a]
    controls._copter_combo.setCurrentIndex(1)
    assert picked == [a, b]
    controls.set_coptersondes({"A": a, "B": b})        # the pick is kept on a refresh
    assert controls._copter_combo.currentText() == "B" and picked[-1] is b
    controls._btn_obs.setChecked(True)
    assert not controls._copter_combo.isVisibleTo(controls)
    assert not controls._btn_copter.isChecked()


def test_reset_returns_from_coptersonde(app):
    from ui.sounding.controls import SoundingControls
    controls = SoundingControls()
    controls.set_hrrr_available(False)
    controls.set_coptersonde_available(True)
    controls._btn_copter.setChecked(True)
    controls.reset_mode()
    assert controls.active_mode == "obs"
    assert controls._btn_obs.isChecked() and not controls._btn_copter.isChecked()


# -- sounding viewer ---------------------------------------------------------------------

def test_tropoe_without_wind_draws_and_says_so(app):
    from ui.sounding.dialog import SoundingDialog
    dlg = SoundingDialog()
    times = [T0 - timedelta(minutes=5 * i) for i in range(40)][::-1]
    sset = _set("clamps_tropoe", times, wind=False, station_id="CLAMPS1",
                station_name="CLAMPS1 TROPoe (aeri.v2)")
    dlg.load(sset)
    assert dlg.windowTitle() == "CLAMPS TROPoe Profile"
    assert dlg._cur_idx == 39
    assert "CLAMPS1 TROPoe (aeri.v2)" in dlg._header_line1.text()
    assert "no wind" in dlg._header_line2.text()
    # 40 retrievals: about five spaced labels, the last one included
    assert len(dlg._tick_labels) <= 6 and dlg._tick_indices[-1] == 39
    assert [lbl.text() for lbl in dlg._tick_labels][-1] == T0.strftime("%H%MZ")
    dlg._on_scrubber_changed(0)
    dlg.close()


def test_coptersonde_header_and_clock_labels(app):
    from ui.sounding.dialog import SoundingDialog
    dlg = SoundingDialog()
    sset = _set("coptersonde", [T0 - timedelta(hours=1), T0], station_id="LakeVillage5N939",
                station_name="PERiLS CopterSonde (LakeVillage5N939)")
    dlg.load(sset)
    assert dlg.windowTitle() == "CopterSonde Profile"
    assert dlg._header_line1.text() == "CopterSonde  ·  LakeVillage5N939"
    assert "34.000°N" in dlg._header_line2.text()
    assert [lbl.text() for lbl in dlg._tick_labels] == ["0300Z", "0400Z"]
    dlg.close()


def test_day_numbers_only_when_soundings_span_two_days(app):
    from ui.sounding.dialog import SoundingDialog
    dlg = SoundingDialog()
    dlg.load(_set("nssl", [T0, T0 + timedelta(minutes=20)]))
    assert [lbl.text() for lbl in dlg._tick_labels] == ["0400Z", "0420Z"]
    dlg.load(_set("obs", [T0 - timedelta(hours=16), T0]))
    assert [lbl.text() for lbl in dlg._tick_labels] == ["1200Z\n2", "0400Z\n3"]
    dlg.close()


def test_an_unplaced_launch_says_so(app):
    from ui.sounding.dialog import SoundingDialog
    dlg = SoundingDialog()
    sset = _set("nssl", [T0])
    sset.soundings[0].lat = sset.soundings[0].lon = 0.0
    sset.soundings[0].location_source = "unknown"
    dlg.load(sset)
    assert "launch position unknown" in dlg._header_line2.text()
    dlg.close()


# -- VAD window ------------------------------------------------------------------------------

def _vad_set(n, start=T0):
    profiles = [VADProfile(timestamp=start + timedelta(minutes=i), site="DLTRUCK1-DL2-CSMWINDS",
                           heights_m=np.array([100.0, 500.0, 1000.0]),
                           wind_dir=np.array([180.0, 200.0, 220.0]),
                           wind_spd=np.array([10.0, 20.0, 30.0]))
                for i in range(n)]
    return VADSet(profiles)


def test_vad_window_shows_a_preloaded_set_at_the_clock(app, monkeypatch):
    from ui.dialogs import vad_dialog

    def no_fetch(*a, **k):
        raise AssertionError("a preloaded set isn't fetched")

    monkeypatch.setattr(vad_dialog, "fetch_recent_vads", no_fetch)
    vset = _vad_set(500)
    dlg = vad_dialog.VADDialog("Lidar truck CSM winds (DL2)", preloaded_set=vset,
                               start_time=T0 + timedelta(minutes=120, seconds=30))
    dlg._fetch_data()
    assert dlg._cur_idx == 120
    assert "Lidar truck CSM winds (DL2)" in dlg._header_line1.text()
    # 500 profiles: only the spaced tick labels exist
    assert len(dlg._tick_labels) <= 8 and dlg._tick_indices[0] == 0 and dlg._tick_indices[-1] == 499
    dlg._on_scrubber_changed(250)
    dlg._set_view("time_height")
    dlg.close()


def test_vad_window_starts_at_the_first_profile_before_any(app):
    from ui.dialogs.vad_dialog import VADDialog
    dlg = VADDialog("x", preloaded_set=_vad_set(5), start_time=T0 - timedelta(hours=1))
    dlg._fetch_data()
    assert dlg._cur_idx == 0
    assert len(dlg._tick_labels) == 5
    dlg.close()


def test_hodograph_bound_ignores_a_few_spikes():
    profiles = list(_vad_set(300).profiles)
    profiles[7] = VADProfile(timestamp=profiles[7].timestamp, site="x",
                             heights_m=np.array([100.0, 500.0, 1000.0]),
                             wind_dir=np.array([180.0, 180.0, 180.0]),
                             wind_spd=np.array([10.0, 240.0, 30.0]))
    assert VADSet(profiles).hodograph_bound_kt() == 60.0


def test_radar_vad_tooltip_names_the_lidars_in_archive(app):
    from ui.controls.radar_controls import RadarControls
    controls = RadarControls()
    controls.configure_for_archive(True)
    assert "lidar" in controls._btn_vad.toolTip().lower()
    controls.configure_for_archive(False)
    assert controls._btn_vad.toolTip() == "View VAD wind profile hodograph"


# -- the window's handlers ----------------------------------------------------------------

class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _Clock(QObject):
    time_changed = pyqtSignal(object)

    def __init__(self, when):
        super().__init__()
        self.current_time = when


class _Button:
    def __init__(self, checked=True):
        self.checked = checked

    def isChecked(self):
        return self.checked


class _Wind:
    def __init__(self, sets=None):
        self.sets = sets
        self.fetches = 0

    def fetch(self):
        self.fetches += 1
        return True


class _Controls:
    def __init__(self, mode="coptersonde"):
        self.active_mode = mode
        self.listed = None

    def set_coptersondes(self, sets):
        self.listed = sets


class _Host(MainWindowClampsMixin, MainWindowSoundingMixin, MainWindowTrailsMixin):
    def __init__(self):
        self._archive = True
        self._vehicles = {}
        self.updated, self.hidden = [], []
        self.status_msg_label = _Label()
        self._time_ctrl = _Clock(T0)

    def update_vehicle_obs(self, obs):
        self.updated.append(obs)
        self._vehicles[obs.vehicle_id] = type("V", (), {"latest_obs": obs})()

    def _hide_vehicle(self, vid):
        self.hidden.append(vid)
        self._vehicles.pop(vid, None)


def test_trailers_follow_the_clock(app):
    from archive.fetchers.clamps_surface_playback import ClampsSurfacePlayback
    host = _Host()
    host._archive_clamps_surface = ClampsSurfacePlayback()
    first = Observation("CLAMPS1", 35.0, -97.0, T0, temperature_c=20.0)
    later = Observation("CLAMPS1", 35.0, -97.0, T0 + timedelta(minutes=1), temperature_c=21.0)
    host._archive_clamps_surface.install([first, later])
    host._update_clamps_surface(T0 + timedelta(seconds=30))
    assert host.updated == [first]
    host._update_clamps_surface(T0 + timedelta(seconds=40))      # same report: no update
    assert host.updated == [first]
    host._update_clamps_surface(T0 + timedelta(minutes=2))
    assert host.updated == [first, later]
    host._update_clamps_surface(T0 + timedelta(minutes=10))     # stale
    assert host.hidden == ["CLAMPS1"]


def test_trails_and_history_include_the_trailers(app):
    from archive.fetchers.clamps_surface_playback import ClampsSurfacePlayback
    from ui.app.main_window_vehicles import MainWindowVehiclesMixin
    host = _Host()
    host._archive_clamps_surface = ClampsSurfacePlayback()
    rows = [Observation("CLAMPS2", 35.0, -97.0, T0 + timedelta(minutes=m)) for m in (0, 5)]
    host._archive_clamps_surface.install(rows)
    host._archive_mqtt = type("M", (), {"vehicle_histories": lambda self: {}, "_data": {}})()
    assert host._trail_observations() == {"CLAMPS2": rows}
    assert MainWindowVehiclesMixin._get_archive_vehicle_history(host, "CLAMPS2") == rows[:1]


def test_vad_in_archive_asks_for_the_lidar_winds(app):
    host = _Host()
    host._archive_clamps_wind = _Wind()
    host._on_vad_requested()
    assert host._archive_clamps_wind.fetches == 1
    assert host.status_msg_label.text.startswith("Fetching lidar wind profiles")
    host._archive_clamps_wind.sets = {}
    host.status_msg_label.text = ""
    host._on_vad_requested()
    assert host._archive_clamps_wind.fetches == 2 and host.status_msg_label.text == ""


def test_wind_sets_open_the_vad_window(app, monkeypatch):
    from ui.dialogs import vad_dialog
    opened = []

    class FakeVAD:
        def __init__(self, site, parent=None, preloaded_set=None, start_time=None):
            opened.append((site, preloaded_set, start_time))

        def exec(self):
            pass

    monkeypatch.setattr(vad_dialog, "VADDialog", FakeVAD)
    host = _Host()
    one = _vad_set(3)
    host._on_archive_clamps_wind_ready({"CLAMPS2-VAD": one})
    assert opened == [("CLAMPS2 trailer VAD", one, T0)]

    from PyQt6.QtWidgets import QInputDialog
    other = _vad_set(4)
    choices = []
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(
        lambda parent, title, label, items, current, editable: (choices.append(items) or items[1], True)))
    host._on_archive_clamps_wind_ready({"CLAMPS2-VAD": one, "DLTRUCK1-DL1-VAD": other})
    assert choices == [["CLAMPS2 trailer VAD  (3 profiles)", "Lidar truck VAD (DL1)  (4 profiles)"]]
    assert opened[-1][:2] == ("Lidar truck VAD (DL1)", other)

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a: ("", False)))
    host._on_archive_clamps_wind_ready({"CLAMPS2-VAD": one, "DLTRUCK1-DL1-VAD": other})
    assert len(opened) == 2

    host._on_archive_clamps_wind_ready({})
    assert host.status_msg_label.text == "Lidar wind profiles: none for this date"
    host._on_archive_clamps_wind_error("timed out")
    assert host.status_msg_label.text == "Lidar wind profiles unavailable: timed out"


def test_the_truck_is_placed_from_one_second_data_then_mqtt(app):
    host = _Host()
    near = Observation("lid1", 35.1, -97.1, T0 + timedelta(seconds=20))
    mqtt = Observation("lid1", 36.0, -98.0, T0 + timedelta(seconds=5))
    host._archive_vehicle_obs = type("D", (), {"observations_by_vehicle": lambda self: {"lid1": [near]}})()
    host._archive_mqtt = type("M", (), {"vehicle_histories": lambda self: {"lid1": [mqtt]}})()
    assert host._lidar_truck_position(T0) == (35.1, -97.1, near.timestamp)
    assert host._lidar_truck_position(T0 + timedelta(minutes=5)) is None
    host._archive_vehicle_obs = type("D", (), {"observations_by_vehicle": lambda self: {}})()
    assert host._lidar_truck_position(T0) == (36.0, -98.0, mqtt.timestamp)


def test_coptersonde_results_reach_the_drawer_only_in_its_mode(app):
    host = _Host()
    host.btn_sounding = _Button(True)
    host.sounding_controls = _Controls("coptersonde")
    sets = {"A": _set("coptersonde", [T0])}
    host._on_archive_coptersondes_ready(sets)
    assert host.sounding_controls.listed == sets
    assert host.status_msg_label.text == "CopterSonde: 1 site(s)"
    host._on_archive_coptersondes_ready({})
    assert host.status_msg_label.text == "CopterSonde: no ascents by 04:00Z on 2023-03-03"
    host.sounding_controls = _Controls("obs")
    host._on_archive_coptersondes_ready(sets)
    assert host.sounding_controls.listed is None


def test_coptersonde_mode_fetches_on_the_archive_clock(app):
    host = _Host()
    host.btn_sounding = _Button(True)
    calls = []
    host._click_modes = type("C", (), {"set_prompt": lambda self, p: calls.append(("prompt", p))})()
    host.map_widget = type("W", (), {"__getattr__": lambda self, name: lambda *a: calls.append(name)})()
    host._archive_sounding = type("S", (), {"fetch_coptersondes": lambda self: calls.append("fetch")})()
    host._on_sounding_mode_changed("coptersonde")
    assert ("prompt", "CopterSonde profiles") in calls and calls[-1] == "fetch"
    assert host.status_msg_label.text == "Fetching CopterSonde profiles…"


def test_clamps_profiles_offer_the_nearest_obs_but_no_hrrr_in_archive(app, monkeypatch):
    from ui.app import main_window_sounding
    monkeypatch.setattr(main_window_sounding, "nearest_obs_station", lambda lat, lon: {"station_id": "LZK"})
    host = _Host()
    for source in ("clamps_tropoe", "coptersonde"):
        assert host._compute_available_sources(_set(source, [T0])) == {"obs": {"station_id": "LZK"}}
