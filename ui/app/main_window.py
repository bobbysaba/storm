
import json
import logging
import os
import threading
import feature_flags
import runtime_flags
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QToolButton, QFrame, QFileDialog, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal, QSize

from ui.theme import DARK_THEME, set_pill_status
from ui.click_mode import ClickModeController
from ui.shortcuts import ARCHIVE, LIVE, ShortcutRegistry
from ui.widgets.shortcut_overlay import ShortcutOverlay
from ui import session_state, text_scale
from ui.map.widget import MapWidget
from ui.controls.radar_controls import RadarControls
from ui.controls.hazard_controls import HazardControls
from ui.controls.routing_controls import RoutingControls
from ui.widgets.nav_pill import NavPill
from ui.controls.deploy_locs_controls import DeployLocsControls
from ui.controls.satellite_controls import SatelliteControls
from ui.controls.map_controls import MapControls
from ui.controls.landcover_controls import LandcoverControls
from ui.controls.mesoanalysis_controls import MesoanalysisControls
from ui.controls.sfcoa_controls import SfcoaControls
from ui.controls.surface_controls import SurfaceControls
from ui.widgets.outlook_panel import OutlookPanel
from ui.sounding.controls import SoundingControls
from ui.app.overlay_geometry import bottom_left_y_avoiding, stack_gap
from ui.layer_status import LayerStatusBoard, LayerStatusDot
from ui.widgets.annotation_tools import AnnotationTools
from ui.widgets.responsive_toolbar import ResponsiveToolbar
import config
from core.annotation import Annotation
from core.storm_cone import StormCone
from core.drawing import DrawingAnnotation
from ui.widgets.layer_order_pill import LayerOrderPill
from ui.widgets.drawer import Drawer
from ui.app.drawer_manager import DrawerManager
from ui.app.main_window_debug import MainWindowDebugMixin
from ui.app.main_window_map_helpers import MainWindowMapHelpersMixin
from ui import palette as P
from ui.app.main_window_archive import MainWindowArchiveMixin
from ui.app.main_window_radar import MainWindowRadarMixin
from ui.app.main_window_hazards import MainWindowHazardsMixin
from ui.app.main_window_overlays import MainWindowOverlaysMixin
from ui.app.main_window_sounding import MainWindowSoundingMixin
from ui.app.main_window_map_objects import MainWindowMapObjectsMixin
from ui.app.main_window_vehicles import MainWindowVehiclesMixin
from ui.app.main_window_status import MainWindowStatusMixin
from ui.app.main_window_loop import MainWindowLoopMixin
from ui.app.main_window_util import NLCD_TILES_PATH, SATELLITE_TILES_PATH, _make_camera_icon

log = logging.getLogger(__name__)


