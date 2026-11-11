
import csv
import json
import logging
import os
import threading
import time
import feature_flags
import runtime_flags
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QToolButton,
    QFrame, QCheckBox, QPushButton, QGridLayout, QFileDialog, QSizePolicy, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal, QSize, QStandardPaths

from ui.theme import DARK_THEME, set_pill_status
from ui.click_mode import ClickModeController, kind_of
from ui.shortcuts import ARCHIVE, LIVE, ShortcutRegistry
from ui.widgets.shortcut_overlay import ShortcutOverlay
from ui import session_state, text_scale
from ui.map.widget import MapWidget
from ui.controls.radar_controls import RadarControls
from ui.controls.hazard_controls import HazardControls
from ui.controls.routing_controls import RoutingControls, _make_loc_icon
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
from archive.vehicle_speed import calculate_vehicle_speed, format_vehicle_speed
from ui.app.overlay_geometry import bottom_left_y_avoiding, stack_gap
from ui.layer_status import LayerStatusBoard, LayerStatusDot
from ui.widgets.annotation_tools import AnnotationTools
from ui.widgets.responsive_toolbar import ResponsiveToolbar
from ui.dialogs.annotation_dialog import AnnotationPlaceDialog, AnnotationEditDialog, AnnotationMoveConfirmDialog
from ui.dialogs.drawing_dialog import (
    DrawingTitleDialog, DrawingEditDialog, DrawingPlaceConfirmDialog,
    DrawingMoveConfirmDialog,
)
from ui.dialogs.storm_cone_dialog import (
    StormConeInputDialog, StormConePlaceConfirmDialog, StormConeMotionConfirmDialog,
    StormConeMoveConfirmDialog,
)
from ui.dialogs.scan_sector_dialog import ScanSectorDialog
from data.update_checker import UpdateWorker
import config
from core.annotation import Annotation, ANNOTATION_TYPE_MAP
from core.storm_cone import StormCone, motion_from_fixes
from core.scan_sector import ScanSector, feature_collection
from core.drawing import DrawingAnnotation, DRAWING_TYPE_MAP, FRONT_TYPE_KEYS
from core.observation import Observation
from core.vehicle_history import VehicleHistoryStore
from core.vehicle import Vehicle
from network.mqtt_client import MQTTClient
from network.annotation_sync import AnnotationSync
from network.storm_cone_sync import StormConeSync
from network.drawing_sync import DrawingSync
from network.scan_sector_sync import ScanSectorSync
from network.vehicle_sync import VehicleSync
from data.ingest.gps_reader import GPSReader
from data.ingest.obs_file_watcher import ObsFileWatcher, FieldMap
from ui.layers.station_plot_layer import StationPlotLayer
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
from ui.app.main_window_util import (
    NLCD_TILES_PATH, SATELLITE_TILES_PATH, SCAN_MOVE_FIXES_TO_STOP, SCAN_MOVE_THRESHOLD_KM,
    VEHICLE_PANEL_WIDTH, _ANNOTATE_KINDS, _NetChecker, _clear_layout, _coords_close,
    _make_camera_icon,
)

log = logging.getLogger(__name__)


