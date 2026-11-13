"""Session restore: storage, each control's save/restore, and MainWindow's restore order."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ui import session_state
from ui.app.drawer_manager import DEFAULT_PINNED, DrawerManager
from ui.controls.hazard_controls import HazardControls
from ui.controls.mesoanalysis_controls import MESO_PRODUCT_GROUPS, MesoanalysisControls
from ui.controls.radar_controls import RadarControls
from ui.controls.satellite_controls import SatelliteControls
from ui.controls.sfcoa_controls import SfcoaControls
from ui.controls.surface_controls import SurfaceControls


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "storm.ini"), QSettings.Format.IniFormat)


# ── storage ────────────────────────────────────────────────────────────

def test_mode_key():
    assert session_state.mode_key() == "vehicle"
    assert session_state.mode_key(monitor=True) == "monitor"
    assert session_state.mode_key(viewer=True) == "viewer"
    # archive wins over the live flags it can be combined with
    assert session_state.mode_key(viewer=True, archive=True) == "archive"


def test_save_load_per_mode(settings):
    session_state.save("vehicle", {"radar": {"data": True}}, settings)
    assert session_state.load("vehicle", settings) == {"radar": {"data": True}}
    assert session_state.load("archive", settings) == {}
    session_state.clear("vehicle", settings)
    assert session_state.load("vehicle", settings) == {}


def test_unreadable_session_is_ignored(settings):
    settings.setValue("session/vehicle", "{not json")
    assert session_state.load("vehicle", settings) == {}
    settings.setValue("session/vehicle", "[1, 2]")
    assert session_state.load("vehicle", settings) == {}


# ── controls ───────────────────────────────────────────────────────────

def test_radar_round_trip(app, monkeypatch):
    monkeypatch.setattr(RadarControls, "_refresh_product_availability", lambda self, site: None)
    src = RadarControls()
    src._product_combo.setCurrentIndex(src._product_combo.findData("N0U"))
    src._chk_show_data.setChecked(True)
    src._opacity_slider.setValue(70)
    src._btn_sync_vehicles.setChecked(True)
    state = src.save_state()
    assert state == {"data": True, "product": "N0U", "opacity": 70,
                     "sync_vehicles": True, "dealias": False}

    dst = RadarControls()
    toggled, products = [], []
    dst.radar_toggled.connect(toggled.append)
    dst.product_changed.connect(products.append)
    opacities = []
    dst.opacity_changed.connect(opacities.append)
    dst.restore_state(state)
    assert dst.current_product() == "N0U" and dst.is_data_enabled()
    assert opacities == [0.7]
    assert products == ["N0U"] and toggled == [True]
    assert dst.is_sync_vehicles()


def test_radar_ignores_old_saved_sync(app, monkeypatch):
    monkeypatch.setattr(RadarControls, "_refresh_product_availability", lambda self, site: None)
    rc = RadarControls()
    rc.restore_state({"sync": False, "sync_vehicles": True})   # SYNC was removed
    assert rc.is_sync_vehicles() and "sync" not in rc.save_state()


def test_satellite_round_trip_and_pending_meso(app):
    src = SatelliteControls()
    src._opacity_slider.setValue(40)
    src._btn_conus.setChecked(True)
    assert src.save_state() == {"mode": "conus", "opacity": 40, "band": "vis"}

    dst = SatelliteControls()
    modes = []
    dst.mode_changed.connect(modes.append)
    dst.restore_state({"mode": "meso2", "opacity": 55})
    assert dst._opacity_slider.value() == 55
    # MESO-2 isn't known yet: nothing selected until its sector arrives
    assert dst.current_mode() == "" and modes == []
    dst.set_meso_available(1, True, {"north": 1})
    assert dst.current_mode() == ""
    dst.set_meso_available(2, True, {"north": 2})
    assert dst.current_mode() == "meso2" and modes == ["meso2"]


def test_satellite_band_round_trip(app):
    src = SatelliteControls()
    src.configure_for_archive(True)
    src._btn_ir.setChecked(True)
    assert src.save_state()["band"] == "ir"

    dst = SatelliteControls()
    bands = []
    dst.band_changed.connect(bands.append)
    dst.restore_state(src.save_state())
    assert dst.current_band() == "ir" and bands == ["ir"]
    dst.restore_state({"band": "bogus"})
    assert dst.current_band() == "ir"


def test_satellite_pending_meso_falls_back_to_conus(app):
    dst = SatelliteControls()
    dst.restore_state({"mode": "meso1"})
    dst.set_meso_available(1, False)
    assert dst.current_mode() == "conus"


def test_satellite_user_choice_cancels_pending_meso(app):
    dst = SatelliteControls()
    dst.restore_state({"mode": "meso1"})
    dst._btn_conus.setChecked(True)
    dst.set_meso_available(1, True, {"north": 1})
    assert dst.current_mode() == "conus"


def test_satellite_mode_can_be_left_to_launch_prefs(app):
    dst = SatelliteControls()
    dst.restore_state({"mode": "conus", "opacity": 30}, mode=False)
    assert dst.current_mode() == "" and dst._opacity_slider.value() == 30


def test_hazards_round_trip(app):
    src = HazardControls()
    src._btn_tor.setChecked(True)
    src._btn_watches.setChecked(True)
    src._btn_nws_warnings.setChecked(True)
    state = src.save_state()
    assert state == {"day": 1, "spc_mode": "tor", "watches": True, "mds": False,
                     "nws_warnings": True, "cwa": False}

    dst = HazardControls()
    modes = []
    dst.spc_mode_changed.connect(modes.append)
    dst.restore_state(state)
    assert dst.save_state() == state and modes == ["tor"]


def test_hazards_keep_launch_spc_product(app):
    dst = HazardControls()
    dst._btn_outlook.setChecked(True)     # launch dialog: auto SPC
    dst.restore_state({"spc_mode": "tor", "mds": True}, spc_mode=False)
    assert dst._active_spc_mode() == "outlook" and dst._btn_mds.isChecked()


def test_surface_round_trip_skips_asos(app):
    src = SurfaceControls()
    src._btn_ok.setChecked(True)
    src._btn_ks.setChecked(True)
    src._btn_asos.setChecked(True)
    src._chk_show_plots.setChecked(False)
    state = src.save_state()
    assert state == {"sources": ["ok", "ks"], "plots": False}

    dst = SurfaceControls()
    dst.restore_state(state)
    assert dst._btn_ok.isChecked() and dst._btn_ks.isChecked()
    assert not dst._btn_asos.isChecked() and not dst.plots_visible()


def _sfcoa_products(*ids):
    return [SimpleNamespace(variable_id=v, group="thermo", label=v.upper(), long_name="",
                            units="") for v in ids]


def test_sfcoa_products_wait_for_listing(app):
    dst = SfcoaControls()
    toggled = []
    dst.product_toggled.connect(lambda pid, on: toggled.append((pid, on)))
    dst.restore_state({"products": ["mlcape", "sbcape"]})
    assert dst.save_state() == {"products": ["mlcape", "sbcape"]}

    dst.set_products(_sfcoa_products("sbcape", "t2m"))
    assert dst.active_products() == {"sbcape"} and toggled == [("sbcape", True)]
    assert dst._product_buttons["sbcape"].isChecked()
    # not offered yet: still pending, and still saved
    assert dst.save_state() == {"products": ["mlcape", "sbcape"]}

    dst.set_products(_sfcoa_products("sbcape", "mlcape"))
    assert dst.active_products() == {"sbcape", "mlcape"}


def test_meso_products_wait_for_listing(app):
    product_id = MESO_PRODUCT_GROUPS[0][1][0][0]
    dst = MesoanalysisControls()
    dst.restore_state({"products": [product_id]})
    assert dst.active_products() == set()
    dst.set_products([SimpleNamespace(product_id=product_id)])
    assert dst.active_products() == {product_id}
    assert dst._product_buttons[product_id].isChecked()


# ── drawer manager ─────────────────────────────────────────────────────

class _FakeDrawer(QObject):
    opened = pyqtSignal()
    closed = pyqtSignal()

    def toggle_drawer(self, checked):
        (self.opened if checked else self.closed).emit()


class _FakeButton(QObject):
    def __init__(self, drawer):
        super().__init__()
        self._checked = False
        self._drawer = drawer

    def isChecked(self):
        return self._checked

    def setChecked(self, on):
        if on != self._checked:
            self._checked = on
            self._drawer.toggle_drawer(on)


def _manager(settings, *keys, child=()):
    mgr = DrawerManager(settings=settings)
    buttons = {}
    for key in keys + tuple(child):
        drawer = _FakeDrawer()
        buttons[key] = _FakeButton(drawer)
        mgr.add(key, drawer, buttons[key], child=key in child)
    return mgr, buttons


def test_open_keys_and_close_all(app, settings):
    mgr, b = _manager(settings, "radar", "hazards", "map", child=("route",))
    mgr.set_pinned("hazards", True)               # stays open beside radar
    b["hazards"].setChecked(True)
    b["radar"].setChecked(True)
    b["route"].setChecked(True)
    assert mgr.open_keys() == ["hazards", "radar"]
    mgr.close_all()
    assert mgr.open_keys() == [] and not b["route"].isChecked()


def test_reset_restores_default_pins(app, settings):
    mgr, _ = _manager(settings, "radar", "sounding", "prev_locs")
    mgr.set_pinned("radar", True)
    mgr.set_pinned("sounding", True)
    mgr.reset()
    assert {k for k in ("radar", "sounding", "prev_locs") if mgr.is_pinned(k)} == set(DEFAULT_PINNED)


# ── MainWindow restore order ───────────────────────────────────────────

pytest.importorskip("PyQt6.QtWebEngineWidgets")

from ui.app.main_window import MainWindow  # noqa: E402


class _FakeControl:
    def __init__(self, saved=None):
        self.saved = saved or {}
        self.restored = []

    def save_state(self):
        return self.saved

    def restore_state(self, state, **kwargs):
        self.restored.append((state, kwargs))


class _FakeRadar(_FakeControl):
    def __init__(self):
        super().__init__({"data": True})
        self.site = "KTLX"
        self.sites = []

    def current_site(self):
        return self.site

    def set_selected_site(self, site, emit=False):
        self.site = site
        self.sites.append((site, emit))


class _Host:
    _SESSION_CONTROLS = MainWindow._SESSION_CONTROLS
    _SESSION_SKIP_DRAWERS = MainWindow._SESSION_SKIP_DRAWERS


for _name in ("_session_mode", "_session_skipped", "_collect_session_state", "_restore_session"):
    setattr(_Host, _name, MainWindow.__dict__[_name])


def _host(settings, monkeypatch, monitor=False, archive=False, launch_sat="", launch_spc=False):
    monkeypatch.setattr(session_state, "_settings", lambda s=None: settings)
    h = _Host()
    h._monitor, h._viewer, h._archive = monitor, False, archive
    h._launch_auto_satellite, h._launch_auto_spc = launch_sat, launch_spc
    h._radar_fetcher = object()
    h.radar_controls = _FakeRadar()
    for key in ("satellite", "hazards", "surface", "mesoanalysis", "sfcoa"):
        setattr(h, MainWindow._SESSION_CONTROLS[key], _FakeControl({"k": key}))
    h.inits = []
    h._init_mesoanalysis = lambda: h.inits.append("mesoanalysis")
    h._init_sfcoa = lambda: h.inits.append("sfcoa")
    h._drawers, h.buttons = _manager(settings, "radar", "hazards", "sounding", "surface", "sfcoa")
    for key in h.buttons:
        h._drawers.set_pinned(key, True)          # pinned: several stay open, as saved
    return h


_SAVED = {
    "drawers": ["hazards", "sounding", "radar"],
    "radar": {"data": True, "product": "N0U"},
    "radar_site": "KFWS",
    "satellite": {"mode": "meso1"},
    "hazards": {"spc_mode": "tor"},
    "surface": {"sources": ["ok"]},
    "mesoanalysis": {"products": []},
    "sfcoa": {"products": ["sbcape"]},
}


def test_restore_live(app, settings, monkeypatch):
    session_state.save("vehicle", _SAVED, settings)
    h = _host(settings, monkeypatch)
    h._restore_session()
    assert h._session_restored
    assert h.radar_controls.restored == [(_SAVED["radar"], {})]
    assert h.radar_controls.sites == []          # vehicle mode picks the nearest site
    assert h.satellite_controls.restored == [(_SAVED["satellite"], {"mode": True})]
    assert h.hazard_controls.restored == [(_SAVED["hazards"], {"spc_mode": True})]
    assert h.surface_controls.restored == [(_SAVED["surface"], {})]
    # only layers with saved products start their fetcher
    assert h.inits == ["sfcoa"] and h.mesoanalysis_controls.restored == []
    # SOUNDING would enter its click mode, so it stays closed
    assert h._drawers.open_keys() == ["hazards", "radar"]


def test_launch_prefs_win(app, settings, monkeypatch):
    session_state.save("vehicle", _SAVED, settings)
    h = _host(settings, monkeypatch, launch_sat="conus", launch_spc=True)
    h._restore_session()
    assert h.satellite_controls.restored[0][1] == {"mode": False}
    assert h.hazard_controls.restored[0][1] == {"spc_mode": False}


def test_monitor_restores_radar_site(app, settings, monkeypatch):
    session_state.save("monitor", _SAVED, settings)
    h = _host(settings, monkeypatch, monitor=True)
    h._restore_session()
    assert h.radar_controls.sites == [("KFWS", True)]


def test_archive_reads_its_own_session_and_skips_live_sources(app, settings, monkeypatch):
    session_state.save("vehicle", _SAVED, settings)
    h = _host(settings, monkeypatch, archive=True)
    h._restore_session()
    assert h.hazard_controls.restored == []      # nothing saved for archive yet

    session_state.save("archive", _SAVED, settings)
    h = _host(settings, monkeypatch, archive=True)
    h._restore_session()
    assert h.radar_controls.restored == [] and h.surface_controls.restored == []
    assert h.inits == []
    assert h.hazard_controls.restored and h.satellite_controls.restored


def test_collect_round_trips(app, settings, monkeypatch):
    h = _host(settings, monkeypatch)
    h.buttons["radar"].setChecked(True)
    h.buttons["sounding"].setChecked(True)
    state = h._collect_session_state()
    assert state["drawers"] == ["radar"]
    assert state["radar"] == {"data": True} and state["radar_site"] == "KTLX"
    assert state["sfcoa"] == {"k": "sfcoa"}