class MainWindow(
    MainWindowArchiveMixin,
    MainWindowRadarMixin,
    MainWindowHazardsMixin,
    MainWindowOverlaysMixin,
    MainWindowSoundingMixin,
    MainWindowMapObjectsMixin,
    MainWindowVehiclesMixin,
    MainWindowStatusMixin,
    MainWindowLoopMixin,
    MainWindowMapHelpersMixin,
    MainWindowDebugMixin,
    QMainWindow,
):
    # emitted from background threads to update the discussion text panel safely.
    _panel_text_ready = pyqtSignal(int, str, str)
    # emitted from the decode thread when a scan has been decoded — carries
    _scan_decoded = pyqtSignal(int, str, str, object)
    # emitted from the decode thread on a decode failure — site, product.
    _radar_decode_failed = pyqtSignal(str, str)
    _render_ready = pyqtSignal(object)
    # emitted from the archive-render thread when an archive scan PNG is ready.
    _archive_render_ready = pyqtSignal(object)
    # emitted when the user aborts an archive loading session.
    session_aborted = pyqtSignal()

    _layout_pending = False

    def __init__(
        self,
        debug: bool = False,
        monitor: bool = False,
        viewer: bool = False,
        archive_time=None,    # datetime | None
    ):
        super().__init__()
        self._debug = debug
        self._monitor = monitor
        self._viewer = viewer
        self._archive_time = archive_time    # None = live mode
        self._archive = archive_time is not None
        self._current_radar_scan = None
        self._nws_active_phenoms: set[str] = set()  # phenom codes present in last NWS fetch
        self._mesoanalysis_fetcher = None
        self._mesoanalysis_current_metadata: dict | None = None
        self._mesoanalysis_visible = False
        self._mesoanalysis_active_products: set[str] = set()
        self._mesoanalysis_times_by_product: dict[str, list] = {}
        self._mesoanalysis_times = []
        self._mesoanalysis_overlay_cache: dict[tuple[str, str], dict] = {}
        self._sfcoa_fetcher = None
        self._sfcoa_current_metadata: dict | None = None
        self._sfcoa_visible = False
        self._sfcoa_active_products: set[str] = set()
        self._sfcoa_times = []
        self._sfcoa_variables_by_time: dict[str, list] = {}
        self._sfcoa_overlay_cache: dict[tuple[str, str], dict] = {}
        self._sfcoa_status_text = ""

        self.setWindowTitle(
            f"STORM  v{config.VERSION}"
            + (f"  [ARCHIVE {archive_time.strftime('%Y-%m-%d %H:%MZ')}]"
               if self._archive else "")
        )
        self.setMinimumSize(1024, 680)
        self.resize(1280, 800)

        # global dark theme applied once here — all children inherit via QSS cascade
        self.setStyleSheet(DARK_THEME)
        # build UI in dependency order
        self._runtime_safe = runtime_flags.FLAGS.runtime_safe

        self._init_layer_status()
        self._init_map()
        self._init_toolbar()
        self._init_statusbar()
        self._init_click_modes()
        self._init_vehicle_panel()
        self._radar_station_sites = self._load_radar_station_sites()
        self._radar_station_picker_visible = False
        self._radar_auto_site_pending = not monitor and not self._archive
        self._startup_sequence_started = False
        self._startup_local_pending = False
        self._startup_mqtt_pending = False
        self._post_startup_fetchers_started = False
        self._local_startup_timer = QTimer(self)
        self._local_startup_timer.setSingleShot(True)
        self._local_startup_timer.timeout.connect(self._complete_local_startup_phase)
        self._mqtt_startup_timer = QTimer(self)
        self._mqtt_startup_timer.setSingleShot(True)
        self._mqtt_startup_timer.timeout.connect(self._complete_mqtt_startup_phase)

        if self._archive:
            self.map_widget.map_ready.connect(self._begin_archive_startup)
        else:
            self.map_widget.map_ready.connect(self._begin_startup_sequence)

        # fine-grained startup toggles are for crash-isolation only.
        self._disable_radar = runtime_flags.FLAGS.disable_radar
        self._disable_mqtt = runtime_flags.FLAGS.disable_mqtt
        self._disable_annotations = runtime_flags.FLAGS.disable_annotations
        self._disable_deploy_locs = runtime_flags.FLAGS.disable_deploy_locs
        self._disable_data_inputs = runtime_flags.FLAGS.disable_data_inputs

        # features that require MQTT should be disabled when MQTT is disabled.
        if self._disable_mqtt:
            self._disable_annotations = True
            self._disable_data_inputs = True

        if self._runtime_safe:
            log.warning("Running in safe runtime mode (radar/MQTT/data inputs disabled)")
            self._init_measure()
            self._init_stations()
            self.status_msg_label.setText("Safe runtime mode - background services disabled")
            set_pill_status(self.status_msg_label, "warn")
        else:
            log.warning(
                "Startup toggles: radar=%s mqtt=%s annotations=%s deploy_locs=%s data_inputs=%s",
                "off" if self._disable_radar else "on",
                "off" if self._disable_mqtt else "on",
                "off" if self._disable_annotations else "on",
                "off" if self._disable_deploy_locs else "on",
                "off" if self._disable_data_inputs else "on",
            )

            self._init_measure()
            self._init_stations()

            if not self._disable_deploy_locs:
                self._init_deploy_locs()

        # wire map mousemove → status bar coordinate and zoom display
        self.map_widget.map_moved.connect(self._on_map_moved)

        # clock ticks every second
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._clock_layout_synced = False
        self._update_clock()

        # internet connectivity indicator — checks every 30 seconds
        self._start_net_check()

        # gps fix age indicator — polls every 5 seconds (vehicle mode only)
        self._last_local_obs_ts: float = 0.0
        if not (self._monitor or self._viewer):
            self._gps_indicator_timer = QTimer(self)
            self._gps_indicator_timer.timeout.connect(self._update_gps_indicator)
            self._gps_indicator_timer.start(5_000)
            self._update_gps_indicator()

        # in-ops update checker — background, non-blocking, no dialogs
        self._start_update_check()

        # restore window geometry and dock layout from last session.
        _s = QSettings("NSSL", "STORM")
        if _s.contains("geometry"):
            self.restoreGeometry(_s.value("geometry"))
        if _s.contains("windowState"):
            self.restoreState(_s.value("windowState"))
            # keep toolbar button in sync with current vehicle panel visibility
            self.btn_vehicles.setChecked(self.vehicle_panel.isVisible())

        # screenshot button — persistent, bottom-right above zoom controls
        self._init_screenshot_button()
        self._init_scan_button()

        # layer order pill — floats above the bottom-left status pill
        self._layer_pill = LayerOrderPill(self._map_container)
        self._layer_pill.order_changed.connect(self._apply_layer_order)
        self._layer_pill.order_changed.connect(lambda _: self._layout_overlays())
        self._layer_pill.size_changed.connect(self._layout_overlays)
        self._layer_pill.show()

        # extra startup layout passes avoid first-paint clipping in floating pills.
        QTimer.singleShot(0, self._layout_overlays)
        QTimer.singleShot(220, self._layout_overlays)

        # keyboard shortcuts (ui/shortcuts.py); ctrl+d works even outside --debug
        self._init_shortcuts()

        # auto-init debug panel when launched with --debug flag
        if debug:
            self._init_debug_panel()


    def _init_map(self):
        # container fills the QMainWindow central area; map + overlays are
        self._map_container = QWidget()
        self.setCentralWidget(self._map_container)

        self.map_widget = MapWidget()
        self.map_widget.setParent(self._map_container)

        # defer initial geometry until after all overlay widgets exist
        QTimer.singleShot(0, self._layout_overlays)






    def _begin_startup_sequence(self):
        if self._runtime_safe or self._startup_sequence_started:
            return
        self._startup_sequence_started = True
        self._begin_local_data_phase()

    def _begin_local_data_phase(self):
        if self._disable_data_inputs or self._monitor:
            self._complete_local_startup_phase()
            return
        self._startup_local_pending = True
        self._init_data_inputs()
        self._local_startup_timer.start(3000)

    def _complete_local_startup_phase(self):
        if self._startup_local_pending:
            self._startup_local_pending = False
            self._local_startup_timer.stop()
        self._begin_mqtt_phase()

    def _begin_mqtt_phase(self):
        if self._startup_mqtt_pending or hasattr(self, "_mqtt_client") or self._disable_mqtt:
            if self._disable_mqtt:
                self._complete_mqtt_startup_phase()
            return
        self._startup_mqtt_pending = True
        self._init_mqtt()
        if not self._disable_annotations:
            self._init_annotations()
            self._init_storm_cone()
            threading.Thread(target=self._fetch_current_json, daemon=True).start()
        if not config.MQTT_HOST:
            self._complete_mqtt_startup_phase()
        else:
            self._mqtt_startup_timer.start(4000)

    def _fetch_current_json(self):
        """One-shot background fetch of current.json to pre-populate annotations on launch."""
        from urllib.request import urlopen, Request

        url = f"{config.NSSL_API_ROOT}/current.json"
        try:
            headers = {"User-Agent": "Mozilla/5.0 STORM/1.0"}
            if config.NSSL_API_KEY:
                headers["X-API-Key"] = config.NSSL_API_KEY
            req = Request(url, headers=headers)
            with urlopen(req, timeout=10, context=config.NSSL_SSL_CONTEXT) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            log.warning("current.json fetch failed: %s", e)
            return

        n_ann = n_cone = n_drawing = 0
        for item in data.values():
            if item.get("deleted"):
                continue
            if "type_key" in item:
                try:
                    ann = Annotation.from_dict(item)
                    self._annotation_sync.annotation_received.emit(ann)
                    n_ann += 1
                except Exception as e:
                    log.warning("current.json annotation parse error: %s", e)
            elif "drawing_type" in item:
                try:
                    drawing = DrawingAnnotation.from_dict(item)
                    self._drawing_sync.drawing_received.emit(drawing)
                    n_drawing += 1
                except Exception as e:
                    log.warning("current.json drawing parse error: %s", e)
            elif "speed_kts" in item or "heading" in item:
                try:
                    cone = StormCone.from_dict(item)
                    self._storm_cone_sync.cone_received.emit(cone)
                    n_cone += 1
                except Exception as e:
                    log.warning("current.json cone parse error: %s", e)

        log.info("current.json loaded: %d annotations, %d cones, %d drawings", n_ann, n_cone, n_drawing)

    def _on_refresh_current_json(self):
        """Triggered by the Refresh button in the annotation toolbar."""
        threading.Thread(target=self._fetch_current_json, daemon=True).start()

    def _complete_mqtt_startup_phase(self):
        if self._startup_mqtt_pending:
            self._startup_mqtt_pending = False
            self._mqtt_startup_timer.stop()
        self._start_post_startup_fetchers()

    def _start_post_startup_fetchers(self):
        if self._post_startup_fetchers_started:
            return
        self._post_startup_fetchers_started = True

        s = QSettings()
        self._launch_auto_spc        = s.value("launch/auto_spc",        False, type=bool)
        self._launch_auto_nws        = s.value("launch/auto_nws",        False, type=bool)
        self._launch_auto_radar      = s.value("launch/auto_radar",      False, type=bool)
        self._launch_auto_satellite  = s.value("launch/auto_satellite",  "",    type=str)
        self._launch_auto_obs_ok     = s.value("launch/auto_obs_ok",     False, type=bool)
        self._launch_auto_obs_wtm    = s.value("launch/auto_obs_wtm",    False, type=bool)
        self._launch_auto_obs_ks     = s.value("launch/auto_obs_ks",     False, type=bool)
        self._launch_auto_obs_co     = s.value("launch/auto_obs_co",     False, type=bool)
        self._launch_auto_obs_ne     = s.value("launch/auto_obs_ne",     False, type=bool)

        if not self._disable_radar:
            self._init_radar()
        self._init_hazards()

        self._init_satellite()
        if not self._disable_radar and not self._archive:
            self._init_loop_sync()
        self._init_surface_obs()
        self._apply_launch_prefs()
        QTimer.singleShot(0, self._restore_session)

    def _apply_launch_prefs(self):
        """Auto-enable map layers based on launch dialog preferences."""
        if self._launch_auto_spc:
            self.hazard_controls._btn_outlook.setChecked(True)
        if self._launch_auto_nws:
            self.hazard_controls._btn_nws_warnings.setChecked(True)
        if not self._disable_radar and hasattr(self, "_radar_fetcher") and self._launch_auto_radar:
            self.radar_controls._chk_show_data.blockSignals(True)
            self.radar_controls._chk_show_data.setChecked(True)
            self.radar_controls._chk_show_data.blockSignals(False)
            self._auto_start_radar()
            self.btn_radar.setChecked(True)
        if self._launch_auto_obs_ok:
            self.surface_controls._btn_ok.setChecked(True)
        if self._launch_auto_obs_wtm:
            self.surface_controls._btn_wtm.setChecked(True)
        if self._launch_auto_obs_ks:
            self.surface_controls._btn_ks.setChecked(True)
        if self._launch_auto_obs_co:
            self.surface_controls._btn_co.setChecked(True)
        if self._launch_auto_obs_ne:
            self.surface_controls._btn_ne.setChecked(True)
        sat = self._launch_auto_satellite
        if sat == "conus":
            self.satellite_controls._btn_conus.setChecked(True)
        elif sat == "auto_meso":
            self._auto_meso_pending = True
            self._satellite_fetcher.meso_sectors_updated.connect(self._on_auto_meso_caps_ready)

    # control attribute per session-state key (each has save_state / restore_state)
    _SESSION_CONTROLS = {
        "radar": "radar_controls", "satellite": "satellite_controls",
        "hazards": "hazard_controls", "surface": "surface_controls",
        "mesoanalysis": "mesoanalysis_controls", "sfcoa": "sfcoa_controls",
    }
    # opening SOUNDING enters its map-click mode; don't do that on launch
    _SESSION_SKIP_DRAWERS = ("sounding",)

    def _session_mode(self) -> str:
        return session_state.mode_key(self._monitor, self._viewer, self._archive)

    def _session_skipped(self) -> set[str]:
        """Controls and drawers this launch mode has no data source for."""
        if self._archive:
            # archive radar follows the archive case, not the live product list
            return {"radar", "surface", "mesoanalysis", "sfcoa", "prev_locs", "annotate"}
        skip = set()
        if not hasattr(self, "_radar_fetcher"):
            skip.add("radar")
        return skip

    def _collect_session_state(self) -> dict:
        state = {"drawers": [k for k in self._drawers.open_keys()
                             if k not in self._SESSION_SKIP_DRAWERS]}
        for key, attr in self._SESSION_CONTROLS.items():
            control = getattr(self, attr, None)
            if control is not None:
                state[key] = control.save_state()
        state["radar_site"] = self.radar_controls.current_site()
        return state

    def _restore_session(self) -> None:
        """Bring back the last session's view for this launch mode (see
        ui/session_state.py). Runs after the launch-dialog prefs, which win
        where the two overlap: the satellite sector and the SPC product."""
        self._session_restored = True
        state = session_state.load(self._session_mode())
        if not state:
            return
        skip = self._session_skipped()
        if "radar" not in skip:
            site = state.get("radar_site")
            # other live modes pick the nearest site from GPS
            if self._monitor and site and site != self.radar_controls.current_site():
                self.radar_controls.set_selected_site(site, emit=True)
            self.radar_controls.restore_state(state.get("radar") or {})
        self.satellite_controls.restore_state(state.get("satellite") or {},
                                              mode=not self._launch_auto_satellite)
        self.hazard_controls.restore_state(state.get("hazards") or {},
                                           spc_mode=not self._launch_auto_spc)
        if "surface" not in skip:
            self.surface_controls.restore_state(state.get("surface") or {})
        for key, init in (("mesoanalysis", self._init_mesoanalysis), ("sfcoa", self._init_sfcoa)):
            control = getattr(self, self._SESSION_CONTROLS[key], None)
            saved = state.get(key) or {}
            if control is None or key in skip or not saved.get("products"):
                continue
            control.restore_state(saved)
            init()   # the fetcher lists products, which turns the saved ones on
        for key in state.get("drawers") or ():
            if key in skip or key in self._SESSION_SKIP_DRAWERS:
                continue
            try:
                btn = self._drawers.entry(key).button
            except KeyError:
                continue
            if not btn.isChecked():
                btn.setChecked(True)

    def _on_reset_layout(self) -> None:
        """MAP → RESET LAYOUT: close every drawer, default pins, AUTO, 100 % text."""
        self._drawers.close_all()
        self._drawers.reset()
        self.map_controls.set_drawer_mode(self._drawers.mode)
        self.map_controls.set_text_scale(100)
        self._on_text_scale_changed(100)
        self._request_layout()

    def _on_auto_meso_caps_ready(self, sectors: dict):
        """One-shot: pick the closer meso sector once caps have been fetched."""
        if not getattr(self, "_auto_meso_pending", False):
            return
        self._auto_meso_pending = False
        try:
            self._satellite_fetcher.meso_sectors_updated.disconnect(self._on_auto_meso_caps_ready)
        except Exception:
            pass

        vehicle = self._vehicles.get(config.VEHICLE_ID)
        lat = vehicle.lat if vehicle else config.HOME_LAT
        lon = vehicle.lon if vehicle else config.HOME_LON

        best_idx = None
        best_dist = float("inf")
        for idx in (1, 2):
            bbox = sectors.get(idx)
            if not bbox:
                continue
            clat = (bbox["north"] + bbox["south"]) / 2
            clon = (bbox["east"]  + bbox["west"])  / 2
            dist = (lat - clat) ** 2 + (lon - clon) ** 2
            if dist < best_dist:
                best_dist = dist
                best_idx = idx

        if best_idx == 1:
            self.satellite_controls._btn_meso1.setChecked(True)
        elif best_idx == 2:
            self.satellite_controls._btn_meso2.setChecked(True)
        else:
            self.satellite_controls._btn_conus.setChecked(True)


    def _init_toolbar(self):
        # groups: data | analysis | team | map (see ui/widgets/responsive_toolbar.py)
        tb = self._floating_toolbar = ResponsiveToolbar(self._map_container)
        tb.set_icon_px(self._toolbar_icon_px())

        # ── data ──
        self.btn_radar = tb.add_button("RADAR", "Show/hide radar controls", "radar")
        # radar controls drop down below the toolbar as a separate floating pill
        self.radar_controls = RadarControls(self._map_container)
        self.radar_controls.setObjectName("floatingToolbar")
        self.btn_radar.toggled.connect(self.radar_controls.toggle_drawer)
        self.radar_controls.opacity_changed.connect(self.map_widget.set_radar_opacity)
        self.btn_radar.toggled.connect(
            lambda on: self._set_radar_station_picker_visible(False) if not on else None
        )

        self.btn_satellite = tb.add_button(
            "SATELLITE", "Show/hide GOES satellite imagery overlay", "satellite"
        )
        self.satellite_controls = SatelliteControls(self._map_container)
        self.satellite_controls.setObjectName("floatingToolbar")
        self.btn_satellite.toggled.connect(self.satellite_controls.toggle_drawer)
        self.btn_satellite.toggled.connect(self._on_satellite_toggled)

        self.btn_hazards = tb.add_button(
            "HAZARDS", "Show/hide SPC and NWS hazard overlays", "hazards"
        )
        self.hazard_controls = HazardControls(self._map_container)
        self.hazard_controls.setObjectName("floatingToolbar")
        self.btn_hazards.toggled.connect(self.hazard_controls.toggle_drawer)

        self.outlook_panel = OutlookPanel(self._map_container)
        self.outlook_panel.closed.connect(self._layout_overlays)
        self._fetch_generation = 0
        self._panel_text_ready.connect(self._on_panel_text_ready)

        self.btn_surface = tb.add_button(
            "SURFACE", "Show/hide surface observation controls", "surface"
        )
        self.surface_controls = SurfaceControls(self._map_container)
        self.surface_controls.setObjectName("floatingToolbar")
        self.btn_surface.toggled.connect(self.surface_controls.toggle_drawer)

        if feature_flags.is_enabled("mesoanalysis"):
            self.btn_mesoanalysis = tb.add_button(
                "MESO", "Show/hide Satsquatch mesoanalysis contours", "meso", overflow=True
            )
            self.mesoanalysis_controls = MesoanalysisControls(self._map_container)
            self.mesoanalysis_controls.setObjectName("floatingToolbar")
            self.btn_mesoanalysis.toggled.connect(self.mesoanalysis_controls.toggle_drawer)
            self.btn_mesoanalysis.toggled.connect(self._on_mesoanalysis_drawer_toggled)

        if feature_flags.is_enabled("sfcoa"):
            self.btn_sfcoa = tb.add_button(
                "SFCOA", "Show/hide SFCOA mesoanalysis contours", "sfcoa", overflow=True
            )
            self.sfcoa_controls = SfcoaControls(self._map_container)
            self.sfcoa_controls.setObjectName("floatingToolbar")
            self.btn_sfcoa.toggled.connect(self.sfcoa_controls.toggle_drawer)
            self.btn_sfcoa.toggled.connect(self._on_sfcoa_drawer_toggled)

        # ── analysis ──
        tb.add_group()
        self.btn_sounding = tb.add_button(
            "SOUNDING", "Click map for HRRR point sounding or observed radiosonde data",
            "sounding",
        )
        self.sounding_controls = SoundingControls(self._map_container)
        self.sounding_controls.setObjectName("floatingToolbar")
        self.btn_sounding.toggled.connect(self.sounding_controls.toggle_drawer)
        self.btn_sounding.toggled.connect(self._on_sounding_mode_toggled)

        # ── team ──
        tb.add_group()
        self.btn_vehicles = tb.add_button("VEHICLES", "Toggle vehicle panel", "vehicles")

        self.btn_prev_locs = tb.add_button(
            "PREV LOCS", "Show previous truck deployment locations", "prev_locs", overflow=True
        )
        self.deploy_locs_controls = DeployLocsControls(self._map_container)
        self.deploy_locs_controls.setObjectName("floatingToolbar")
        self.btn_prev_locs.toggled.connect(self.deploy_locs_controls.toggle_drawer)

        self.btn_annotate = tb.add_button(
            "ANNOTATE", "Place road annotations and storm motion cone", "annotate"
        )
        # annotation tools drop down below the toolbar as a separate floating pill
        self.annotation_tools = AnnotationTools(self._map_container)
        self.annotation_tools.setObjectName("floatingToolbar")
        self.btn_annotate.toggled.connect(self.annotation_tools.toggle_drawer)

        # ── map ──
        tb.add_group()
        self.btn_map = tb.add_button("MAP", "Map tools and base layers", "map")
        nlcd_available = feature_flags.is_enabled("nlcd") and os.path.isfile(NLCD_TILES_PATH)
        satellite_available = (
            feature_flags.is_enabled("satellite_basemap")
            and os.path.isfile(SATELLITE_TILES_PATH)
        )
        self.map_controls = MapControls(
            route_available=not self._viewer,
            landcover_available=nlcd_available,
            satellite_available=satellite_available,
            parent=self._map_container,
        )
        self.map_controls.setObjectName("floatingToolbar")
        self.btn_map.toggled.connect(self.map_controls.toggle_drawer)

        self.btn_measure = self.map_controls.btn_measure
        self.btn_route = self.map_controls.btn_route
        self.btn_nlcd = self.map_controls.btn_landcover
        self.btn_satellite_basemap = self.map_controls.btn_satellite_basemap

        self.routing_controls = RoutingControls(self._map_container)
        self.routing_controls.setObjectName("floatingToolbar")
        self.btn_route.toggled.connect(self.routing_controls.toggle_drawer)
        self.routing_controls.enter_pick_mode.connect(self._enter_route_pick)
        self.routing_controls.cancel_pick_mode.connect(
            lambda: self._click_modes.exit("route_pick")
        )
        self.routing_controls.route_calculated.connect(self._on_route_calculated)
        self.routing_controls.route_cleared.connect(self._on_route_cleared)
        self.map_widget.map_pick_for_route.connect(self._on_map_pick_for_route)
        self.map_widget.private_pin_route_requested.connect(
            self._on_private_pin_route_requested
        )
        if nlcd_available:
            self.landcover_controls = LandcoverControls(self._map_container)
            self.landcover_controls.setObjectName("floatingToolbar")
            self.btn_nlcd.toggled.connect(self.landcover_controls.toggle_drawer)
            self.btn_nlcd.toggled.connect(self._on_nlcd_toggled)
            self.landcover_controls.opacity_changed.connect(self._on_nlcd_opacity_changed)
        if satellite_available:
            self.btn_satellite_basemap.toggled.connect(self._on_satellite_basemap_toggled)
        if self._archive:
            tb.set_available(self.btn_prev_locs, False)
            # no archive data sources for these — hide to prevent crashes.
            tb.set_available(self.btn_surface, False)    # _surface_fetcher / _surface_layer absent
            tb.set_available(self.btn_annotate, False)   # _mqtt_client / _annotation_sync / _drawing_sync absent
            if hasattr(self, "btn_mesoanalysis"):
                tb.set_available(self.btn_mesoanalysis, False)   # Satsquatch mesoanalysis is not archived
            if hasattr(self, "btn_sfcoa"):
                tb.set_available(self.btn_sfcoa, False)          # SFCOA mesoanalysis is not archived
        tb.finish()

        self.nav_pill = NavPill(self._map_container)
        self._pill_route_expanded = False
        self.routing_controls.nav_updated.connect(self._on_nav_updated)
        self.routing_controls.route_cleared.connect(self.nav_pill.nav_clear)
        self.routing_controls.route_cleared.connect(self._layout_overlays)
        self.routing_controls.route_cleared.connect(self._on_pill_route_cleared)
        self.nav_pill.open_drawer_requested.connect(self._on_pill_expand_requested)
        self.nav_pill.clear_requested.connect(self.routing_controls._on_clear)
        self.btn_route.toggled.connect(self._on_route_drawer_toggled)

        for drawer in self._map_container.findChildren(Drawer):
            drawer.geometry_changed.connect(self._request_layout)
        self._init_drawer_manager()
        self._init_layer_status_dots()

    def _init_layer_status(self):
        """One health record per data layer (see ui/layer_status.py)."""
        self._layer_status = LayerStatusBoard(parent=self)
        surface_sources = (
            "ok_mesonet", "wtm", "ks_mesonet", "co_mesonet", "ne_mesonet", "sd_mesonet", "asos",
        )
        hazard_sources = (
            "spc_outlook", "spc_tor", "spc_wind", "spc_hail", "spc_prob", "spc_sig",
            "spc_watches", "spc_mds", "nws_warnings",
        )
        layers = [
            ("radar", "Radar", ("radar",)),
            # active while a mode is selected, not while the drawer is open
            ("satellite", "Satellite", ()),
            ("hazards", "Hazards", hazard_sources),
            ("mesoanalysis", "Mesoanalysis", ("mesoanalysis",)),
            ("sfcoa", "SFCOA", ("sfcoa",)),
            ("surface", "Surface obs", surface_sources),
        ]
        for name, label, sources in layers:
            self._layer_status.add(
                name, label, sources, config.LAYER_AGE_THRESHOLDS_MIN.get(name, (None, None))
            )

    def _init_layer_status_dots(self):
        """Amber/red dot on each data layer's toolbar button; a red dot opens the drawer."""
        buttons = {
            "radar": "btn_radar",
            "satellite": "btn_satellite",
            "hazards": "btn_hazards",
            "mesoanalysis": "btn_mesoanalysis",
            "sfcoa": "btn_sfcoa",
            "surface": "btn_surface",
        }
        self._layer_status_dots = []
        self._overflow_dots = {}
        for name, btn_attr in buttons.items():
            btn = getattr(self, btn_attr, None)
            if btn is None:
                continue
            dot = LayerStatusDot(btn, self._layer_status[name])
            dot.activated.connect(lambda b=btn: b.setChecked(True))
            self._layer_status_dots.append(dot)
            # a copy on the ⋯ button, shown while this button sits in its menu
            more_dot = LayerStatusDot(self._floating_toolbar.more_button, self._layer_status[name])
            more_dot.activated.connect(lambda b=btn: b.setChecked(True))
            more_dot.set_allowed(False)
            self._overflow_dots[btn] = more_dot
        self._floating_toolbar.overflow_changed.connect(self._on_toolbar_overflow_changed)
        self._on_toolbar_overflow_changed(self._floating_toolbar.overflowed())
        if hasattr(self, "radar_controls"):
            self.radar_controls.bind_status(self._layer_status["radar"])
        if hasattr(self, "satellite_controls"):
            self.satellite_controls.bind_status(self._layer_status["satellite"])

    def _on_toolbar_overflow_changed(self, overflowed):
        for btn, dot in self._overflow_dots.items():
            dot.set_allowed(btn in overflowed)

    def _init_drawer_manager(self):
        """Decide which drawers share the screen (see ui/app/drawer_manager.py)."""
        self._drawers = DrawerManager(lambda: self._map_container.height(), parent=self)
        entries = [
            ("radar", "radar_controls", "btn_radar"),
            ("prev_locs", "deploy_locs_controls", "btn_prev_locs"),
            ("hazards", "hazard_controls", "btn_hazards"),
            ("satellite", "satellite_controls", "btn_satellite"),
            ("mesoanalysis", "mesoanalysis_controls", "btn_mesoanalysis"),
            ("sfcoa", "sfcoa_controls", "btn_sfcoa"),
            ("surface", "surface_controls", "btn_surface"),
            ("sounding", "sounding_controls", "btn_sounding"),
            ("annotate", "annotation_tools", "btn_annotate"),
            ("map", "map_controls", "btn_map"),
        ]
        for key, drawer_attr, btn_attr in entries:
            if not hasattr(self, drawer_attr):
                continue
            btn = getattr(self, btn_attr)
            self._drawers.add(key, getattr(self, drawer_attr), btn)
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, k=key, b=btn: self._show_drawer_pin_menu(k, b, pos)
            )
        # opened from inside the MAP drawer; never auto-closed
        self._drawers.add("route", self.routing_controls, self.btn_route, child=True)
        if hasattr(self, "landcover_controls"):
            self._drawers.add("landcover", self.landcover_controls, self.btn_nlcd, child=True)

        self._drawers.pins_changed.connect(self._refresh_drawer_pins)
        pin_keys = {self._drawers.entry(k).button: k for k in self._drawers.keys()}
        self._floating_toolbar.set_pin_handlers(
            lambda b: self._drawers.is_pinned(pin_keys[b]) if b in pin_keys else None,
            lambda b, on: self._drawers.set_pinned(pin_keys[b], on),
        )
        self._refresh_drawer_pins()
        self.map_controls.set_drawer_mode(self._drawers.mode)
        self.map_controls.drawer_mode_changed.connect(self._drawers.set_mode)
        self.map_controls.set_text_scale(text_scale.percent())
        self.map_controls.text_scale_changed.connect(self._on_text_scale_changed)
        self.map_controls.reset_layout_requested.connect(self._on_reset_layout)
        self._drawers.mode_changed.connect(self._request_layout)

    def _show_drawer_pin_menu(self, key: str, button: QToolButton, pos):
        menu = QMenu(self)
        act = menu.addAction("Keep open when other drawers open")
        act.setCheckable(True)
        act.setChecked(self._drawers.is_pinned(key))
        act.toggled.connect(lambda on: self._drawers.set_pinned(key, on))
        menu.exec(button.mapToGlobal(pos))

    def _refresh_drawer_pins(self):
        for key in ("radar", "prev_locs", "hazards", "satellite", "mesoanalysis", "sfcoa",
                    "surface", "sounding", "annotate", "map"):
            try:
                btn = self._drawers.entry(key).button
            except KeyError:
                continue
            pinned = self._drawers.is_pinned(key)
            if btn.property("pinned") != pinned:
                btn.setProperty("pinned", pinned)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            tip = btn.toolTip().removesuffix(" (pinned open)")
            btn.setToolTip(tip + (" (pinned open)" if pinned else ""))

    def _on_nav_updated(self, step_text: str, summary: str):
        """Show/refresh the nav pill — but only when the toolbar drawer is open."""
        self.nav_pill.update_nav(step_text, summary)
        if self.btn_route.isChecked():
            self.nav_pill.hide()
        self._layout_overlays()

    def _on_pill_expand_requested(self):
        """Toggle the floating routing panel below the nav pill."""
        self._pill_route_expanded = not self._pill_route_expanded
        self.nav_pill.set_expanded(self._pill_route_expanded)
        self.routing_controls.toggle_drawer(self._pill_route_expanded)
        self._request_layout()

    def _on_pill_route_cleared(self):
        """Collapse the pill-expanded routing panel when the route is cleared."""
        if self._pill_route_expanded:
            self._pill_route_expanded = False
            self.nav_pill.set_expanded(False)
            self.routing_controls.toggle_drawer(False)

    def _on_route_drawer_toggled(self, checked: bool):
        """Hide pill while toolbar drawer is open; restore it when drawer closes."""
        if checked:
            # if pill-expand was open, close it — toolbar takes over
            if self._pill_route_expanded:
                self._pill_route_expanded = False
                self.nav_pill.set_expanded(False)
            self.nav_pill.hide()
        elif self.routing_controls._last_result is not None:
            self.nav_pill.show()
        self._layout_overlays()

    def _on_route_calculated(self, result):
        self._set_layer_active("route", True)
        import json as _json
        geojson_str = _json.dumps(result.geometry)
        dlat, dlon  = result.dest_latlon
        self.map_widget.set_route(geojson_str, dlon, dlat)

    def _on_route_cleared(self):
        self._set_layer_active("route", False)
        self.map_widget.clear_route()
        self._click_modes.exit("route_pick")
        self.map_widget.set_route_pick_mode(False)

    def _enter_route_pick(self):
        self._click_modes.enter("route_pick", "route — click map to pick the point")
        self.map_widget.set_route_pick_mode(True)

    def _on_map_pick_for_route(self, lat: float, lon: float):
        # the pick lands first; exiting afterwards finds nothing left to cancel
        self.routing_controls.on_map_pick(lat, lon)
        self._click_modes.exit("route_pick")

    def _exit_route_pick(self, _mode: str, _next: str):
        self.routing_controls.cancel_pick()
        self.map_widget.set_route_pick_mode(False)

    def _on_private_pin_route_requested(self, lat: float, lon: float, label: str):
        if self._viewer or self._monitor or self._archive:
            return
        if not hasattr(self, "routing_controls"):
            return
        self.routing_controls.route_to_pin(lat, lon, label)
        self.btn_route.setChecked(True)
        self._layout_overlays()

    # toolbar buttons driven by single-key shortcuts
    _SHORTCUT_BUTTONS = {
        "radar": "btn_radar", "satellite": "btn_satellite", "hazards": "btn_hazards",
        "surface": "btn_surface", "vehicles": "btn_vehicles", "sounding": "btn_sounding",
        "measure": "btn_measure", "annotate": "btn_annotate",
    }

    def _init_shortcuts(self):
        """Bind the shortcut table for this launch mode and build the ? overlay."""
        mode = ARCHIVE if self._archive else LIVE
        sc = self._shortcuts = ShortcutRegistry(self, mode)
        for action, attr in self._SHORTCUT_BUTTONS.items():
            btn = getattr(self, attr, None)
            if btn is None:
                continue
            sc.bind(action, lambda b=btn: self._click_if_shown(b))
            btn.setToolTip(sc.tooltip(action, btn.toolTip()))

        sc.bind("escape", self._on_escape_pressed)
        sc.bind("loop", self.radar_controls.toggle_loop)
        sc.bind("frame_prev", lambda: self.radar_controls.step_frame(-1))
        sc.bind("frame_next", lambda: self.radar_controls.step_frame(1))
        for action, method in (("play", "toggle_play"), ("step_back", "step_back"),
                               ("step_forward", "step_forward"), ("skip_start", "skip_start"),
                               ("skip_end", "skip_end")):
            sc.bind(action, lambda m=method: self._call_archive_controls(m))
        sc.bind("screenshot", lambda: self._click_if_shown(self.btn_screenshot))
        self.btn_screenshot.setToolTip(sc.tooltip("screenshot", self.btn_screenshot.toolTip()))
        sc.bind("help", self._toggle_shortcut_overlay)
        sc.bind("debug", self._toggle_debug_panel)
        sc.bind("error_log", self._toggle_error_log_panel)

        self._shortcut_overlay = ShortcutOverlay(mode, self._map_container)

    @staticmethod
    def _click_if_shown(btn) -> None:
        # buttons hidden for this mode or feature set ignore their key
        if not btn.isHidden() and btn.isEnabled():
            btn.click()

    def _call_archive_controls(self, method: str) -> None:
        controls = getattr(self, "_archive_controls", None)
        if controls is not None:
            getattr(controls, method)()

    def _toggle_shortcut_overlay(self) -> None:
        self._shortcut_overlay.toggle()
        self._request_layout()

    def _toolbar_icon_px(self) -> int:
        return round(14 * text_scale.percent() / 100)


    def _init_click_modes(self):
        """One map click mode at a time (ui/click_mode.py), named by an on-map banner."""
        self._click_modes = ClickModeController(self)
        cm = self._click_modes
        cm.register("sounding", lambda _m, _n: self.btn_sounding.setChecked(False))
        cm.register("measure", lambda _m, _n: self.btn_measure.setChecked(False),
                    self._on_measure_click)
        cm.register("route_pick", self._exit_route_pick)
        cm.register("annotation", self._exit_annotation_mode, self._on_annotation_place_click)
        cm.register("storm_motion_fix", self._exit_annotation_mode, self._on_storm_motion_fix_click)
        cm.register("drawing", self._exit_drawing_mode, self._on_drawing_click)
        cm.register("asos_bbox", self._exit_asos_bbox)
        cm.prompt_changed.connect(self._on_click_mode_prompt)

        self._mode_banner = QLabel(self._map_container)
        self._mode_banner.setObjectName("clickModeBanner")
        self._mode_banner.setVisible(False)

    def _on_click_mode_prompt(self, prompt: str):
        self._show_mode_banner(prompt, cancellable=True)

    def _show_mode_banner(self, text: str, cancellable: bool):
        if text:
            self._mode_banner.setText(f"▶  {text}  ·  Esc to exit" if cancellable else f"▶  {text}")
        self._mode_banner.setVisible(bool(text))
        self._request_layout()

    def _status_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {P.DIVIDER}; margin: 4px 0;")
        return div

    def closeEvent(self, event):
        _s = QSettings("NSSL", "STORM")
        _s.setValue("geometry", self.saveGeometry())
        _s.setValue("windowState", self.saveState())
        from ui.widgets.layer_order_pill import save_layer_order
        save_layer_order(self._layer_pill.current_order())
        # only once restored: quitting mid-startup would overwrite it with defaults
        if getattr(self, "_session_restored", False):
            try:
                session_state.save(self._session_mode(), self._collect_session_state())
            except Exception:
                log.exception("could not save session state")

        # stop all background workers before closing so threads don't outlive
        self._clock_timer.stop()
        if hasattr(self, "_vehicle_history"):
            self._vehicle_history.close()
        if hasattr(self, "_update_check_timer"):
            self._update_check_timer.stop()
        if hasattr(self, "_time_ctrl"):
            self._time_ctrl.pause()
        if hasattr(self, "_radar_fetcher"):
            self._radar_fetcher.stop()
        if hasattr(self, "_hazard_fetcher"):
            self._hazard_fetcher.stop()
        if hasattr(self, "_satellite_fetcher"):
            self._satellite_fetcher.stop()
        if hasattr(self, "_surface_fetcher"):
            self._surface_fetcher.stop()
        if hasattr(self, "_gps_reader") and self._gps_reader is not None:
            self._gps_reader.stop()
        if hasattr(self, "_obs_watcher") and self._obs_watcher is not None:
            self._obs_watcher.stop()
        if hasattr(self, "_mqtt_client"):
            self._mqtt_client.disconnect()
        self._cleanup_debug_panel()

        super().closeEvent(event)
        QApplication.quit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_overlays()

    def _layout_overlays(self):
        """Position map + floating overlays within the container after any resize."""
        r = self._map_container.rect()

        MARGIN = 8

        # map always fills the full container (overlays float on top)
        self.map_widget.setGeometry(r)

        # toolbar: shrink-wrap to content, center horizontally, float with margin
        if hasattr(self, "_floating_toolbar"):
            # drops to icon-only, then the ⋯ menu, as the window narrows
            self._floating_toolbar.fit(r.width() - 2 * MARGIN)
            self._floating_toolbar.adjustSize()
            tb_w = self._floating_toolbar.width()
            tb_h = self._floating_toolbar.height()
            tb_x = max(0, (r.width() - tb_w) // 2)
            self._floating_toolbar.setGeometry(tb_x, MARGIN, tb_w, tb_h)
            self._floating_toolbar.raise_()

            # stack open pills below the toolbar to avoid overlap
            _drop_y = MARGIN + tb_h + 4
            _stack_y = _drop_y

            def _stack(widget):
                nonlocal _stack_y
                if widget is None:
                    return
                layout = widget.layout()
                if layout is not None:
                    layout.activate()
                widget.adjustSize()
                w = widget.width()
                h = widget.height()
                x = max(0, (r.width() - w) // 2)
                widget.setGeometry(x, _stack_y, w, h)
                widget.raise_()
                _stack_y += h + stack_gap(h)

            if hasattr(self, "radar_controls") and self.radar_controls.occupies_space():
                _stack(self.radar_controls)
            if hasattr(self, "vehicle_panel") and self.vehicle_panel.isVisible():
                # apply a session-scoped size watermark so the panel only ever
                # grows while open — eliminates the constant resize churn when
                # vehicles stream in/out. resets when the toolbar toggle hides it.
                vp = self.vehicle_panel
                if vp.layout() is not None:
                    vp.layout().activate()
                vp.adjustSize()
                wm_w, wm_h = self._vehicle_panel_size_watermark
                w = max(vp.width(), wm_w)
                h = max(vp.height(), wm_h)
                self._vehicle_panel_size_watermark = (w, h)
                x = max(0, (r.width() - w) // 2)
                vp.setGeometry(x, _stack_y, w, h)
                vp.raise_()
                _stack_y += h + stack_gap(h)
            if hasattr(self, "vehicle_detail_panel") and self.vehicle_detail_panel.isVisible():
                _stack(self.vehicle_detail_panel)
            if hasattr(self, "deploy_locs_controls") and self.deploy_locs_controls.occupies_space():
                _stack(self.deploy_locs_controls)
            if hasattr(self, "hazard_controls") and self.hazard_controls.occupies_space():
                _stack(self.hazard_controls)
            if hasattr(self, "satellite_controls") and self.satellite_controls.occupies_space():
                _stack(self.satellite_controls)
            if hasattr(self, "mesoanalysis_controls") and self.mesoanalysis_controls.occupies_space():
                _stack(self.mesoanalysis_controls)
            if hasattr(self, "sfcoa_controls") and self.sfcoa_controls.occupies_space():
                _stack(self.sfcoa_controls)
            if hasattr(self, "surface_controls") and self.surface_controls.occupies_space():
                _stack(self.surface_controls)
            if hasattr(self, "sounding_controls") and self.sounding_controls.occupies_space():
                _stack(self.sounding_controls)
            if hasattr(self, "annotation_tools") and self.annotation_tools.occupies_space():
                _stack(self.annotation_tools)
            if hasattr(self, "map_controls") and self.map_controls.occupies_space():
                _stack(self.map_controls)
            if hasattr(self, "landcover_controls") and self.landcover_controls.occupies_space():
                _stack(self.landcover_controls)
            if hasattr(self, "routing_controls") and self.btn_route.isChecked():
                _stack(self.routing_controls)

            if hasattr(self, "_mode_banner") and self._mode_banner.isVisible():
                mb = self._mode_banner
                mb.adjustSize()
                mb.setGeometry(max(MARGIN, (r.width() - mb.width()) // 2), _stack_y,
                               mb.width(), mb.height())
                mb.raise_()

        # nav pill — upper-right, below toolbar (hidden while toolbar drawer is open)
        _np_w = min(340, r.width() - 2 * MARGIN)
        _nav_y = MARGIN + (tb_h + 4 if hasattr(self, "_floating_toolbar") else 0)
        _np_h = 0
        if hasattr(self, "nav_pill") and self.nav_pill.isVisible():
            self.nav_pill.setFixedWidth(_np_w)
            self.nav_pill.layout().activate()
            _np_h = self.nav_pill.sizeHint().height()
            self.nav_pill.setGeometry(r.width() - _np_w - MARGIN, _nav_y, _np_w, _np_h)
            self.nav_pill.raise_()

        # routing panel — floats below the nav pill when opened via ▾ button
        if (hasattr(self, "routing_controls") and self._pill_route_expanded
                and not self.btn_route.isChecked()):
            rc = self.routing_controls
            rc_y = _nav_y + _np_h + 4
            rc.adjustSize()
            rc.setGeometry(r.width() - _np_w - MARGIN, rc_y, _np_w, rc.height())
            rc.raise_()

        # outlook panel — right side, below toolbar, above status pill
        if hasattr(self, "outlook_panel"):
            op = self.outlook_panel
            top = _drop_y if hasattr(self, "_floating_toolbar") else MARGIN
            bottom_pad = 40  # clear status pills
            panel_h = max(100, r.height() - top - MARGIN - bottom_pad)
            op.setGeometry(r.width() - OutlookPanel.PANEL_WIDTH - MARGIN, top,
                           OutlookPanel.PANEL_WIDTH, panel_h)
            op.raise_()

        # archive controls bar — centered, pinned to bottom
        arc_bar_h = 0
        archive_rect = None
        if hasattr(self, "_archive_controls"):
            ac = self._archive_controls
            ac_w = min(r.width() - 2 * MARGIN, 680)
            ac.setFixedWidth(ac_w)
            ac.adjustSize()
            ac_h = ac.sizeHint().height()
            arc_bar_h = ac_h + MARGIN
            ac_x = max(MARGIN, (r.width() - ac_w) // 2)
            ac.setGeometry(ac_x, r.height() - arc_bar_h, ac_w, ac_h)
            archive_rect = (ac_x, r.height() - arc_bar_h, ac_w, ac_h)
            ac.raise_()

        # shortcut list (? key) — centered over the map
        if hasattr(self, "_shortcut_overlay") and self._shortcut_overlay.isVisible():
            so = self._shortcut_overlay
            so.adjustSize()
            so.move(max(MARGIN, (r.width() - so.width()) // 2),
                    max(MARGIN, (r.height() - so.height()) // 2))
            so.raise_()

        # debug pill — bottom-center, sits above archive controls (or above bottom margin)
        if hasattr(self, "_debug_pill"):
            dp = self._debug_pill
            dp_w = dp.width()
            dp_h = dp.height()
            dp_x = max(MARGIN, (r.width() - dp_w) // 2)
            dp_bottom = r.height() - arc_bar_h - MARGIN if arc_bar_h else r.height() - MARGIN
            dp.move(dp_x, dp_bottom - dp_h)
            dp.raise_()

        # Left status pill stays bottom-left when there is room.  On narrower
        # archive layouts it moves above the playback bar instead of covering it.
        if hasattr(self, "_status_left"):
            self._status_left.adjustSize()
            sl = self._status_left.size()
            _status_y = bottom_left_y_avoiding(
                r.height(),
                MARGIN,
                sl.width(),
                sl.height(),
                archive_rect,
            )
            self._status_left.setGeometry(
                MARGIN, _status_y,
                sl.width(), sl.height()
            )
            self._status_left.raise_()

        # layer order pill — sits directly above the status pill
        if hasattr(self, "_layer_pill"):
            lp_w = self._layer_pill.width()
            lp_h = self._layer_pill.height()
            # anchor bottom of pill to just above the status pill
            lp_bottom = _status_y - 4
            lp_y = lp_bottom - lp_h
            self._layer_pill.move(MARGIN, lp_y)
            self._layer_pill._relayout()
            self._layer_pill.raise_()

        # keep the drawer stack above the bottom overlays (see DrawerManager)
        if hasattr(self, "_drawers"):
            bottom = archive_rect[1] if archive_rect else r.height()
            if hasattr(self, "_status_left"):
                bottom = min(bottom, _status_y)
            fixed = [w.height() for w in (getattr(self, "vehicle_panel", None),
                                          getattr(self, "vehicle_detail_panel", None),
                                          getattr(self, "_mode_banner", None))
                     if w is not None and w.isVisible()]
            skip = ("route",) if self._pill_route_expanded else ()
            self._drawers.enforce_fit(bottom - 6 - _drop_y, fixed, skip)

        # re-center button — bottom-right, above MapLibre zoom controls (~70px tall)
        _ZOOM_CTRL_H = 70   # approximate height of MapLibre NavigationControl
        _GAP = 6
        _recenter_top = r.height() - _ZOOM_CTRL_H - _GAP
        if hasattr(self, "btn_recenter"):
            btn_w = self.btn_recenter.width()
            btn_h = self.btn_recenter.height()
            self.btn_recenter.move(
                r.width() - btn_w - MARGIN,
                _recenter_top - btn_h,
            )
            self.btn_recenter.raise_()
            if self.btn_recenter.isVisible():
                _recenter_top = _recenter_top - btn_h - _GAP

        # screenshot button — stacks above re-center button (or above zoom ctrls)
        if hasattr(self, "btn_screenshot"):
            sb_w = self.btn_screenshot.width()
            sb_h = self.btn_screenshot.height()
            self.btn_screenshot.move(
                r.width() - sb_w - MARGIN,
                _recenter_top - sb_h,
            )
            self.btn_screenshot.raise_()
            if self.btn_screenshot.isVisible():
                _recenter_top = _recenter_top - sb_h - _GAP

        # scan button — vehicle mode only, near the other bottom map controls
        if hasattr(self, "btn_scan"):
            sc_w = self.btn_scan.width()
            sc_h = self.btn_scan.height()
            self.btn_scan.move(
                r.width() - sc_w - MARGIN,
                _recenter_top - sc_h,
            )
            self.btn_scan.raise_()


    def _on_text_scale_changed(self, pct: int) -> None:
        if text_scale.set_percent(pct):
            self._floating_toolbar.set_icon_px(self._toolbar_icon_px())
            self._floating_toolbar.invalidate()
            self._request_layout()

    def _request_layout(self, *_args):
        """Coalesce overlay re-layout to once per event-loop turn."""
        if self._layout_pending:
            return
        self._layout_pending = True
        QTimer.singleShot(0, self._run_requested_layout)

    def _run_requested_layout(self):
        self._layout_pending = False
        self._layout_overlays()





        # _nws_active_phenoms is updated by _on_nws_raw_phenoms (raw signal),

    _INJECT_COOLDOWN_MS = 600




    def _on_escape_pressed(self):
        """Esc closes the shortcut list, else leaves the map click mode, else closes the newest drawer."""
        overlay = getattr(self, "_shortcut_overlay", None)
        if overlay is not None and not overlay.isHidden():
            overlay.hide()
            return
        if self._click_modes.exit():
            return
        if hasattr(self, "_drawers"):
            self._drawers.close_newest(skip=("route",) if self._pill_route_expanded else ())





    def _init_screenshot_button(self) -> None:
        self.btn_screenshot = QToolButton(self._map_container)
        self.btn_screenshot.setIcon(_make_camera_icon(18))
        self.btn_screenshot.setIconSize(QSize(18, 18))
        self.btn_screenshot.setToolTip("Save a screenshot of the map")
        self.btn_screenshot.setFixedSize(32, 32)
        self.btn_screenshot.setStyleSheet(P.qss("""
            QToolButton {
                background: $bg_hover_2;
                border: 1px solid $border_2;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: $bg_hover_4;
                border-color: $info;
            }
            QToolButton:pressed {
                background: $bg_hover_3;
            }
        """))
        self.btn_screenshot.clicked.connect(self._on_screenshot_clicked)
        self.btn_screenshot.show()

    def _on_screenshot_clicked(self) -> None:
        """Hide the floating toolbar, capture the window, then prompt to save."""
        to_hide = [self.btn_screenshot]
        if hasattr(self, "_floating_toolbar"):
            to_hide.append(self._floating_toolbar)
        prev_visible = [(w, w.isVisible()) for w in to_hide]
        for w, _ in prev_visible:
            w.hide()
        # let Qt repaint without the hidden widgets before grabbing.
        QApplication.processEvents()

        try:
            screen = self.windowHandle().screen() if self.windowHandle() else QApplication.primaryScreen()
            if screen is None:
                log.warning("Screenshot: no screen available")
                return
            # grab the main window rect in screen coordinates.
            top_left = self.mapToGlobal(self.rect().topLeft())
            dpr = screen.devicePixelRatio()
            pix = screen.grabWindow(
                0,
                int(top_left.x()),
                int(top_left.y()),
                int(self.width()),
                int(self.height()),
            )
            if not pix.isNull():
                pix.setDevicePixelRatio(dpr)
        finally:
            for w, was_visible in prev_visible:
                if was_visible:
                    w.show()
            self._layout_overlays()

        if pix.isNull():
            log.warning("Screenshot: grabbed pixmap was null")
            return

        from pathlib import Path
        default_dir = Path.home() / "Pictures"
        if not default_dir.exists():
            default_dir = Path.home()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = str(default_dir / f"storm_{ts}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot", default_path, "PNG Image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not pix.save(path, "PNG"):
            log.warning("Screenshot: failed to save to %s", path)
        else:
            log.info("Screenshot saved to %s", path)