class MainWindow(
    MainWindowSoundingMixin,
    MainWindowOverlaysMixin,
    MainWindowHazardsMixin,
    MainWindowRadarMixin,
    MainWindowArchiveMixin,
    MainWindowMapHelpersMixin, MainWindowDebugMixin, QMainWindow):
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

    def _init_statusbar(self):
        # row 0: version anchor | [update indicator] | status message
        self._status_left = QWidget(self._map_container)
        self._status_left.setObjectName("statusOverlayLeft")
        pill = QVBoxLayout(self._status_left)
        pill.setContentsMargins(10, 5, 10, 5)
        pill.setSpacing(3)

        row0 = QHBoxLayout()
        row0.setContentsMargins(0, 0, 0, 0)
        row0.setSpacing(8)

        version_label = QLabel(f"STORM v{config.VERSION}")
        set_pill_status(version_label, "muted")
        row0.addWidget(version_label)

        # in-ops update indicator — hidden until an update is detected
        self.update_indicator = QPushButton("↑ UPDATE AVAILABLE")
        self.update_indicator.setFlat(True)
        self.update_indicator.setProperty("pillRole", "action")
        set_pill_status(self.update_indicator, "accent")
        self.update_indicator.setToolTip("Click to apply update and restart STORM")
        self.update_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_indicator.setVisible(False)
        self.update_indicator.clicked.connect(self._on_update_indicator_clicked)
        row0.addWidget(self.update_indicator)

        self.status_msg_label = QLabel("")
        set_pill_status(self.status_msg_label, "label")
        row0.addWidget(self.status_msg_label)
        row0.addStretch()

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)

        self.coord_label = QLabel("LAT: ---.---- LON: ---.----")
        coord_probe = "LAT: -180.0000  LON: -180.0000"
        self.coord_label.setMinimumWidth(self.coord_label.fontMetrics().horizontalAdvance(coord_probe) + 8)
        self.vehicle_count_label = QLabel("VEHICLES: 0")

        # mode badge: VEHICLE / MONITOR / VIEWER / ARCHIVE — always leftmost in row 1
        if self._archive:
            mode_badge = QLabel("● ARCHIVE")
            set_pill_status(mode_badge, "mode_archive")
        elif self._monitor:
            mode_badge = QLabel("● MONITOR")
            set_pill_status(mode_badge, "mode_monitor")
        elif self._viewer:
            mode_badge = QLabel("● VIEWER")
            set_pill_status(mode_badge, "mode_viewer")
        elif runtime_flags.FLAGS.admin_mode:
            mode_badge = QLabel("● ADMIN")
            set_pill_status(mode_badge, "mode_admin")
        else:
            mode_badge = QLabel("● VEHICLE")
            set_pill_status(mode_badge, "mode_vehicle")
        mode_badge.setProperty("pillRole", "badge")
        row1.addWidget(mode_badge)

        row1.addWidget(self._status_divider())
        row1.addWidget(self.coord_label)
        row1.addWidget(self._status_divider())
        row1.addWidget(self.vehicle_count_label)
        row1.addStretch()

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)

        # gps fix status — vehicle mode only (hidden in monitor/viewer)
        self.gps_indicator = QLabel("● NO GPS FIX")
        self.gps_indicator.setProperty("pillRole", "badge")
        set_pill_status(self.gps_indicator, "error")
        self.gps_indicator.setVisible(not (self._monitor or self._viewer))
        row2.addWidget(self.gps_indicator)

        if not (self._monitor or self._viewer):
            row2.addWidget(self._status_divider())

        self.conn_indicator = QLabel("● AWS OFFLINE")
        self.conn_indicator.setProperty("pillRole", "badge")
        set_pill_status(self.conn_indicator, "error")
        row2.addWidget(self.conn_indicator)
        row2.addWidget(self._status_divider())

        self.net_indicator = QLabel("")
        self.net_indicator.setProperty("pillRole", "badge")
        set_pill_status(self.net_indicator, "off")
        self.net_indicator.setVisible(False)
        row2.addWidget(self.net_indicator)

        row2.addStretch()

        self.date_label = QLabel("-- --- ----")
        row2.addWidget(self.date_label)

        row2.addWidget(self._status_divider())

        self.clock_label = QLabel("--:--:-- UTC")
        self.clock_label.setProperty("pillRole", "clock")
        row2.addWidget(self.clock_label)

        # keep hazard_indicator as a hidden member so existing callers don't break
        self.hazard_indicator = QLabel("● DATA OFFLINE")
        self.hazard_indicator.setVisible(False)

        pill.addLayout(row0)
        pill.addLayout(row1)
        pill.addLayout(row2)


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


    def update_coordinates(self, lat: float, lon: float):
        self.coord_label.setText(f"LAT: {lat:>9.4f}  LON: {lon:>10.4f}")

    def _on_map_moved(self, lat: float, lon: float, _zoom: float):
        self.update_coordinates(lat, lon)

    def update_vehicle_count(self, count: int):
        self.vehicle_count_label.setText(f"VEHICLES: {count}")
        if hasattr(self, "_vehicle_count_badge"):
            self._vehicle_count_badge.setText(str(count))

    def _start_net_check(self):
        """Start periodic internet connectivity check (TCP to 1.1.1.1:53 every 30s)."""
        self._net_check_timer = QTimer(self)
        self._net_check_timer.timeout.connect(self._run_net_check)
        self._net_check_timer.start(30_000)
        self._run_net_check()  # immediate first check

    def _run_net_check(self):
        checker = _NetChecker()
        checker.result_ready.connect(self._on_net_result)
        threading.Thread(target=checker.check, daemon=True).start()

    def _on_net_result(self, state: str):
        if state == "ok":
            self.net_indicator.setText("● NET OK")
            set_pill_status(self.net_indicator, "ok")
        elif state == "slow":
            self.net_indicator.setText("● NET SLOW")
            set_pill_status(self.net_indicator, "warn")
        else:
            self.net_indicator.setText("● NO INTERNET")
            set_pill_status(self.net_indicator, "error")
        self.net_indicator.setVisible(True)
        self._layout_overlays()

    def _update_gps_indicator(self):
        """Refresh GPS fix status label based on age of last local vehicle observation."""
        age = time.monotonic() - self._last_local_obs_ts
        if self._last_local_obs_ts == 0.0 or age > 30:
            self.gps_indicator.setText("● NO GPS FIX")
            set_pill_status(self.gps_indicator, "error")
        elif age > 5:
            self.gps_indicator.setText("● GPS STALE")
            set_pill_status(self.gps_indicator, "warn")
        else:
            self.gps_indicator.setText("● GPS OK")
            set_pill_status(self.gps_indicator, "ok")
        self._layout_overlays()


    def _start_update_check(self):
        """Check for updates at startup and then every 30 minutes."""
        self._update_worker = UpdateWorker()
        self._update_worker.check_done.connect(self._on_update_check_done)
        self._update_worker.pull_done.connect(self._on_update_pull_done)
        self._update_worker.start_check()

        self._update_check_timer = QTimer(self)
        self._update_check_timer.timeout.connect(self._update_worker.start_check)
        self._update_check_timer.start(10 * 60 * 1000)   # 10 min

    def _on_update_check_done(self, commits_behind: int):
        if commits_behind > 0:
            self.update_indicator.setText("↑ UPDATE AVAILABLE")
            set_pill_status(self.update_indicator, "accent")
            self.update_indicator.setEnabled(True)
        elif commits_behind == -2:
            self.update_indicator.setText("DEV BUILD")
            set_pill_status(self.update_indicator, "off")
            self.update_indicator.setEnabled(False)
        else:
            # -1 (error) or 0 (current) — stay silent
            self.update_indicator.setVisible(False)
            self._layout_overlays()
            return
        self.update_indicator.setVisible(True)
        self._layout_overlays()

    def _on_update_indicator_clicked(self):
        self.update_indicator.setEnabled(False)
        self.update_indicator.setText("↑ UPDATING...")
        set_pill_status(self.update_indicator, "muted")
        self._update_worker.start_pull()

    def _on_update_pull_done(self, success: bool, deps_changed: bool):
        import os, sys
        if success and deps_changed:
            # can't auto-restart safely if conda env changed — tell the user
            _cmd = "conda env update -f envs/storm.yml --prune"
            self.update_indicator.setText(f"↑ DEPS CHANGED — RUN: {_cmd}  THEN RESTART")
            set_pill_status(self.update_indicator, "deps")
            self.update_indicator.setEnabled(False)
            self.update_indicator.setVisible(True)
            self._layout_overlays()
        elif success:
            self.update_indicator.setText("↑ RESTARTING...")
            set_pill_status(self.update_indicator, "ok")
            self._layout_overlays()
            QTimer.singleShot(600, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))
        else:
            self.update_indicator.setText("↑ UPDATE FAILED — RETRY")
            set_pill_status(self.update_indicator, "error")
            self.update_indicator.setEnabled(True)
            self.update_indicator.setVisible(True)
            self._layout_overlays()

    def set_connection_status(self, connected: bool):
        if connected:
            self.conn_indicator.setText("● AWS OK")
            set_pill_status(self.conn_indicator, "ok")
        else:
            self.conn_indicator.setText("● AWS OFFLINE")
            set_pill_status(self.conn_indicator, "error")


    def _init_vehicle_panel(self):
        self.vehicle_panel = QWidget(self._map_container)
        self.vehicle_panel.setObjectName("vehiclePill")
        self.vehicle_panel.setMinimumWidth(VEHICLE_PANEL_WIDTH)
        self.vehicle_panel.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        # session-scoped size watermark — panel never shrinks while open;
        # resets to (0, 0) when the toolbar toggle hides it.
        self._vehicle_panel_size_watermark = (0, 0)
        layout = QVBoxLayout(self.vehicle_panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # header row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        header = QLabel("VEHICLES")
        header.setObjectName("vehiclePillTitle")
        header_row.addWidget(header)

        self._vehicle_count_badge = QLabel("0")
        self._vehicle_count_badge.setObjectName("vehiclePillCount")
        header_row.addWidget(self._vehicle_count_badge)

        header_row.addStretch()

        self._chk_station_plots = QCheckBox("station plots")
        self._chk_station_plots.setChecked(True)
        self._chk_station_plots.setObjectName("vehiclePillToggle")
        header_row.addWidget(self._chk_station_plots)

        layout.addLayout(header_row)

        # placeholder until vehicle list is populated
        placeholder = QLabel("AWAITING VEHICLES...")
        placeholder.setObjectName("vehiclePillEmpty")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet(f"color: {P.TEXT_DISABLED}; font-size: 11px; font-weight: 500; letter-spacing: 0.5px; padding: 6px 0;")
        self._vehicle_placeholder = placeholder
        layout.addWidget(placeholder)

        self._vehicle_rows_widget = QWidget()
        self._vehicle_rows_widget.setObjectName("vehicleRowsContainer")
        # icon groups are columns laid out horizontally
        self._vehicle_rows_layout = QHBoxLayout(self._vehicle_rows_widget)
        self._vehicle_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._vehicle_rows_layout.setSpacing(14)
        self._vehicle_rows_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._vehicle_rows_widget.setVisible(False)
        layout.addWidget(self._vehicle_rows_widget)

        # no stretch so the pill shrink-wraps to content

        # start hidden — opened via toolbar toggle
        self.vehicle_panel.hide()
        self.btn_vehicles.toggled.connect(self.vehicle_panel.setVisible)
        self.btn_vehicles.toggled.connect(self._on_vehicle_panel_toggled)
        self.btn_vehicles.toggled.connect(self._request_layout)
        self.btn_prev_locs.toggled.connect(self.map_widget.set_deploy_locs_visible)
        self.btn_prev_locs.toggled.connect(self._apply_deploy_locs_filter_on_show)
        self.deploy_locs_controls.metric_changed.connect(self.map_widget.set_deploy_locs_metric)
        self.deploy_locs_controls.filter_changed.connect(self.map_widget.set_deploy_locs_filter)
        self.deploy_locs_controls.size_changed.connect(self.map_widget.set_deploy_locs_size)

        # detail pill (hidden until a vehicle is selected)
        self._selected_vehicle_ids = []
        self._vehicle_age_display_state: dict[str, tuple[str, str]] = {}
        self._vehicle_row_age_labels: dict[str, QLabel] = {}
        self.vehicle_detail_panel = QWidget(self._map_container)
        self.vehicle_detail_panel.setObjectName("vehicleDetailPill")
        detail_layout = QVBoxLayout(self.vehicle_detail_panel)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        detail_layout.setSpacing(6)

        self._vehicle_detail_title = QLabel("VEHICLE")
        self._vehicle_detail_title.setObjectName("vehicleDetailTitle")
        detail_layout.addWidget(self._vehicle_detail_title)

        self._vehicle_detail_body_widget = QWidget()
        self._vehicle_detail_body_layout = QVBoxLayout(self._vehicle_detail_body_widget)
        self._vehicle_detail_body_layout.setContentsMargins(0, 0, 0, 0)
        self._vehicle_detail_body_layout.setSpacing(0)
        detail_layout.addWidget(self._vehicle_detail_body_widget)

        self.vehicle_detail_panel.hide()
        self.btn_vehicles.toggled.connect(self._sync_vehicle_detail_visibility)


        # _nws_active_phenoms is updated by _on_nws_raw_phenoms (raw signal),

    _INJECT_COOLDOWN_MS = 600


    def _init_mqtt(self):
        self._mqtt_client = MQTTClient(client_id=config.VEHICLE_ID, parent=self)
        self._mqtt_client.connected.connect(self._on_mqtt_connected)
        self._mqtt_client.disconnected.connect(self._on_mqtt_disconnected)

        self._vehicle_sync = VehicleSync(self._mqtt_client, parent=self)
        self._vehicle_sync.vehicle_received.connect(self._on_remote_vehicle_obs)
        self._storm_cone_sync = StormConeSync(self._mqtt_client, read_only=self._viewer, parent=self)
        self._scan_sector_sync = ScanSectorSync(self._mqtt_client, read_only=self._viewer, parent=self)
        self._scan_sector_sync.scan_received.connect(self._recv_remote_scan_sector)

        # connect after a short delay so the window is fully painted first
        if config.MQTT_HOST:
            QTimer.singleShot(500, self._mqtt_connect)
        else:
            log.info("MQTT host not configured — running offline")

    def _on_remote_vehicle_obs(self, obs):
        # if this machine is producing local data (not in monitor mode),
        if not self._monitor and obs.vehicle_id == config.VEHICLE_ID:
            return
        self.update_vehicle_obs(obs)

    def _on_local_vehicle_obs(self, obs: Observation):
        if self._startup_local_pending and obs.vehicle_id == config.VEHICLE_ID:
            self._complete_local_startup_phase()
        # track GPS fix age for the local vehicle status indicator
        if obs.vehicle_id == config.VEHICLE_ID and obs.lat is not None:
            self._last_local_obs_ts = time.monotonic()
        # always update local GUI at the full poll rate (1 Hz)
        self.update_vehicle_obs(obs)
        # throttle MQTT publishes independently — other vehicles don't need 1 Hz
        vehicle_sync = getattr(self, "_vehicle_sync", None)
        if vehicle_sync is not None:
            now = time.monotonic()
            if now - self._last_mqtt_publish >= config.OBS_MQTT_PUBLISH_S:
                vehicle_sync.publish_obs(obs)
                self._last_mqtt_publish = now

    def _mqtt_connect(self):
        use_tls = config.MQTT_USE_TLS and not runtime_flags.FLAGS.mqtt_no_tls
        if not use_tls:
            log.warning("MQTT TLS disabled via --mqtt-no-tls (diagnostic mode)")
        self._mqtt_client.connect_to_broker(
            host=config.MQTT_HOST,
            port=config.MQTT_PORT,
            use_tls=use_tls,
            ca_cert=config.MQTT_CA_CERT,
            cert_file=config.MQTT_CERT_FILE,
            key_file=config.MQTT_KEY_FILE,
        )

    def _on_mqtt_connected(self):
        self.set_connection_status(True)
        if self._startup_mqtt_pending:
            self._mqtt_startup_timer.start(1500)
        if self.status_msg_label.text().startswith("MQTT:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_mqtt_disconnected(self, code: int):
        self.set_connection_status(False)
        if self._startup_mqtt_pending:
            self._complete_mqtt_startup_phase()
        code_map = {
            -1: "setup error (cert/key/path)",
            7: "connection lost",
            128: "unspecified error",
            129: "malformed packet",
            130: "protocol error",
            131: "implementation-specific error",
            132: "unsupported protocol version",
            133: "client ID invalid",
            134: "bad username/password",
            135: "not authorized",
            136: "server unavailable",
            137: "server busy",
            138: "banned",
            140: "bad auth method",
            149: "packet too large",
            151: "quota exceeded",
            153: "payload format invalid",
        }
        reason = code_map.get(code, "connection/auth error")
        self.status_msg_label.setText(f"MQTT: offline ({code}) {reason}")
        self._layout_overlays()


    def _init_annotations(self):
        self._annotations: dict[str, Annotation] = {}
        self._active_annotation_type: str = ""
        self._annotation_sync = AnnotationSync(self._mqtt_client, read_only=self._viewer, parent=self)

        # tool selection → set cursor mode
        self.annotation_tools.tool_selected.connect(self._on_annotation_tool_selected)
        self.annotation_tools.refresh_requested.connect(self._on_refresh_current_json)

        # map click → place annotation (if tool is active)
        self.map_widget.map_clicked.connect(self._on_map_click)

        # annotation marker click → edit/delete dialog
        self.map_widget.annotation_clicked.connect(self._on_annotation_clicked)
        self.map_widget.annotation_drag_ended.connect(self._on_annotation_drag_end)
        self._moving_annotation_id = None

        # remote annotations arriving over MQTT — update map without re-publishing
        self._annotation_sync.annotation_received.connect(self._recv_remote_annotation)
        self._annotation_sync.annotation_deleted.connect(self._recv_remote_annotation_deleted)

        self._init_drawings()

    def _init_drawings(self):
        self._drawings: dict[str, DrawingAnnotation] = {}
        self._active_drawing_type: str = ""
        self._drawing_points: list = []
        self._moving_drawing_id: str | None = None
        self._moving_drawing_original_coordinates: list | None = None
        self._drawing_sync = DrawingSync(self._mqtt_client, read_only=self._viewer, parent=self)

        self.map_widget.map_double_clicked.connect(self._on_map_dblclick)
        self.map_widget.drawing_clicked.connect(self._on_drawing_clicked)
        self.map_widget.drawing_drag_ended.connect(self._on_drawing_drag_end)
        self._drawing_sync.drawing_received.connect(self._recv_remote_drawing)
        self._drawing_sync.drawing_deleted.connect(self._recv_remote_drawing_deleted)

    def _set_placement_prompt(self, msg: str, needs_click: bool = True):
        """Name what the next map click or drag does, in the on-map banner."""
        text = f"{msg}  —  click map to place" if needs_click else msg
        if self._click_modes.is_active():
            self._click_modes.set_prompt(text)
        else:
            # drag-to-move flows aren't click modes; Esc doesn't cancel them
            self._show_mode_banner(text, cancellable=False)

    def _clear_placement_prompt(self):
        if not self._click_modes.is_active():
            self._show_mode_banner("", cancellable=False)

    def _on_annotation_tool_selected(self, type_key: str):
        if not type_key:
            # drawer closed or the active tool clicked again. A storm-motion fix in
            # progress survives closing the drawer, so radar frames can be stepped.
            if (self._click_modes.is_active("storm_motion_fix")
                    and self._pending_cone_motion_fix is not None):
                return
            for kind in _ANNOTATE_KINDS:
                self._click_modes.exit(kind)
            return

        if type_key in DRAWING_TYPE_MAP:
            # drawing tool (front or custom shape)
            self._click_modes.enter(f"drawing:{type_key}")
            self._active_drawing_type = type_key
            self.map_widget.set_drawing_mode(True, type_key)
            meta = DRAWING_TYPE_MAP[type_key]
            self._set_placement_prompt(
                f"{meta['label']} — click to add points, double-click to finish",
                needs_click=False,
            )
        elif type_key == "storm_motion":
            self._click_modes.enter("storm_motion_fix")
            self._active_annotation_type = type_key
            self.map_widget.set_annotation_mode(True)
            self._set_placement_prompt(
                "storm cone — select first radar feature position",
                needs_click=False,
            )
        else:
            self._click_modes.enter(f"annotation:{type_key}")
            self._active_annotation_type = type_key
            self.map_widget.set_annotation_mode(True)
            if type_key == "pressure_system":
                label = "pressure system"
            else:
                label = ANNOTATION_TYPE_MAP.get(type_key, {}).get("label", "annotation")
            self._set_placement_prompt(label)

    def _exit_annotation_mode(self, _mode: str, next_mode: str):
        self._active_annotation_type = ""
        self._pending_cone_params = None
        self._pending_cone_motion_fix = None
        self.map_widget.set_annotation_mode(False)
        self.map_widget.set_storm_cone_placement_mode(False)
        if kind_of(next_mode) not in _ANNOTATE_KINDS:
            self.annotation_tools.deactivate_tool()

    def _exit_drawing_mode(self, _mode: str, next_mode: str):
        self._cancel_drawing()
        if kind_of(next_mode) not in _ANNOTATE_KINDS:
            self.annotation_tools.deactivate_tool()

    def _on_map_click(self, lat: float, lon: float):
        self._click_modes.click(lat, lon)

    def _on_annotation_place_click(self, lat: float, lon: float):
        type_key = self._active_annotation_type
        if type_key == "fork":
            # only one fork on the map: remove the old one before placing
            existing_forks = [aid for aid, a in self._annotations.items() if a.type_key == "fork"]
            for fid in existing_forks:
                self._delete_annotation(fid)
            self._click_modes.exit()
            self._place_annotation(Annotation.new(type_key="fork", lat=lat, lon=lon))
            return
        dlg = AnnotationPlaceDialog(type_key, lat, lon, viewer_mode=self._viewer, parent=self)
        if dlg.exec() == AnnotationPlaceDialog.DialogCode.Accepted:
            annotation = Annotation.new(
                type_key=dlg.result_type_key(),
                lat=lat,
                lon=lon,
                label=dlg.result_label(),
            )
            self._place_annotation(annotation)

    def _on_annotation_clicked(self, annotation_id: str):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        dlg = AnnotationEditDialog(annotation, viewer_mode=self._viewer, parent=self)
        if dlg.exec() == AnnotationEditDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_annotation(annotation_id)
            elif dlg.action() == "save":
                annotation.label = dlg.result_label()
                self._update_annotation(annotation)
            elif dlg.action() == "move":
                self._moving_annotation_id = annotation_id
                self.map_widget.set_annotation_draggable(annotation_id, True)
                self._set_placement_prompt("drag the annotation to its new location", needs_click=False)

    def _on_annotation_drag_end(self, annotation_id: str, lat: float, lon: float):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        self.map_widget.set_annotation_draggable(annotation_id, False)
        self._moving_annotation_id = None
        self._clear_placement_prompt()

        dlg = AnnotationMoveConfirmDialog(annotation, lat, lon, parent=self)
        if dlg.exec() == AnnotationMoveConfirmDialog.DialogCode.Accepted:
            annotation.lat = lat
            annotation.lon = lon
            self._update_annotation(annotation)
        else:
            # revert to original position
            self.map_widget.move_annotation(annotation_id, annotation.lat, annotation.lon)

    def _place_annotation(self, annotation: Annotation):
        self._annotations[annotation.id] = annotation
        self.map_widget.add_annotation(annotation)
        self._annotation_sync.publish_create(annotation)
        self._refresh_annotation_layer()
        log.info("annotation placed: %s at (%.4f, %.4f)", annotation.type_key, annotation.lat, annotation.lon)

    def _delete_annotation(self, annotation_id: str):
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._annotation_sync.publish_delete(annotation_id)
        self._refresh_annotation_layer()
        log.info("annotation deleted: %s", annotation_id)

    def _update_annotation(self, annotation: Annotation):
        self._annotations[annotation.id] = annotation
        # re-add marker so label tooltip reflects new text
        self.map_widget.add_annotation(annotation)
        self._annotation_sync.publish_update(annotation)
        log.info("annotation updated: %s label=%s", annotation.id, annotation.label)

    def _recv_remote_annotation(self, annotation: Annotation):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._annotations[annotation.id] = annotation
        self.map_widget.add_annotation(annotation)
        self._refresh_annotation_layer()
        log.info("remote annotation received: %s (%s)", annotation.id, annotation.type_key)

    def _recv_remote_annotation_deleted(self, annotation_id: str, deleted_at: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._refresh_annotation_layer()
        log.info("remote annotation deleted: %s at %s", annotation_id, deleted_at or "unknown")


    def _on_drawing_click(self, lat: float, lon: float):
        """Add a point to the in-progress drawing."""
        self._drawing_points.append([lat, lon])
        self.map_widget.drawing_update_preview(self._drawing_points)
        n = len(self._drawing_points)
        meta = DRAWING_TYPE_MAP.get(self._active_drawing_type, {})
        self._set_placement_prompt(
            f"{meta.get('label', '')} — {n} point{'s' if n != 1 else ''} — double-click to finish",
            needs_click=False,
        )

    def _on_map_dblclick(self, lat: float, lon: float):
        if not self._click_modes.is_active("drawing"):
            return
        self._finalize_drawing(lat, lon)

    def _finalize_drawing(self, lat: float, lon: float):
        pts = self._drawing_points[:]
        while pts and _coords_close(pts[-1], (lat, lon)):
            pts.pop()
        pts.append([lat, lon])

        drawing_type = self._active_drawing_type

        if len(pts) < 2:
            self._drawing_points = pts
            self.map_widget.drawing_update_preview(self._drawing_points)
            meta = DRAWING_TYPE_MAP.get(drawing_type, {})
            self._set_placement_prompt(
                f"{meta.get('label', 'Drawing')} needs at least 2 points — keep drawing, then double-click to finish",
                needs_click=False,
            )
            return
        if drawing_type == "polygon" and len(pts) < 3:
            self._drawing_points = pts
            self.map_widget.drawing_update_preview(self._drawing_points)
            self._set_placement_prompt(
                "Polygon needs at least 3 points — keep drawing, then double-click to finish",
                needs_click=False,
            )
            return

        self._click_modes.exit("drawing")   # clear state + preview before showing dialog

        if drawing_type in ("polyline", "polygon"):
            dlg = DrawingTitleDialog(drawing_type, parent=self)
            if dlg.exec() != DrawingTitleDialog.DialogCode.Accepted:
                return
            title = dlg.title()
            color = dlg.color()
            line_style = dlg.line_style()
        else:
            title = DRAWING_TYPE_MAP.get(drawing_type, {}).get("label", drawing_type)
            color = None
            line_style = "solid"

        drawing = DrawingAnnotation.new(
            drawing_type=drawing_type,
            coordinates=pts,
            title=title,
            color=color,
            line_style=line_style,
        )
        dlg = DrawingPlaceConfirmDialog(drawing_type, len(pts), parent=self)
        if dlg.exec() != DrawingPlaceConfirmDialog.DialogCode.Accepted:
            return
        self._place_drawing(drawing)
        if drawing_type in FRONT_TYPE_KEYS:
            self.btn_annotate.setChecked(False)

    def _cancel_drawing(self):
        self._drawing_points.clear()
        self._active_drawing_type = ""
        self.map_widget.set_drawing_mode(False)
        self._clear_placement_prompt()

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

    def _on_drawing_clicked(self, drawing_id: str):
        # ignore while a map click mode is active
        if self._click_modes.is_active():
            return
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        dlg = DrawingEditDialog(drawing, parent=self)
        if dlg.exec() != DrawingEditDialog.DialogCode.Accepted:
            return
        action = dlg.action()
        if action == "delete":
            self._delete_drawing(drawing_id)
        elif action == "flip":
            drawing.flipped = not drawing.flipped
            self._update_drawing(drawing)
        elif action == "move":
            self._moving_drawing_id = drawing_id
            self._moving_drawing_original_coordinates = [pt[:] for pt in drawing.coordinates]
            self.map_widget.set_drawing_draggable(drawing_id, True)
            self._set_placement_prompt("drag the drawing to its new location", needs_click=False)
        elif action == "save":
            drawing.title = dlg.result_title()
            drawing.color = dlg.result_color()
            drawing.line_style = dlg.result_line_style()
            self._update_drawing(drawing)

    def _on_drawing_drag_end(self, drawing_id: str, coordinates_json: str):
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        self.map_widget.set_drawing_draggable(drawing_id, False)
        self._moving_drawing_id = None
        try:
            new_coordinates = json.loads(coordinates_json)
        except json.JSONDecodeError:
            log.warning("drawing drag end parse failed for %s", drawing_id)
            self._clear_placement_prompt()
            self._update_drawing(drawing)
            return
        dlg = DrawingMoveConfirmDialog(drawing, new_coordinates, parent=self)
        if dlg.exec() == DrawingMoveConfirmDialog.DialogCode.Accepted:
            drawing.coordinates = new_coordinates
        elif self._moving_drawing_original_coordinates is not None:
            drawing.coordinates = [pt[:] for pt in self._moving_drawing_original_coordinates]
        self._moving_drawing_original_coordinates = None
        self._clear_placement_prompt()
        self._update_drawing(drawing)

    def _place_drawing(self, drawing: DrawingAnnotation):
        self._drawings[drawing.id] = drawing
        self.map_widget.add_drawing(drawing)
        self._drawing_sync.publish_create(drawing)
        self._refresh_annotation_layer()
        log.info("drawing placed: %s at %d points", drawing.drawing_type, len(drawing.coordinates))

    def _delete_drawing(self, drawing_id: str):
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._drawing_sync.publish_delete(drawing_id)
        self._refresh_annotation_layer()
        log.info("drawing deleted: %s", drawing_id)

    def _update_drawing(self, drawing: DrawingAnnotation):
        self._drawings[drawing.id] = drawing
        self.map_widget.remove_drawing(drawing.id)
        self.map_widget.add_drawing(drawing)
        self._drawing_sync.publish_update(drawing)
        log.info("drawing updated: %s", drawing.id)

    def _recv_remote_drawing(self, drawing: DrawingAnnotation):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._drawings[drawing.id] = drawing
        self.map_widget.add_drawing(drawing)
        self._refresh_annotation_layer()
        log.info("remote drawing received: %s (%s)", drawing.id, drawing.drawing_type)

    def _recv_remote_drawing_deleted(self, drawing_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._refresh_annotation_layer()
        log.info("remote drawing deleted: %s", drawing_id)


    def _init_storm_cone(self):
        self._storm_cones: dict[str, StormCone] = {}
        self._pending_cone_params: dict | None = None
        self._pending_cone_motion_fix: dict | None = None
        self._moving_cone_id: str | None = None
        self._moving_cone_original_location: tuple[float, float] | None = None

        # cone placed via ANNOTATE drawer — map cone-click → edit dialog
        self.map_widget.storm_cone_clicked.connect(self._on_storm_cone_clicked)
        self.map_widget.storm_cone_drag_ended.connect(self._on_storm_cone_drag_end)
        self.map_widget.storm_cone_place_drag_ended.connect(self._on_storm_cone_place_drag_end)

        # remote cones arriving over MQTT — update map without re-publishing
        self._storm_cone_sync.cone_received.connect(self._recv_remote_storm_cone)
        self._storm_cone_sync.cone_deleted.connect(self._recv_remote_storm_cone_deleted)

        # auto-expire cones older than 1 hour (checked every 60 seconds).
        self._cone_expire_timer = QTimer(self)
        self._cone_expire_timer.setInterval(60_000)
        self._cone_expire_timer.timeout.connect(self._expire_storm_cones)
        self._cone_expire_timer.start()

    def _on_storm_motion_fix_click(self, lat: float, lon: float):
        scan = self._current_displayed_radar_scan()
        if scan is None:
            self._set_placement_prompt(
                "storm cone — show a radar frame before selecting motion",
                needs_click=False,
            )
            return

        scan_time = scan.scan_time
        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)
        time_label = scan_time.strftime("%H:%MZ")

        if self._pending_cone_motion_fix is None:
            self._pending_cone_motion_fix = {
                "lat": lat,
                "lon": lon,
                "scan_time": scan_time,
                "site": getattr(scan, "site", ""),
                "product": getattr(scan, "product", ""),
                "time_label": time_label,
            }
            self._set_placement_prompt(
                f"storm cone — first fix {time_label}; move to a later radar frame and select the same feature",
                needs_click=False,
            )
            return

        first = self._pending_cone_motion_fix
        if getattr(scan, "site", "") != first.get("site", ""):
            self._set_placement_prompt(
                "storm cone — radar site changed; select the first fix again",
                needs_click=False,
            )
            self._pending_cone_motion_fix = None
            return

        try:
            motion = motion_from_fixes(
                first["lat"], first["lon"], first["scan_time"],
                lat, lon, scan_time,
            )
        except ValueError:
            self._set_placement_prompt(
                "storm cone — choose a radar frame later than the first fix",
                needs_click=False,
            )
            return

        if motion["distance_nm"] < 0.05:
            self._set_placement_prompt(
                "storm cone — second fix is too close; choose the same feature on a later frame",
                needs_click=False,
            )
            return

        speed_kts = motion["speed_kts"]
        heading = int(round(motion["heading"])) % 360
        dlg = StormConeMotionConfirmDialog(
            lat,
            lon,
            speed_kts,
            heading,
            first["time_label"],
            time_label,
            motion["distance_nm"],
            parent=self,
        )
        if dlg.exec() != StormConeMotionConfirmDialog.DialogCode.Accepted:
            self._set_placement_prompt(
                f"storm cone — first fix {first['time_label']}; select a second point",
                needs_click=False,
            )
            return

        cone = StormCone.new(
            lat,
            lon,
            heading=heading,
            speed_kts=speed_kts,
            valid_at=scan_time,
        )
        self._click_modes.exit()
        self._place_storm_cone(cone)

    def _on_storm_cone_clicked(self, cone_id: str):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        dlg = StormConeInputDialog(
            edit_mode=True,
            speed_kts=cone.speed_kts,
            heading=int(cone.heading),
            parent=self,
        )
        if dlg.exec() == StormConeInputDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_storm_cone(cone_id)
            elif dlg.action() == "save":
                cone.speed_kts = dlg.speed_kts()
                cone.heading = dlg.heading()
                cone.created_at = datetime.now(timezone.utc)
                self._update_storm_cone(cone)
            elif dlg.action() == "move":
                self._moving_cone_id = cone_id
                self._moving_cone_original_location = (cone.lat, cone.lon)
                self.map_widget.set_storm_cone_draggable(cone_id, True)
                self._set_placement_prompt("drag the storm cone to its new location", needs_click=False)

    def _on_storm_cone_drag_end(self, cone_id: str, lat: float, lon: float):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        self.map_widget.set_storm_cone_draggable(cone_id, False)
        self._moving_cone_id = None
        dlg = StormConeMoveConfirmDialog(
            self._moving_cone_original_location[0] if self._moving_cone_original_location else cone.lat,
            self._moving_cone_original_location[1] if self._moving_cone_original_location else cone.lon,
            lat,
            lon,
            parent=self,
        )
        if dlg.exec() == StormConeMoveConfirmDialog.DialogCode.Accepted:
            cone.lat = lat
            cone.lon = lon
            cone.created_at = datetime.now(timezone.utc)
        elif self._moving_cone_original_location is not None:
            cone.lat, cone.lon = self._moving_cone_original_location
        self._moving_cone_original_location = None
        self._clear_placement_prompt()
        self._update_storm_cone(cone)

    def _on_storm_cone_place_drag_end(self, lat: float, lon: float):
        if self._pending_cone_params is None:
            return
        dlg = StormConePlaceConfirmDialog(
            lat,
            lon,
            self._pending_cone_params["speed_kts"],
            self._pending_cone_params["heading"],
            parent=self,
        )
        if dlg.exec() != StormConePlaceConfirmDialog.DialogCode.Accepted:
            self._set_placement_prompt("storm cone — click and drag to place", needs_click=False)
            return
        cone = StormCone.new(lat, lon, **self._pending_cone_params)
        self._pending_cone_params = None
        self._click_modes.exit()
        self._clear_placement_prompt()
        self._place_storm_cone(cone)

    def _place_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._storm_cone_sync.publish_create(cone)
        self._refresh_storm_cone_layer()
        log.info("storm cone placed: id=%s lat=%.4f lon=%.4f hdg=%.0f spd=%.0f",
                 cone.id, cone.lat, cone.lon, cone.heading, cone.speed_kts)

    def _delete_storm_cone(self, cone_id: str):
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._storm_cone_sync.publish_delete(cone_id)
        self._refresh_storm_cone_layer()
        log.info("storm cone deleted: %s", cone_id)

    def _update_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)   # re-add rebuilds geometry
        self._storm_cone_sync.publish_update(cone)
        log.info("storm cone updated: id=%s hdg=%.0f spd=%.0f",
                 cone.id, cone.heading, cone.speed_kts)

    def _recv_remote_storm_cone(self, cone: StormCone):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._refresh_storm_cone_layer()
        log.info("remote storm cone received: %s", cone.id)

    def _recv_remote_storm_cone_deleted(self, cone_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._refresh_storm_cone_layer()
        log.info("remote storm cone deleted: %s", cone_id)

    def _expire_storm_cones(self):
        """Remove any storm cone whose created_at is more than 1 hour ago.

        Called every 60 s by _cone_expire_timer.  Expired cones are silently
        removed from the map and dict — no MQTT delete is published, because
        the broker's own 1-hour message-expiry will have already purged the
        retained message on the broker side.
        """
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        expired = [
            cone_id
            for cone_id, cone in list(self._storm_cones.items())
            if cone.created_at < cutoff
        ]
        for cone_id in expired:
            self._storm_cones.pop(cone_id, None)
            self.map_widget.remove_storm_cone(cone_id)
            log.info("storm cone auto-expired (>1 h): %s", cone_id)
        if expired:
            self._refresh_storm_cone_layer()


    def _init_measure(self):
        self._measure_has_anchor = False
        self._measure_complete = False
        self.btn_measure.toggled.connect(self._on_measure_toggled)

    def _on_measure_toggled(self, active: bool):
        if active:
            self._click_modes.enter("measure")
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(True)
            self._set_placement_prompt("measure — click first point")
        else:
            self._click_modes.exit("measure")
            # if user exits mid-measure after first point, clear partial artifacts.
            if self._measure_has_anchor or self._measure_complete:
                self.map_widget.clear_measure()
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(False)
            self._clear_placement_prompt()

    def _on_measure_click(self, lat: float, lon: float):
        if self._measure_complete:
            self._set_placement_prompt("measure complete", needs_click=False)
            return

        self.map_widget.measure_click(lat, lon)
        if not self._measure_has_anchor:
            self._measure_has_anchor = True
            self._set_placement_prompt("measure — click second point")
        else:
            # second point placed — keep tool selected so user can toggle off to clear.
            self._measure_has_anchor = False
            self._measure_complete = True
            self.map_widget.set_measure_mode(False)   # reset cursor while keeping line visible
            self._set_placement_prompt("measure complete", needs_click=False)


    def _init_stations(self):
        self._vehicles: dict[str, Vehicle] = {}
        # archive playback replays old data, so only live sessions persist history
        history_dir = None if self._archive else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        ) / "vehicle_history"
        self._vehicle_history = VehicleHistoryStore(history_dir)
        self._vehicle_timeseries_dlg: "VehicleTimeseriesDialog | None" = None
        self._scan_sectors: dict[str, ScanSector] = {}
        self._scan_move_count = 0
        self._last_scan_publish = 0.0
        self._follow_mode = False
        self._station_layer = StationPlotLayer(self.map_widget)
        self._chk_station_plots.toggled.connect(self._station_layer.set_visible)
        self.map_widget.user_dragged.connect(self._on_user_dragged)
        # station plots on by default — delayed until map is ready
        QTimer.singleShot(1200, lambda: self._station_layer.set_visible(
            self._chk_station_plots.isChecked()
        ))

        self.btn_recenter = QToolButton(self._map_container)
        self.btn_recenter.setIcon(_make_loc_icon(18))
        self.btn_recenter.setIconSize(QSize(18, 18))
        self.btn_recenter.setToolTip("Re-center on vehicle")
        self.btn_recenter.setFixedSize(32, 32)
        self.btn_recenter.setStyleSheet(P.qss("""
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
        self.btn_recenter.clicked.connect(self._on_recenter_clicked)
        self.btn_recenter.hide()


    def _apply_deploy_locs_filter_on_show(self, visible: bool):
        """Apply the current threshold filter whenever the layer is toggled on."""
        if visible:
            self.map_widget.set_deploy_locs_filter(
                self.deploy_locs_controls.current_metric(),
                self.deploy_locs_controls.current_threshold(),
            )

    def _init_deploy_locs(self):
        if config.DEPLOY_LOCS_FILE:
            QTimer.singleShot(1200, self._load_deploy_locs)

    def _load_deploy_locs(self):
        try:
            with open(config.DEPLOY_LOCS_FILE, newline='') as f:
                reader = csv.DictReader(f)
                points = [
                    {
                        "lat": float(r["lat"]),
                        "lon": float(r["lon"]),
                        "rank_abi": int(r["rank_abi"]) if r["rank_abi"] else None,
                        "rank_aoi": int(r["rank_aoi"]) if r["rank_aoi"] else None,
                        "rqi": float(r["rqi"]) if r["rqi"] else None,
                    }
                    for r in reader
                ]
            self.map_widget.load_deploy_locs(points)
            log.info("deploy locs: loaded %d points from %s", len(points), config.DEPLOY_LOCS_FILE)
        except Exception as e:
            log.warning("deploy locs: could not load %s: %s", config.DEPLOY_LOCS_FILE, e)

    def update_vehicle_obs(self, obs: Observation) -> None:
        """Public entry point for all vehicle observation updates (MQTT, file watcher, GPS)."""
        self._maybe_seed_initial_radar_site(obs)
        if not self._should_display_vehicle_obs(obs):
            self._hide_vehicle(obs.vehicle_id)
            return

        existing = self._vehicles.get(obs.vehicle_id)
        if obs.vehicle_id == config.VEHICLE_ID:
            icon_type = config.VEHICLE_ICON
        else:
            icon_type = getattr(obs, "icon_type", None) or (existing.icon_type if existing else "car")
        v = self._vehicles.setdefault(
            obs.vehicle_id,
            Vehicle(id=obs.vehicle_id, lat=obs.lat, lon=obs.lon, icon_type=icon_type),
        )
        v.icon_type = icon_type
        v.lat, v.lon, v.latest_obs = obs.lat, obs.lon, obs
        
        # append to vehicle history if this is NOT the local vehicle and has met data
        if obs.vehicle_id != config.VEHICLE_ID:
            has_met_data = any([
                obs.temperature_c is not None,
                obs.dewpoint_c is not None,
                obs.wind_speed_ms is not None,
                obs.pressure_mb is not None,
            ])
            if has_met_data:
                history = self._vehicle_history.append(obs)
                # live-update timeseries dialog if open (it throttles redraws)
                if (self._vehicle_timeseries_dlg is not None
                        and self._vehicle_timeseries_dlg.isVisible()):
                    self._vehicle_timeseries_dlg.update_vehicle(
                        obs.vehicle_id, history)

        marker_color = self._obs_age_color(obs)
        age_label = self._obs_age_label(obs)
        self._vehicle_age_display_state[obs.vehicle_id] = (marker_color, age_label)
        hover_text = self._archive_vehicle_hover_text(obs.vehicle_id)
        self.map_widget.add_vehicle(
            obs.vehicle_id,
            obs.lat,
            obs.lon,
            marker_color,
            v.icon_type,
            hover_text=hover_text,
        )
        if obs.vehicle_id == config.VEHICLE_ID and hasattr(self, "routing_controls"):
            self.routing_controls.update_own_position(obs.lat, obs.lon)
            if not self._viewer and not self._monitor and not self._archive and obs.lat is not None and obs.lon is not None:
                self.map_widget.set_private_pin_own_location(obs.lat, obs.lon)
        self._sync_routing_vehicle_snapshot()
        count = len(self._vehicles)
        self.update_vehicle_count(count)
        if hasattr(self, "_vehicle_placeholder"):
            self._vehicle_placeholder.setVisible(False)
        self._refresh_vehicle_panel()
        self._station_layer.update(obs.vehicle_id, obs.lat, obs.lon, obs)
        self._refresh_vehicle_detail()
        self._update_recenter_btn_visibility()
        if self._follow_mode and obs.vehicle_id == self._follow_target_id():
            self.map_widget.follow_move(obs.lat, obs.lon)
        if obs.vehicle_id == config.VEHICLE_ID:
            self._update_local_scan_from_vehicle(obs)

    def _archive_vehicle_hover_text(self, vehicle_id: str) -> str:
        """Build the admin vehicle name and ground-speed tooltip (archive or live)."""
        if not runtime_flags.FLAGS.admin_mode:
            return vehicle_id

        if self._archive:
            if not hasattr(self, "_time_ctrl"):
                return vehicle_id
            archive_time = self._time_ctrl.current_time
            dense_obs = getattr(self, "_archive_vehicle_obs", None)
            if (
                dense_obs is not None
                and dense_obs.has_fresh_observation(vehicle_id, archive_time)
            ):
                speed = dense_obs.speed(vehicle_id, archive_time)
            elif hasattr(self, "_archive_mqtt"):
                speed = self._archive_mqtt.vehicle_speed(vehicle_id, archive_time)
            else:
                return vehicle_id
        else:
            history = self._vehicle_history.get(vehicle_id)
            if not history:
                return vehicle_id
            now = datetime.now(tz=timezone.utc)
            speed = calculate_vehicle_speed(
                history.recent(30, now), now, short_seconds=10, average_seconds=30
            )

        return f"{vehicle_id}\n{format_vehicle_speed(speed)}"

    def _follow_target_id(self) -> str | None:
        """Return the vehicle ID to follow: local vehicle, first selected, or sole vehicle."""
        if config.VEHICLE_ID in self._vehicles:
            return config.VEHICLE_ID
        if self._selected_vehicle_ids:
            return self._selected_vehicle_ids[0]
        if len(self._vehicles) == 1:
            return next(iter(self._vehicles))
        return None

    def _set_follow_mode(self, enabled: bool) -> None:
        self._follow_mode = enabled
        self.map_widget.set_follow(enabled)
        self._update_recenter_btn_visibility()

    def _on_recenter_clicked(self) -> None:
        """Re-engage follow mode from the floating re-center button."""
        self._set_follow_mode(True)
        if self._follow_target_id():
            target = self._vehicles.get(self._follow_target_id())
            if target:
                self.map_widget.follow_move(target.lat, target.lon)

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

    def _init_scan_button(self) -> None:
        self.btn_scan = QToolButton(self._map_container)
        self.btn_scan.setText("SCAN")
        self.btn_scan.setToolTip("Start or stop current data collection footprint")
        self.btn_scan.setFixedSize(58, 32)
        self.btn_scan.setStyleSheet(P.qss("""
            QToolButton {
                background: $bg_hover_2;
                border: 1px solid $border_2;
                border-radius: 4px;
                color: $text_secondary;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }
            QToolButton:hover {
                background: $bg_hover_4;
                border-color: $accent;
                color: $accent;
            }
            QToolButton[active="true"] {
                background: $accent@0.18;
                border-color: $accent;
                color: $accent;
            }
        """))
        self.btn_scan.clicked.connect(self._on_scan_button_clicked)
        self.btn_scan.setVisible(not (self._monitor or self._viewer or self._archive))

    def _on_scan_button_clicked(self) -> None:
        vehicle = self._vehicles.get(config.VEHICLE_ID)
        current = self._scan_sectors.get(config.VEHICLE_ID)
        if current and current.active:
            lat, lon = current.lat, current.lon
        elif vehicle is not None:
            lat, lon = vehicle.lat, vehicle.lon
        else:
            lat, lon = config.HOME_LAT, config.HOME_LON
            self.status_msg_label.setText("Scan: no local GPS yet; using configured home location")
            self._layout_overlays()

        dlg = ScanSectorDialog(
            vehicle_id=config.VEHICLE_ID,
            lat=lat,
            lon=lon,
            active_scan=current,
            parent=self,
        )
        if not dlg.exec():
            return

        if dlg.action == "stop":
            self._deactivate_local_scan()
        elif dlg.action == "apply":
            self._activate_local_scan(dlg.scan_sector())

    def _activate_local_scan(self, scan: ScanSector) -> None:
        self._scan_sectors[scan.vehicle_id] = scan
        self._scan_move_count = 0
        self._last_scan_publish = time.monotonic()
        self._refresh_scan_sectors()
        self._publish_scan(scan)
        self._update_scan_button()

    def _deactivate_local_scan(self, reason: str = "") -> None:
        existing = self._scan_sectors.pop(config.VEHICLE_ID, None)
        if existing is None:
            self._refresh_scan_sectors()
            self._update_scan_button()
            return
        inactive = ScanSector(
            vehicle_id=config.VEHICLE_ID,
            active=False,
            mode=existing.mode,
            lat=existing.lat,
            lon=existing.lon,
            range_m=existing.range_m,
            inner_range_m=existing.inner_range_m,
            azimuth_deg=existing.azimuth_deg,
            beam_width_deg=existing.beam_width_deg,
            follow_vehicle=existing.follow_vehicle,
        )
        self._publish_scan(inactive)
        self._scan_move_count = 0
        self._refresh_scan_sectors()
        self._update_scan_button()
        if reason:
            self.status_msg_label.setText(reason)
            self._layout_overlays()

    def _recv_remote_scan_sector(self, scan: ScanSector) -> None:
        if not (self._monitor or self._viewer or self._archive) and scan.vehicle_id == config.VEHICLE_ID:
            return
        if scan.active:
            self._scan_sectors[scan.vehicle_id] = scan
        else:
            self._scan_sectors.pop(scan.vehicle_id, None)
        self._refresh_scan_sectors()

    def _publish_scan(self, scan: ScanSector) -> None:
        sync = getattr(self, "_scan_sector_sync", None)
        if sync is not None:
            sync.publish(scan)

    def _refresh_scan_sectors(self) -> None:
        self.map_widget.set_scan_sectors_geojson(
            feature_collection(list(self._scan_sectors.values()))
        )
        self._set_layer_active("scan_sectors", bool(self._scan_sectors))

    def _update_scan_button(self) -> None:
        if not hasattr(self, "btn_scan"):
            return
        active = config.VEHICLE_ID in self._scan_sectors
        self.btn_scan.setProperty("active", "true" if active else "false")
        self.btn_scan.setText("SCAN ON" if active else "SCAN")
        scan = self._scan_sectors.get(config.VEHICLE_ID)
        if scan and scan.active:
            desc = scan.mode.upper()
            if scan.range_m:
                desc += f" {scan.range_m / 1000:.1f} km"
            self.btn_scan.setToolTip(f"Current sampling: {desc}")
        else:
            self.btn_scan.setToolTip("Start or stop current data collection footprint")
        self.btn_scan.style().unpolish(self.btn_scan)
        self.btn_scan.style().polish(self.btn_scan)
        self.btn_scan.adjustSize()
        self.btn_scan.setFixedSize(max(58, self.btn_scan.sizeHint().width() + 10), 32)
        self._layout_overlays()

    def _update_local_scan_from_vehicle(self, obs: Observation) -> None:
        scan = self._scan_sectors.get(config.VEHICLE_ID)
        if scan is None or not scan.active:
            return
        if scan.follow_vehicle:
            scan.lat = obs.lat
            scan.lon = obs.lon
            scan.timestamp = datetime.now(timezone.utc)
            self._refresh_scan_sectors()
            now = time.monotonic()
            if now - self._last_scan_publish >= config.OBS_MQTT_PUBLISH_S:
                self._publish_scan(scan)
                self._last_scan_publish = now
            return

        dist_km = self._haversine_km(scan.lat, scan.lon, obs.lat, obs.lon)
        if dist_km > SCAN_MOVE_THRESHOLD_KM:
            self._scan_move_count += 1
        else:
            self._scan_move_count = 0
        if self._scan_move_count >= SCAN_MOVE_FIXES_TO_STOP:
            self._deactivate_local_scan("Scan stopped: vehicle moved away from the collection point")

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

    def _update_recenter_btn_visibility(self) -> None:
        if not hasattr(self, "btn_recenter"):
            return
        visible = not self._follow_mode and self._follow_target_id() is not None
        self.btn_recenter.setVisible(visible)

    def _on_user_dragged(self) -> None:
        """Called when JS detects a map drag — disengage follow mode."""
        if self._follow_mode:
            self._follow_mode = False
            self.map_widget.set_follow(False)
            self._update_recenter_btn_visibility()

    def _should_display_vehicle_obs(self, obs: Observation) -> bool:
        return self._obs_age_minutes(obs) <= 10.0 * 60.0

    def _sync_routing_vehicle_snapshot(self) -> None:
        if hasattr(self, "routing_controls"):
            self.routing_controls.update_vehicles(self._vehicles)

    def _hide_vehicle(self, vehicle_id: str) -> None:
        removed = self._vehicles.pop(vehicle_id, None)
        self._vehicle_age_display_state.pop(vehicle_id, None)
        if removed is None:
            return

        self.map_widget.remove_vehicle(vehicle_id)
        self._station_layer.remove(vehicle_id)
        self._sync_routing_vehicle_snapshot()
        if vehicle_id in self._scan_sectors:
            self._scan_sectors.pop(vehicle_id, None)
            self._refresh_scan_sectors()
            self._update_scan_button()
        if vehicle_id in self._selected_vehicle_ids:
            self._selected_vehicle_ids = [vid for vid in self._selected_vehicle_ids if vid != vehicle_id]
        self.update_vehicle_count(len(self._vehicles))
        self._refresh_vehicle_panel()
        self._refresh_vehicle_detail()
        self._sync_vehicle_detail_visibility()
        self._update_recenter_btn_visibility()

    def _obs_age_minutes(self, obs: Observation) -> float:
        ref = (
            self._time_ctrl.current_time
            if self._archive and hasattr(self, "_time_ctrl")
            else datetime.now(timezone.utc)
        )
        age = ref - obs.timestamp
        return max(0.0, age.total_seconds() / 60.0)

    def _obs_age_color(self, obs: Observation) -> str:
        age_min = self._obs_age_minutes(obs)
        if age_min <= 1.0:
            return P.OK  # fresh
        if age_min <= 3.0:
            return P.WARN  # caution
        if age_min <= 5.0:
            return P.WARN_5  # aging
        return P.ERROR      # stale

    def _obs_age_label(self, obs: Observation) -> str:
        age_min = self._obs_age_minutes(obs)
        if age_min < 1.0:
            return "<1m"
        if age_min < 60.0:
            return f"{age_min:.0f}m"
        hours = age_min / 60.0
        return f"{hours:.1f}h"

    def _obs_status_sort_key(self, v) -> int:
        """Return a sort key based on ob status color: 0=green, 1=yellow, 2=orange, 3=red, 4=no obs."""
        if not v.latest_obs:
            return 4
        age_min = self._obs_age_minutes(v.latest_obs)
        if age_min <= 1.0:
            return 0
        if age_min <= 3.0:
            return 1
        if age_min <= 5.0:
            return 2
        return 3

    def _on_vehicle_panel_toggled(self, checked: bool) -> None:
        """Reset the size watermark when the panel is hidden so the next
        open starts fresh and re-grows from current content."""
        if not checked:
            self._vehicle_panel_size_watermark = (0, 0)

    def _refresh_vehicle_panel(self):
        if not hasattr(self, "_vehicle_rows_layout"):
            return

        rebuilt_rows = False
        self.vehicle_panel.setUpdatesEnabled(False)
        try:
            self._vehicle_row_age_labels.clear()
            _clear_layout(self._vehicle_rows_layout)

            if not self._vehicles:
                self._vehicle_rows_widget.setVisible(False)
                if hasattr(self, "_vehicle_placeholder"):
                    self._vehicle_placeholder.setVisible(True)
                self._vehicle_rows_layout.invalidate()
                self.vehicle_panel.layout().invalidate()
                rebuilt_rows = True
                return

            self._vehicle_rows_widget.setVisible(True)
            if hasattr(self, "_vehicle_placeholder"):
                self._vehicle_placeholder.setVisible(False)

            # 1. group vehicles by their icon_type
            grouped_vehicles = {}
            for v in self._vehicles.values():
                icon = v.icon_type if v.icon_type else "unknown"
                grouped_vehicles.setdefault(icon, []).append(v)

            # 2. sort vehicles within each group by ob status color
            for icon in grouped_vehicles:
                grouped_vehicles[icon].sort(key=self._obs_status_sort_key)

            # 3. lay icon groups out as side-by-side columns
            for icon, vehicles in sorted(grouped_vehicles.items()):
                col_widget = QWidget()
                col_widget.setMinimumWidth(180)
                col_layout = QVBoxLayout(col_widget)
                col_layout.setContentsMargins(0, 0, 0, 0)
                col_layout.setSpacing(0)
                col_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

                header = QLabel(icon.upper())
                header.setStyleSheet(f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: bold; padding-bottom: 4px; padding-left: 4px;")
                col_layout.addWidget(header)

                for v in vehicles:
                    col_layout.addWidget(self._make_vehicle_row(v))

                self._vehicle_rows_layout.addWidget(col_widget)

            # invalidate cached size hints so the panel reflects the rebuild
            self._vehicle_rows_layout.invalidate()
            self.vehicle_panel.layout().invalidate()
            self._vehicle_rows_widget.updateGeometry()
            self.vehicle_panel.updateGeometry()
            rebuilt_rows = True
        finally:
            # re-enable updates BEFORE _layout_overlays so adjustSize() reads
            # fresh size hints, not the stale ones cached during the rebuild.
            self.vehicle_panel.setUpdatesEnabled(True)
            if rebuilt_rows:
                self._layout_overlays()

    def _make_vehicle_row(self, v) -> QWidget:
        obs = v.latest_obs
        selected = v.id in self._selected_vehicle_ids

        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background-color: {P.alpha(P.INFO, 0.08)}; border-bottom: 1px solid {P.BORDER_3}; }}"
            if selected else
            f"QFrame {{ background: transparent; border-bottom: 1px solid {P.BORDER_3}; }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 5, 0, 5)
        rl.setSpacing(6)

        badge_color = self._obs_age_color(obs) if obs else P.TEXT_MUTED
        badge = QLabel("●")
        badge.setStyleSheet(f"color: {badge_color}; font-size: 12px; background: transparent; border: none;")
        badge.setFixedWidth(12)
        rl.addWidget(badge)

        name_btn = QPushButton(v.id)
        name_btn.setFlat(True)
        name_btn.setToolTip(v.id)
        name_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        name_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {P.TEXT}; "
            "font-weight: 600; font-size: 11px; padding: 0; text-align: left; }"
            f"QPushButton:hover {{ color: {P.INFO}; }}"
        )
        name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        name_btn.clicked.connect(lambda checked=False, vid=v.id: self._on_vehicle_row_clicked(vid))
        rl.addWidget(name_btn)

        if obs is None:
            no_obs = QLabel("no observations")
            no_obs.setStyleSheet(f"color: {P.TEXT_LABEL}; font-size: 11px; background: transparent; border: none;")
            no_obs.setFixedWidth(78)
            rl.addWidget(no_obs)
        else:
            sep = QLabel("·")
            sep.setStyleSheet(f"color: {P.TEXT_MUTED}; background: transparent; border: none;")
            sep.setFixedWidth(6)
            rl.addWidget(sep)
            age = QLabel(f"{self._obs_age_label(obs)} old")
            age.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px; background: transparent; border: none;")
            age.setFixedWidth(42)
            rl.addWidget(age)
            self._vehicle_row_age_labels[v.id] = age

        rl.addStretch()
        return row

    def _on_vehicle_row_clicked(self, vid: str):
        if vid in self._selected_vehicle_ids:
            self._selected_vehicle_ids.remove(vid)
        else:
            self._selected_vehicle_ids.append(vid)
        v = self._vehicles.get(vid)
        if v is not None:
            self.map_widget.fly_to(v.lat, v.lon, zoom=13)
        self._refresh_vehicle_panel()
        self._refresh_vehicle_detail()
        self._sync_vehicle_detail_visibility()
        self._layout_overlays()

    def _on_timeseries_button_clicked(self, vehicle_id: str):
        """Open (or re-load) the shared timeseries dialog for the given vehicle."""
        # collect all vehicles that have met history
        if self._archive:
            if not hasattr(self, "_archive_mqtt"):
                return
            all_vehicles = {
                vid: self._get_archive_vehicle_history(vid)
                for vid in self._vehicles
            }
        else:
            all_vehicles = {
                vid: hist
                for vid, hist in self._vehicle_history.items()
                if hist
            }

        # drop vehicles with no usable observations
        all_vehicles = {v: obs for v, obs in all_vehicles.items() if obs}

        if vehicle_id not in all_vehicles:
            return

        if self._vehicle_timeseries_dlg is None:
            from ui.dialogs.vehicle_timeseries_dialog import VehicleTimeseriesDialog  # noqa: PLC0415
            self._vehicle_timeseries_dlg = VehicleTimeseriesDialog(self)

        self._vehicle_timeseries_dlg.load(vehicle_id, all_vehicles)
    
    def _get_archive_vehicle_history(self, vehicle_id: str) -> list:
        """Extract vehicle observation history from archive MQTT data."""
        if not hasattr(self, "_archive_mqtt") or not hasattr(self, "_time_ctrl"):
            return []

        dense_obs = getattr(self, "_archive_vehicle_obs", None)
        if (
            dense_obs is not None
            and vehicle_id in dense_obs.available_vehicle_ids
        ):
            return dense_obs.history(vehicle_id, self._time_ctrl.current_time)
        
        from network import vehicle_sync
        
        current_time = self._time_ctrl.current_time
        observations = []
        
        # archive MQTT records are stored as list of (timestamp, dict) tuples
        vehicle_records = self._archive_mqtt._data.get("vehicles", [])
        for ts, record in vehicle_records:
            # only include records up to current archive time
            if ts > current_time:
                break
            
            if record.get("vehicle_id") != vehicle_id:
                continue
            
            # convert to Observation
            try:
                obs = vehicle_sync._observation_from_payload(record)
                # only include if has met data
                if (obs.temperature_c is not None or obs.dewpoint_c is not None or 
                    obs.wind_speed_ms is not None or obs.pressure_mb is not None):
                    observations.append(obs)
            except Exception:
                continue
        
        return observations

    def _sync_vehicle_detail_visibility(self):
        if not hasattr(self, "vehicle_detail_panel"):
            return
        if not self.btn_vehicles.isChecked():
            self.vehicle_detail_panel.hide()
            return
        if not self._selected_vehicle_ids:
            self.vehicle_detail_panel.hide()
            return
        self.vehicle_detail_panel.show()

    def _refresh_vehicle_detail(self):
        if not hasattr(self, "vehicle_detail_panel"):
            return
        if not self._selected_vehicle_ids:
            self._vehicle_detail_title.setText("VEHICLE")
            _clear_layout(self._vehicle_detail_body_layout)
            return
        self._vehicle_detail_title.setText(
            f"DETAILS ({len(self._selected_vehicle_ids)})"
        )
        _clear_layout(self._vehicle_detail_body_layout)
        for vid in self._selected_vehicle_ids:
            vehicle = self._vehicles.get(vid)
            section = self._make_vehicle_detail_section(vid, vehicle)
            self._vehicle_detail_body_layout.addWidget(section)
        self._layout_overlays()

    def _make_vehicle_detail_section(self, vid: str, vehicle) -> QWidget:
        obs = vehicle.latest_obs if vehicle else None

        section = QFrame()
        section.setStyleSheet(f"QFrame {{ background: transparent; border-bottom: 1px solid {P.BORDER_3}; }}")
        sl = QVBoxLayout(section)
        sl.setContentsMargins(0, 6, 0, 6)
        sl.setSpacing(4)

        if obs is None:
            top = QHBoxLayout()
            name = QLabel(vid)
            name.setStyleSheet(f"color: {P.TEXT}; font-weight: 600; background: transparent; border: none;")
            top.addWidget(name)
            no_obs = QLabel("no observations")
            no_obs.setStyleSheet(f"color: {P.TEXT_LABEL}; margin-left: 6px; background: transparent; border: none;")
            top.addWidget(no_obs)
            top.addStretch()
            sl.addLayout(top)
            return section

        badge_color = self._obs_age_color(obs)

        # top row: badge · name · age · TIMESERIES button · lat/lon
        top = QHBoxLayout()
        top.setSpacing(8)

        badge = QLabel("●")
        badge.setStyleSheet(f"color: {badge_color}; font-size: 12px; background: transparent; border: none;")
        top.addWidget(badge)

        name_lbl = QLabel(vid)
        name_lbl.setStyleSheet(f"color: {P.TEXT}; font-weight: 600; background: transparent; border: none;")
        top.addWidget(name_lbl)

        age_lbl = QLabel(f"{self._obs_age_label(obs)} old")
        age_lbl.setStyleSheet(f"color: {P.TEXT_SECONDARY}; background: transparent; border: none;")
        top.addWidget(age_lbl)
        
        # timeseries button — only shown if vehicle has met data history
        has_history = bool(self._vehicle_history.get(vid))
        if has_history:
            ts_btn = QPushButton("TIMESERIES")
            ts_btn.setFlat(True)
            ts_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: 1px solid {P.INFO}; "
                f"color: {P.INFO}; font-size: 11px; font-weight: 600; padding: 2px 6px; "
                "border-radius: 3px; }"
                f"QPushButton:hover {{ background: {P.alpha(P.INFO, 0.1)}; }}"
            )
            ts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ts_btn.clicked.connect(lambda checked=False, v=vid: self._on_timeseries_button_clicked(v))
            top.addWidget(ts_btn)

        top.addStretch()

        for key, val in [("lat", f"{obs.lat:.4f}"), ("lon", f"{obs.lon:.4f}")]:
            k_lbl = QLabel(key)
            k_lbl.setStyleSheet(f"color: {P.TEXT_SECONDARY}; background: transparent; border: none;")
            top.addWidget(k_lbl)
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet(f"color: {P.TEXT}; background: transparent; border: none;")
            top.addWidget(v_lbl)

        sl.addLayout(top)

        # timestamp
        ts = obs.timestamp.astimezone(timezone.utc).strftime("%d %b %Y %H%M UTC")
        ts_lbl = QLabel(ts)
        ts_lbl.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px; background: transparent; border: none;")
        sl.addWidget(ts_lbl)

        # obs values grid
        temp_txt = f"{obs.temperature_c * 9/5 + 32:.0f}°F" if obs.temperature_c is not None else "--"
        dew_txt  = f"{obs.dewpoint_c   * 9/5 + 32:.0f}°F" if obs.dewpoint_c   is not None else "--"
        wind_txt = (
            f"{obs.wind_speed_ms * 1.94384:.0f}kt @ {obs.wind_dir_deg:.0f}°"
            if obs.wind_speed_ms is not None and obs.wind_dir_deg is not None else "--"
        )
        pres_txt = f"{obs.pressure_mb:.1f}mb" if obs.pressure_mb is not None else "--"

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        for row_idx, (lbl1, val1, lbl2, val2) in enumerate([
            ("Temp", temp_txt, "Dew",  dew_txt),
            ("Wind", wind_txt, "Pres", pres_txt),
        ]):
            for col, (text, is_val) in enumerate([(lbl1, False), (val1, True), (lbl2, False), (val2, True)]):
                cell = QLabel(text)
                cell.setStyleSheet(
                    f"color: {P.TEXT}; background: transparent; border: none;"
                    if is_val else
                    f"color: {P.TEXT_SECONDARY}; background: transparent; border: none;"
                )
                grid.addWidget(cell, row_idx, col)

        sl.addLayout(grid)
        return section


    def _init_data_inputs(self):
        """Start Track A (file watcher) and/or Track B (GPS) if configured."""
        self._gps_reader: GPSReader | None = None
        self._obs_watcher: ObsFileWatcher | None = None
        self._last_mqtt_publish = 0.0   # monotonic timestamp of last MQTT vehicle publish

        if self._monitor:
            log.info("Monitor mode — no local data inputs started")
            QTimer.singleShot(1500, self._show_monitor_mode_status)
            return

        # track B — GPS puck auto-detect (used when Track A file watcher is not configured)
        if not config.OBS_FILE_DIR:
            self._gps_reader = GPSReader(
                vehicle_id=config.VEHICLE_ID,
                port="",
                baud=config.GPS_BAUD,
                parent=self,
            )
            self._gps_reader.obs_ready.connect(self._on_local_vehicle_obs)
            self._gps_reader.start()
            log.info("GPS reader started in auto-detect mode")

        # track A — instrument file watcher (surface obs vehicles)
        if config.OBS_FILE_DIR:
            if config.OBS_FILE_GPS_MODE:
                field_map = FieldMap(
                    lat="Latitude",
                    lon="Longitude",
                    date_col="ddmmyy",
                    time_col="hhmmss[UTC]",
                    temperature_c="",
                    dewpoint_c="",
                    wind_speed_ms="",
                    wind_dir_deg="",
                    pressure_mb="",
                )
            else:
                field_map = FieldMap(
                    lat=config.OBS_FILE_COL_LAT,
                    lon=config.OBS_FILE_COL_LON,
                    date_col=config.OBS_FILE_COL_DATE,
                    time_col=config.OBS_FILE_COL_TIME,
                    temperature_c=config.OBS_FILE_COL_TEMP,
                    dewpoint_c=config.OBS_FILE_COL_DEWP,
                    wind_speed_ms=config.OBS_FILE_COL_WSPD,
                    wind_dir_deg=config.OBS_FILE_COL_WDIR,
                    pressure_mb=config.OBS_FILE_COL_PRES,
                )
            self._obs_watcher = ObsFileWatcher(
                data_dir=config.OBS_FILE_DIR,
                vehicle_id=config.VEHICLE_ID,
                field_map=field_map,
                poll_interval_s=config.OBS_FILE_POLL_S,
                gps_mode=config.OBS_FILE_GPS_MODE,
                parent=self,
            )
            self._obs_watcher.obs_ready.connect(self._on_local_vehicle_obs)
            self._obs_watcher.start()
            log.info("Obs file watcher started: dir=%s gps_mode=%s",
                     config.OBS_FILE_DIR, config.OBS_FILE_GPS_MODE)
        else:
            log.info("Obs file dir not configured (obs_file.data_dir empty) — Track A disabled")
