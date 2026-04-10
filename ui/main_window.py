# ui/main_window.py
# top-level application window for STORM.
# assembles the layout: toolbar, map widget, status bar, and collapsible panels.

import csv
import json
import logging
import sqlite3
import threading
import time
import runtime_flags
from collections import deque
from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QLabel, QDockWidget, QVBoxLayout, QHBoxLayout,
    QToolButton, QFrame, QCheckBox, QPushButton, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, QObject, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from ui.theme import DARK_THEME, ACCENT
from ui.map_widget import MapWidget, TILES_PATH
from ui.radar_controls import RadarControls, NEXRAD_SITES
from ui.hazard_controls import HazardControls
from ui.routing_controls import RoutingControls, _make_loc_icon
from ui.nav_pill import NavPill
from ui.deploy_locs_controls import DeployLocsControls
from ui.satellite_controls import SatelliteControls
from ui.surface_controls import SurfaceControls
from ui.outlook_panel import OutlookPanel
from ui.radar_overlay import RadarOverlay, render_scan_to_png as _render_scan_to_png
from ui.sounding_dialog import SoundingDialog
from ui.sounding_controls import SoundingControls
from ui.vehicle_timeseries_dialog import VehicleTimeseriesDialog
from ui.annotation_tools import AnnotationTools
from ui.annotation_dialog import AnnotationPlaceDialog, AnnotationEditDialog, AnnotationMoveConfirmDialog
from ui.drawing_dialog import (
    DrawingTitleDialog, DrawingEditDialog, DrawingPlaceConfirmDialog,
    DrawingMoveConfirmDialog,
)
from ui.storm_cone_dialog import (
    StormConeInputDialog, StormConePlaceConfirmDialog, StormConeMoveConfirmDialog,
)
from data.radar_fetcher import RadarFetcher
from data.sounding_fetcher import SoundingFetcher
from data.obs_sounding_fetcher import ObsSoundingFetcher
from data.clamps_sounding_fetcher import ClampsSoundingFetcher
from data.sounding_stations import build_stations_geojson
from data.hazard_fetcher import HazardFetcher
from data.update_checker import UpdateWorker
from data.satellite_fetcher import SatelliteFetcher
from data.surface_fetcher import SurfaceFetcher
from data.radar_decoder import decode_nexrad_l3
import config
from core.annotation import Annotation, ANNOTATION_TYPE_MAP
from core.storm_cone import StormCone
from core.drawing import DrawingAnnotation, DRAWING_TYPE_MAP, FRONT_TYPE_KEYS
from core.observation import Observation
from core.vehicle import Vehicle
from network.mqtt_client import MQTTClient
from network.annotation_sync import AnnotationSync
from network.storm_cone_sync import StormConeSync
from network.drawing_sync import DrawingSync
from network.vehicle_sync import VehicleSync
from data.gps_reader import GPSReader
from data.obs_file_watcher import ObsFileWatcher, FieldMap
from ui.station_plot_layer import StationPlotLayer
from ui.surface_plot_layer import SurfacePlotLayer
from ui.layer_order_pill import LayerOrderPill, MAPLIBRE_LAYERS
from ui.debug_pill import DebugPill

log = logging.getLogger(__name__)


def _coords_close(a, b, tol: float = 1e-4) -> bool:
    """Return True if two [lat, lon] points are within ~10 m of each other."""
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _clear_layout(layout):
    """Remove and schedule deletion of all widgets in a layout."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


class _NetChecker(QObject):
    """Worker that checks internet connectivity from a background thread."""
    result_ready = pyqtSignal(str)   # "ok", "slow", or "none"

    def check(self):
        import socket, time
        try:
            t0 = time.monotonic()
            s  = socket.create_connection(("1.1.1.1", 53), timeout=2)
            s.close()
            self.result_ready.emit("slow" if time.monotonic() - t0 > 1.0 else "ok")
        except OSError:
            self.result_ready.emit("none")


class MainWindow(QMainWindow):
    # Emitted from background threads to update the discussion text panel safely.
    _panel_text_ready = pyqtSignal(int, str, str)
    # Emitted from the decode thread when a scan has been decoded — carries
    # (generation, site, product, RadarScan) so main thread can cache it.
    _scan_decoded = pyqtSignal(int, str, str, object)
    # Emitted from the decode thread on a decode failure — site, product.
    _radar_decode_failed = pyqtSignal(str, str)
    # Emitted from the render thread when a PNG is ready — carries a dict with
    # gen, site, product, scan, png_b64, bounds so main thread can inject it.
    _render_ready = pyqtSignal(object)
    # Emitted when the user aborts an archive loading session.
    session_aborted = pyqtSignal()

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
        self._nws_active_phenoms: set[str] = set()  # phenom codes present in last NWS fetch

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

        self._init_map()
        self._init_toolbar()
        self._init_statusbar()
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

        # Fine-grained startup toggles are for crash-isolation only.
        # Keep them opt-in so normal runs always start full functionality.
        self._disable_radar = runtime_flags.FLAGS.disable_radar
        self._disable_mqtt = runtime_flags.FLAGS.disable_mqtt
        self._disable_annotations = runtime_flags.FLAGS.disable_annotations
        self._disable_deploy_locs = runtime_flags.FLAGS.disable_deploy_locs
        self._disable_data_inputs = runtime_flags.FLAGS.disable_data_inputs

        # Features that require MQTT should be disabled when MQTT is disabled.
        if self._disable_mqtt:
            self._disable_annotations = True
            self._disable_data_inputs = True

        if self._runtime_safe:
            log.warning("Running in safe runtime mode (radar/MQTT/data inputs disabled)")
            self._init_measure()
            self._init_stations()
            self.status_msg_label.setText("Safe runtime mode - background services disabled")
            self.status_msg_label.setStyleSheet(
                "color: #FFD166; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
            )
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
        self.map_widget.map_moved.connect(
            lambda lat, lon, zoom: (
                self.update_coordinates(lat, lon),
            )
        )

        # clock ticks every second
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._clock_layout_synced = False
        self._update_clock()

        # internet connectivity indicator — checks every 30 seconds
        self._start_net_check()

        # GPS fix age indicator — polls every 5 seconds (vehicle mode only)
        self._last_local_obs_ts: float = 0.0
        if not (self._monitor or self._viewer):
            self._gps_indicator_timer = QTimer(self)
            self._gps_indicator_timer.timeout.connect(self._update_gps_indicator)
            self._gps_indicator_timer.start(5_000)
            self._update_gps_indicator()

        # in-ops update checker — background, non-blocking, no dialogs
        self._start_update_check()

        # Restore window geometry and dock layout from last session.
        _s = QSettings("NSSL", "STORM")
        if _s.contains("geometry"):
            self.restoreGeometry(_s.value("geometry"))
        if _s.contains("windowState"):
            self.restoreState(_s.value("windowState"))
            # keep toolbar button in sync with current vehicle panel visibility
            self.btn_vehicles.setChecked(self.vehicle_panel.isVisible())

        # Layer order pill — floats above the bottom-left status pill
        self._layer_pill = LayerOrderPill(self._map_container)
        self._layer_pill.order_changed.connect(self._apply_layer_order)
        self._layer_pill.order_changed.connect(lambda _: self._layout_overlays())
        self._layer_pill.size_changed.connect(self._layout_overlays)
        self._layer_pill.show()

        # Extra startup layout passes avoid first-paint clipping in floating pills.
        QTimer.singleShot(0, self._layout_overlays)
        QTimer.singleShot(220, self._layout_overlays)

        # ctrl+d toggles debug panel even outside --debug mode (emergency diagnostic)
        self._debug_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self._debug_shortcut.activated.connect(self._toggle_debug_panel)
        # Ctrl+E toggles error log panel
        self._error_log_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        self._error_log_shortcut.activated.connect(self._toggle_error_log_panel)
        # Esc cancels in-progress line/polygon/front drawing.
        self._esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._esc_shortcut.activated.connect(self._on_escape_pressed)

        # auto-init debug panel when launched with --debug flag
        if debug:
            self._init_debug_panel()

    # ── Map ──────────────────────────────────────────────────────────────────

    def _init_map(self):
        # container fills the QMainWindow central area; map + overlays are
        # children positioned absolutely so the toolbar and status pills float
        # over the map rather than eating into it
        self._map_container = QWidget()
        self.setCentralWidget(self._map_container)

        self.map_widget = MapWidget()
        self.map_widget.setParent(self._map_container)

        # defer initial geometry until after all overlay widgets exist
        QTimer.singleShot(0, self._layout_overlays)

    # ── Archive startup ───────────────────────────────────────────────────────

    def _begin_archive_startup(self):
        """Called once the map is ready in archive mode."""
        from archive.session import ArchiveSession
        from archive.time_controller import TimeController
        from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher
        from archive.fetchers.satellite_archive_fetcher import ArchiveSatelliteFetcher
        from archive.fetchers.hazard_archive_fetcher import ArchiveHazardFetcher
        from archive.fetchers.sounding_archive_fetcher import ArchiveSoundingFetcher
        from archive.fetchers.mqtt_reader import ArchiveMQTTReader
        from ui.archive_controls import ArchiveControls
        from ui.archive_loading_dialog import ArchiveLoadingDialog

        self._archive_session = ArchiveSession(start_time=self._archive_time)

        # Time controller — central archive clock.
        self._time_ctrl = TimeController(start_time=self._archive_time, parent=self)

        # Archive controls bar at bottom of screen.
        self._archive_controls = ArchiveControls(self._time_ctrl, self._map_container)
        self._archive_controls.setObjectName("archiveControls")
        self._archive_controls.set_radar_status("Radar: waiting")
        self._archive_controls.set_satellite_status("Sat: waiting")
        self._archive_controls.show()

        # MQTT reader — vehicles, annotations, cones, drawings.
        # Initialize annotation/cone/drawing dicts (normally set in _init_annotations
        # and _init_storm_cone, which are skipped in archive mode).
        self._annotations: dict = {}
        self._drawings: dict = {}
        self._storm_cones: dict = {}

        self._archive_mqtt = ArchiveMQTTReader(
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_mqtt.vehicle_position.connect(self._on_archive_vehicle_position)
        self._archive_mqtt.vehicles_cleared.connect(self._on_archive_vehicles_cleared)
        self._archive_mqtt.annotation_received.connect(self._recv_remote_annotation)
        self._archive_mqtt.annotation_deleted.connect(self._recv_remote_annotation_deleted)
        self._archive_mqtt.cone_received.connect(self._recv_remote_storm_cone)
        self._archive_mqtt.cone_deleted.connect(self._recv_remote_storm_cone_deleted)
        self._archive_mqtt.drawing_received.connect(self._recv_remote_drawing)
        self._archive_mqtt.drawing_deleted.connect(self._recv_remote_drawing_deleted)

        # Hazard fetcher.
        self._archive_hazard = ArchiveHazardFetcher(
            session_date=self._archive_time, parent=self
        )
        self._archive_hazard.spc_received.connect(self._on_spc_received)
        self._archive_hazard.nws_received.connect(self._on_nws_received)
        self._archive_hazard.watches_received.connect(self._on_spc_watches_received)
        self._archive_hazard.spc_mds_received.connect(self._on_spc_mds_received)

        # Satellite fetcher.
        self._archive_satellite = ArchiveSatelliteFetcher(
            session_date=self._archive_time, parent=self
        )
        self._archive_satellite.frame_ready.connect(self._on_archive_satellite_frame)
        self._archive_satellite.meso_sectors_updated.connect(self._on_meso_sectors_updated)

        # Sounding dialog and archive fetcher (on-demand).
        self._sounding_dialog = SoundingDialog(self)
        self._archive_sounding = ArchiveSoundingFetcher(parent=self)
        self._archive_sounding.sounding_ready.connect(self._on_sounding_ready)
        self._archive_sounding.fetch_error.connect(
            lambda msg: self.status_msg_label.setText(f"Sounding: {msg}")
        )
        # Also initialise the sounding-station layer so the map shows clickable sites.
        self._sounding_stations_geojson = build_stations_geojson()

        # Radar overlay (reuses existing renderer).
        self._radar_overlay = RadarOverlay(self.map_widget)

        # Radar fetcher — created once the station is known.
        self._archive_radar: "ArchiveRadarFetcher | None" = None

        # Wire time controller to archive fetchers.
        self._time_ctrl.time_changed.connect(self._archive_mqtt.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_hazard.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_satellite.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_sounding.on_time_changed)

        # Show loading dialog while initial data fetches run.
        # "Radar index" is included — the S3 scan listing must complete before
        # playback is useful.  The actual first-scan download happens after the
        # dialog closes (status bar shows progress).
        loading_tasks = ["Vehicle tracks", "Radar index", "Hazard data", "Satellite index"]
        self._archive_loading = ArchiveLoadingDialog(
            session_label=self._archive_time.strftime("%Y-%m-%d  %H:%M UTC"),
            tasks=loading_tasks,
            parent=self,
        )

        # Start background fetches; mark tasks done via callbacks.
        self._archive_mqtt.load()
        self._archive_hazard.load_day_data()
        self._archive_satellite.load_capabilities()

        # Track completion of loading tasks.
        def _check_mqtt_loaded():
            if self._archive_mqtt._loaded:
                self._archive_loading.set_task_done("Vehicle tracks")
                self._try_auto_select_radar_station()
            else:
                QTimer.singleShot(500, _check_mqtt_loaded)

        def _check_hazard_loaded():
            if self._archive_hazard._watches_loaded:
                self._archive_loading.set_task_done("Hazard data")
            else:
                QTimer.singleShot(500, _check_hazard_loaded)

        self._archive_satellite.error.connect(
            lambda _: self._archive_loading.set_task_error("Satellite index")
            if self._archive_loading.isVisible() else None
        )
        self._archive_satellite.error.connect(self._on_archive_satellite_error)

        def _check_satellite_indexed():
            if not self._archive_loading.isVisible():
                return
            if "conus" in self._archive_satellite._indexed_modes:
                self._archive_loading.set_task_done("Satellite index")
            else:
                QTimer.singleShot(1000, _check_satellite_indexed)

        QTimer.singleShot(500, _check_satellite_indexed)

        QTimer.singleShot(400, _check_mqtt_loaded)
        QTimer.singleShot(400, _check_hazard_loaded)

        self._archive_loading.show()
        # Trigger an initial data load for all fetchers once loading completes.
        self._archive_loading.accepted.connect(
            lambda: self._time_ctrl.set_time(self._time_ctrl.current_time)
        )
        self._archive_loading.rejected.connect(self._on_archive_loading_aborted)

        # ── Archive hazard controls wiring ────────────────────────────────────
        # The archive hazard fetcher pushes data on every time tick; the
        # controls just toggle map-layer visibility and update the legend.
        self.hazard_controls.spc_mode_changed.connect(self._on_archive_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_archive_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_archive_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_archive_nws_toggled)
        self.hazard_controls.fetch_requested.connect(self._archive_hazard.refresh_now)
        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        # ── Satellite opacity slider works in archive mode ────────────────────
        self.satellite_controls.configure_for_archive(True)
        self.satellite_controls.mode_changed.connect(self._on_satellite_mode_changed)
        self.satellite_controls.opacity_changed.connect(self.map_widget.set_satellite_opacity)
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        # ── Archive sounding wiring ───────────────────────────────────────────
        self.map_widget.sounding_clicked.connect(self._on_archive_sounding_map_click)
        self.map_widget.obs_sounding_station_clicked.connect(self._on_archive_obs_station_click)
        # mode_changed is normally connected in _init_soundings (not called in archive).
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)

        # ── Radar controls — archive mode keeps station / product / tilt here ───
        self.radar_controls.configure_for_archive(True)
        self.radar_controls.product_changed.connect(self._on_archive_product_changed)
        self.radar_controls.tilt_changed.connect(self._on_archive_tilt_changed)

        # ── Radar station picker → archive fetcher ───────────────────────────
        # Populate the map overlay with station markers (normally done in
        # _init_radar for live mode, but that path is skipped in archive).
        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)

        # ── Set initial time on the time controller (emits time_changed). ─────
        QTimer.singleShot(200, lambda: self._time_ctrl.set_time(self._archive_time))

        # Lay out the archive controls bar at the bottom of the screen.
        QTimer.singleShot(0, self._layout_overlays)

    def _try_auto_select_radar_station(self) -> None:
        """Pick the nearest NEXRAD station from vehicle positions, or fall back
        to the home location when no MQTT data is available."""
        if not hasattr(self, "_archive_mqtt"):
            return
        positions = self._archive_mqtt.first_vehicle_positions()
        if positions:
            _, lat, lon = positions[0]
            site = self._nearest_nexrad(lat, lon, archive=True)
        else:
            # No vehicle data — fall back to the configured home location so
            # radar still starts without requiring a manual station pick.
            log.info(
                "Archive: no vehicle positions — falling back to home location "
                "(%.3f, %.3f) for radar station selection",
                config.HOME_LAT, config.HOME_LON,
            )
            site = self._nearest_nexrad(config.HOME_LAT, config.HOME_LON, archive=True)

        if not site:
            self.status_msg_label.setText(
                "Could not determine radar station — select one on the map"
            )
            return

        self._archive_session.radar_station = site
        self._start_archive_radar(site)

    def _nearest_nexrad(self, lat: float, lon: float, archive: bool = False) -> "str | None":
        """Return the 4-letter NEXRAD ID closest to lat/lon.

        When archive=True, stations known to be absent from the public Level-2
        archive are excluded from consideration.
        """
        from archive.fetchers.radar_archive_fetcher import ARCHIVE_UNAVAILABLE_STATIONS
        best_site = None
        best_dist = float("inf")
        for info in (self._radar_station_sites or []):
            site_id = info.get("site_id", "")
            if archive and site_id in ARCHIVE_UNAVAILABLE_STATIONS:
                continue
            slat = info.get("lat", 0)
            slon = info.get("lon", 0)
            d = (lat - slat) ** 2 + (lon - slon) ** 2
            if d < best_dist:
                best_dist = d
                best_site = site_id
        return best_site

    def _start_archive_radar(self, station: str) -> None:
        """Instantiate and wire an ArchiveRadarFetcher for the given station."""
        from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher

        if self._archive_radar is not None:
            for sig in (
                self._archive_radar.scan_ready,
                self._archive_radar.loading_changed,
                self._archive_radar.error,
            ):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            try:
                self._time_ctrl.time_changed.disconnect(self._archive_radar.on_time_changed)
            except Exception:
                pass

        self._archive_radar = ArchiveRadarFetcher(
            station=station,
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_radar.scan_ready.connect(self._on_archive_radar_scan)
        self._archive_radar.loading_changed.connect(
            lambda loading: (
                self.status_msg_label.setText(f"Radar: loading {station}…" if loading else ""),
                self._archive_controls.set_radar_status(f"Radar: loading {station}…")
                if loading and hasattr(self, "_archive_controls") else None
            )
        )
        self._archive_radar.error.connect(self._on_archive_radar_error)
        self._time_ctrl.time_changed.connect(self._archive_radar.on_time_changed)

        loading = getattr(self, "_archive_loading", None)
        if loading is not None and loading.isVisible():
            self._archive_radar.index_loaded.connect(
                lambda _: loading.set_status("Fetching first radar scan…")
            )
            self._archive_radar.scan_ready.connect(
                lambda _: loading.set_task_done("Radar index")
            )
            self._archive_radar.error.connect(
                lambda _: loading.set_task_error("Radar index")
            )
            _radar_timeout = QTimer(self)
            _radar_timeout.setSingleShot(True)
            _radar_timeout.setInterval(45_000)
            _radar_timeout.timeout.connect(
                lambda: loading.set_task_done("Radar index")
                if loading.isVisible() else None
            )
            _radar_timeout.start()

        if hasattr(self, "radar_controls"):
            self.radar_controls.set_selected_site(station, emit=False)

        self._archive_radar.load_index()
        self._archive_radar.on_time_changed(self._time_ctrl.current_time)

    # ── Archive signal handlers ───────────────────────────────────────────────

    def _on_archive_radar_scan(self, scan) -> None:
        """Render a Level-2 scan from the archive fetcher."""
        from ui.radar_overlay import render_scan_to_png, RENDER_GRID_SIZE

        # Add tilt/product selectors to archive controls the first time;
        # refresh the tilt list on every subsequent scan (VCP can change).
        if hasattr(scan, "available_products"):
            from core.level2_radar_scan import L2_PRODUCTS
            products = [
                (f, L2_PRODUCTS[f]["label"]) for f in scan.available_products
                if f in L2_PRODUCTS
            ]
            self.radar_controls.set_archive_products(products)
            current_tilt_idx = getattr(self._archive_radar, "_tilt_idx", 0)
            self.radar_controls.set_archive_tilts(scan.available_tilts, current_tilt_idx)

        try:
            archive_grid = max(RENDER_GRID_SIZE, 768)
            png, bounds, _ = render_scan_to_png(scan, archive_grid)
            self._radar_overlay.inject(png, bounds)
            if hasattr(self, "radar_controls"):
                self.radar_controls.set_scan_time(
                    scan.scan_time.strftime("%H:%MZ")
                )
            if hasattr(self, "_archive_controls"):
                self._archive_controls.set_radar_status(
                    f"Radar: {scan.pyart_field} {scan.tilt_deg:.1f}deg {scan.scan_time.strftime('%H:%MZ')}"
                )
        except Exception as exc:
            log.error("Archive radar render failed: %s", exc)
            if hasattr(self, "_archive_controls"):
                self._archive_controls.set_radar_status("Radar: render error", error=True)

    def _on_archive_satellite_frame(self, frame) -> None:
        """Update the archive satellite frame; only show if the user has toggled it on."""
        w, s, e, n = frame.bbox
        self.map_widget.set_satellite_frame(frame.b64, w, s, e, n)
        self._archive_sat_has_data = True
        self.satellite_controls.set_scan_time(frame.time_str)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status(
                f"Sat: AWS {frame.mode.upper()} {frame.time_str}"
            )
        self._layout_overlays()
        if self.btn_satellite.isChecked():
            self.map_widget.set_satellite_visible(True)

    def _on_archive_vehicle_position(self, obs) -> None:
        """Update a vehicle marker from the MQTT archive."""
        self.update_vehicle_obs(obs)
        
        # Also update timeseries dialog if open
        if obs.vehicle_id in self._vehicle_timeseries_dialogs:
            dlg = self._vehicle_timeseries_dialogs[obs.vehicle_id]
            if dlg.isVisible():
                observations = self._get_archive_vehicle_history(obs.vehicle_id)
                if observations:
                    dlg.load(obs.vehicle_id, observations)

    def _on_archive_vehicles_cleared(self) -> None:
        """Remove all vehicle markers when time jumps backward."""
        for vid in list(self._vehicles.keys()):
            self.map_widget.remove_vehicle(vid)
        self._vehicles.clear()
        self.update_vehicle_count(0)

    def _on_archive_tilt_changed(self, tilt_idx: int) -> None:
        if self._archive_radar:
            self._archive_radar.set_tilt_index(tilt_idx)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status(f"Radar: loading tilt {tilt_idx}")

    def _on_archive_product_changed(self, pyart_field: str) -> None:
        if self._archive_radar:
            self._archive_radar.set_product(pyart_field)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status(f"Radar: loading {pyart_field}")

    def _on_archive_radar_error(self, msg: str) -> None:
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status("Radar: error", error=True)
        self.status_msg_label.setText(msg)
        self._layout_overlays()

    def _on_archive_loading_aborted(self) -> None:
        """User clicked Abort on the loading dialog — quit the application."""
        self.session_aborted.emit()
        QApplication.quit()

    def _on_archive_satellite_error(self, msg: str) -> None:
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status("Sat: error", error=True)
        self.status_msg_label.setText(f"Satellite: {msg}")
        self._layout_overlays()

    # ── Archive hazard handlers ───────────────────────────────────────────────

    def _on_archive_spc_mode_changed(self, mode: str) -> None:
        """Toggle SPC outlook/product layers in archive mode (no live fetcher)."""
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self.map_widget.set_spc_category_visible(key, outlook_on)
        for key in ("tor", "wind", "hail"):
            on = mode == key
            self.map_widget.set_spc_product_visible(key, on)
        self._update_hazard_legend()

    def _on_archive_watches_toggled(self, enabled: bool) -> None:
        self.map_widget.set_spc_watches_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_nws_toggled(self, enabled: bool) -> None:
        self.map_widget.set_nws_warnings_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_mds_toggled(self, enabled: bool) -> None:
        self.map_widget.set_spc_mds_visible(enabled)
        self._update_hazard_legend()

    # ── Archive sounding handlers ─────────────────────────────────────────────

    def _on_archive_sounding_map_click(self, lat: float, lon: float) -> None:
        self.status_msg_label.setText("Fetching archive sounding…")
        self._archive_sounding.fetch_model_sounding(lat, lon)

    def _on_archive_obs_station_click(
        self, station_id: str, name: str, lat: float, lon: float, elev: float
    ) -> None:
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._archive_sounding.fetch_obs_sounding(station_id, lat, lon, elev)

    # ── Live startup sequence (unchanged) ────────────────────────────────────

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
        import ssl
        from urllib.request import urlopen, Request

        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode    = ssl.CERT_NONE

        url = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/Storm/current.json"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 STORM/1.0"})
            with urlopen(req, timeout=10, context=_ctx) as resp:
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

        if not self._disable_radar:
            self._init_radar()
        self._init_hazards()
        self._init_satellite()
        self._init_surface_obs()
        self._apply_launch_prefs()

    def _apply_launch_prefs(self):
        """Auto-enable map layers based on launch dialog preferences."""
        if self._launch_auto_spc:
            self.hazard_controls._btn_outlook.setChecked(True)
        if self._launch_auto_nws:
            self.hazard_controls._btn_nws_warnings.setChecked(True)
        if self._launch_auto_radar and not self._disable_radar and hasattr(self, "_radar_fetcher"):
            self.radar_controls._chk_show_data.blockSignals(True)
            self.radar_controls._chk_show_data.setChecked(True)
            self.radar_controls._chk_show_data.blockSignals(False)
            self._auto_start_radar()
            self.btn_radar.setChecked(True)
        if self._launch_auto_obs_ok:
            self.surface_controls._btn_ok.setChecked(True)
        if self._launch_auto_obs_wtm:
            self.surface_controls._btn_wtm.setChecked(True)
        sat = self._launch_auto_satellite
        if sat == "conus":
            self.satellite_controls._btn_conus.setChecked(True)
        elif sat == "auto_meso":
            self._auto_meso_pending = True
            self._satellite_fetcher.meso_sectors_updated.connect(self._on_auto_meso_caps_ready)

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

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _init_toolbar(self):
        self._floating_toolbar = QWidget(self._map_container)
        self._floating_toolbar.setObjectName("floatingToolbar")

        tb = QHBoxLayout(self._floating_toolbar)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(4)

        # ── radar ─────────────────────────────────────────────────────────
        self.btn_radar = self._toolbar_toggle("RADAR", "Show/hide radar controls", tb)
        # Radar controls drop down below the toolbar as a separate floating pill
        self.radar_controls = RadarControls(self._map_container)
        self.radar_controls.setObjectName("floatingToolbar")
        self.btn_radar.toggled.connect(self.radar_controls.toggle_drawer)
        self.btn_radar.toggled.connect(
            lambda on: self._set_radar_station_picker_visible(False) if not on else None
        )
        # pulse layout updates for the duration of the open/close animation
        self.btn_radar.toggled.connect(self._start_layout_pulse)

        self._add_separator(tb)

        # ── vehicles ──────────────────────────────────────────────────────
        self.btn_vehicles = self._toolbar_toggle("VEHICLES", "Toggle vehicle panel", tb)

        # ── previous deployment locations ─────────────────────────────────
        self.btn_prev_locs = self._toolbar_toggle(
            "PREV LOCS", "Show previous truck deployment locations", tb
        )
        self.deploy_locs_controls = DeployLocsControls(self._map_container)
        self.deploy_locs_controls.setObjectName("floatingToolbar")
        self.btn_prev_locs.toggled.connect(self.deploy_locs_controls.toggle_drawer)
        self.btn_prev_locs.toggled.connect(self._start_layout_pulse)
        self.deploy_locs_controls.content_resized.connect(self._start_layout_pulse)

        self._add_separator(tb)

        # ── hazards ───────────────────────────────────────────────────────
        self.btn_hazards = self._toolbar_toggle(
            "HAZARDS", "Show/hide SPC and NWS hazard overlays", tb
        )
        self.hazard_controls = HazardControls(self._map_container)
        self.hazard_controls.setObjectName("floatingToolbar")
        self.btn_hazards.toggled.connect(self.hazard_controls.toggle_drawer)
        self.btn_hazards.toggled.connect(self._start_layout_pulse)
        self.hazard_controls.content_resized.connect(self._start_layout_pulse)

        self.outlook_panel = OutlookPanel(self._map_container)
        self.outlook_panel.closed.connect(self._layout_overlays)
        self._fetch_generation = 0
        self._panel_text_ready.connect(self._on_panel_text_ready)

        self._add_separator(tb)

        # ── satellite ─────────────────────────────────────────────────────
        self.btn_satellite = self._toolbar_toggle(
            "SATELLITE", "Show/hide GOES satellite imagery overlay", tb
        )
        self.satellite_controls = SatelliteControls(self._map_container)
        self.satellite_controls.setObjectName("floatingToolbar")
        self.btn_satellite.toggled.connect(self.satellite_controls.toggle_drawer)
        self.btn_satellite.toggled.connect(self._start_layout_pulse)
        self.btn_satellite.toggled.connect(self._on_satellite_toggled)

        self._add_separator(tb)

        self.btn_surface = self._toolbar_toggle(
            "SURFACE", "Show/hide surface observation controls", tb
        )
        self.surface_controls = SurfaceControls(self._map_container)
        self.surface_controls.setObjectName("floatingToolbar")
        self.btn_surface.toggled.connect(self.surface_controls.toggle_drawer)
        self.btn_surface.toggled.connect(self._start_layout_pulse)
        self.surface_controls.content_resized.connect(self._start_layout_pulse)

        self._add_separator(tb)

        # ── sounding ──────────────────────────────────────────────────────
        self.btn_sounding = self._toolbar_toggle(
            "SOUNDING", "Click map for HRRR point sounding or observed radiosonde data", tb
        )
        self.sounding_controls = SoundingControls(self._map_container)
        self.sounding_controls.setObjectName("floatingToolbar")
        self.btn_sounding.toggled.connect(self.sounding_controls.toggle_drawer)
        self.btn_sounding.toggled.connect(self._start_layout_pulse)
        self.btn_sounding.toggled.connect(self._on_sounding_mode_toggled)

        self._add_separator(tb)

        # ── annotations ───────────────────────────────────────────────────
        self.btn_annotate = self._toolbar_toggle(
            "ANNOTATE", "Place road annotations and storm motion cone", tb
        )
        # Annotation tools drop down below the toolbar as a separate floating pill
        self.annotation_tools = AnnotationTools(self._map_container)
        self.annotation_tools.setObjectName("floatingToolbar")
        self.btn_annotate.toggled.connect(self.annotation_tools.toggle_drawer)
        self.btn_annotate.toggled.connect(self._start_layout_pulse)

        self._add_separator(tb)

        # ── measure ───────────────────────────────────────────────────────
        self.btn_measure = self._toolbar_toggle(
            "MEASURE", "Click two points to measure distance", tb
        )

        self._add_separator(tb)

        # ── route / directions ────────────────────────────────────────────
        self.btn_route = self._toolbar_toggle(
            "ROUTE", "Get turn-by-turn directions", tb
        )
        self.routing_controls = RoutingControls(self._map_container)
        self.routing_controls.setObjectName("floatingToolbar")
        self.btn_route.toggled.connect(self.routing_controls.toggle_drawer)
        self.btn_route.toggled.connect(self._start_layout_pulse)
        self.routing_controls.enter_pick_mode.connect(
            lambda: self.map_widget.set_route_pick_mode(True)
        )
        self.routing_controls.enter_pick_mode.connect(
            lambda: self.btn_sounding.setChecked(False) if self.btn_sounding.isChecked() else None
        )
        self.routing_controls.cancel_pick_mode.connect(
            lambda: self.map_widget.set_route_pick_mode(False)
        )
        self.routing_controls.route_calculated.connect(self._on_route_calculated)
        self.routing_controls.route_cleared.connect(self._on_route_cleared)
        self.routing_controls.content_resized.connect(self._start_layout_pulse)
        self.map_widget.map_pick_for_route.connect(
            self.routing_controls.on_map_pick
        )
        if self._viewer or self._archive:
            self.btn_route.hide()
        if self._archive:
            self.btn_prev_locs.hide()
            # No archive data sources for these — hide to prevent crashes.
            self.btn_surface.hide()     # _surface_fetcher / _surface_layer absent
            self.btn_annotate.hide()    # _mqtt_client / _annotation_sync / _drawing_sync absent

        # ── Navigation pill (upper-right, visible when route active + drawer closed)
        self.nav_pill = NavPill(self._map_container)
        self._pill_route_expanded = False
        self.routing_controls.nav_updated.connect(self._on_nav_updated)
        self.routing_controls.route_cleared.connect(self.nav_pill.nav_clear)
        self.routing_controls.route_cleared.connect(self._layout_overlays)
        self.routing_controls.route_cleared.connect(self._on_pill_route_cleared)
        self.nav_pill.open_drawer_requested.connect(self._on_pill_expand_requested)
        self.nav_pill.clear_requested.connect(self.routing_controls._on_clear)
        self.btn_route.toggled.connect(self._on_route_drawer_toggled)

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
        self._start_layout_pulse()

    def _on_pill_route_cleared(self):
        """Collapse the pill-expanded routing panel when the route is cleared."""
        if self._pill_route_expanded:
            self._pill_route_expanded = False
            self.nav_pill.set_expanded(False)
            self.routing_controls.toggle_drawer(False)

    def _on_route_drawer_toggled(self, checked: bool):
        """Hide pill while toolbar drawer is open; restore it when drawer closes."""
        if checked:
            # If pill-expand was open, close it — toolbar takes over
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
        self.map_widget.set_route_pick_mode(False)

    def _toolbar_toggle(self, label: str, tooltip: str, layout: QHBoxLayout) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setChecked(False)
        layout.addWidget(btn)
        return btn

    def _add_separator(self, layout: QHBoxLayout):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #394056; margin: 6px 2px;")
        layout.addWidget(sep)

    # ── Status Bar ────────────────────────────────────────────────────────────

    def _init_statusbar(self):
        # ── Single bottom-left overlay pill — three rows ──────────────────
        # Row 0: version anchor | [update indicator] | status message
        # Row 1: mode | coords | vehicle count
        # Row 2: [GPS] | AWS | NET | date | time
        self._status_left = QWidget(self._map_container)
        self._status_left.setObjectName("statusOverlayLeft")
        pill = QVBoxLayout(self._status_left)
        pill.setContentsMargins(10, 5, 10, 5)
        pill.setSpacing(3)

        # ── Row 0: notifications / status message (always has content) ────
        row0 = QHBoxLayout()
        row0.setContentsMargins(0, 0, 0, 0)
        row0.setSpacing(8)

        version_label = QLabel(f"STORM v{config.VERSION}")
        version_label.setStyleSheet(
            "color: #4A5268; font-size: 10px; font-weight: 600; letter-spacing: 0.8px;"
        )
        row0.addWidget(version_label)

        # in-ops update indicator — hidden until an update is detected
        self.update_indicator = QPushButton("↑ UPDATE AVAILABLE")
        self.update_indicator.setFlat(True)
        self.update_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
            "color: #00CFFF; background: transparent; border: none; padding: 0;"
        )
        self.update_indicator.setToolTip("Click to apply update and restart STORM")
        self.update_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_indicator.setVisible(False)
        self.update_indicator.clicked.connect(self._on_update_indicator_clicked)
        row0.addWidget(self.update_indicator)

        self.status_msg_label = QLabel("")
        self.status_msg_label.setStyleSheet(
            "color: #9BA3B2; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row0.addWidget(self.status_msg_label)
        row0.addStretch()

        # ── Row 1: positional / operational info ──────────────────────────
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)

        self.coord_label = QLabel("LAT: ---.---- LON: ---.----")
        coord_probe = "LAT: -180.0000  LON: -180.0000"
        self.coord_label.setMinimumWidth(self.coord_label.fontMetrics().horizontalAdvance(coord_probe) + 8)
        self.vehicle_count_label = QLabel("VEHICLES: 0")

        for lbl in [self.coord_label, self.vehicle_count_label]:
            lbl.setStyleSheet("color: #C8D0DE; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;")

        # Mode badge: VEHICLE / MONITOR / VIEWER / ARCHIVE — always leftmost in row 1
        if self._archive:
            mode_badge = QLabel("● ARCHIVE")
            mode_badge.setStyleSheet(
                "color: #FF9F1C; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        elif self._monitor:
            mode_badge = QLabel("● MONITOR")
            mode_badge.setStyleSheet(
                "color: #FFD166; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        elif self._viewer:
            mode_badge = QLabel("● VIEWER")
            mode_badge.setStyleSheet(
                "color: #9B8FFF; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        else:
            mode_badge = QLabel("● VEHICLE")
            mode_badge.setStyleSheet(
                "color: #39D98A; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
        row1.addWidget(mode_badge)

        row1.addWidget(self._status_divider())
        row1.addWidget(self.coord_label)
        row1.addWidget(self._status_divider())
        row1.addWidget(self.vehicle_count_label)
        row1.addStretch()

        # ── Row 2: connection indicators + clock ──────────────────────────
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)

        # GPS fix status — vehicle mode only (hidden in monitor/viewer)
        self.gps_indicator = QLabel("● NO GPS FIX")
        self.gps_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
        )
        self.gps_indicator.setVisible(not (self._monitor or self._viewer))
        row2.addWidget(self.gps_indicator)

        if not (self._monitor or self._viewer):
            row2.addWidget(self._status_divider())

        self.conn_indicator = QLabel("● AWS OFFLINE")
        self.conn_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
        )
        row2.addWidget(self.conn_indicator)
        row2.addWidget(self._status_divider())

        self.net_indicator = QLabel("")
        self.net_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #3A3B4A;"
        )
        self.net_indicator.setVisible(False)
        row2.addWidget(self.net_indicator)

        row2.addStretch()

        self.date_label = QLabel("-- --- ----")
        self.date_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #C8D0DE;"
        )
        row2.addWidget(self.date_label)

        row2.addWidget(self._status_divider())

        self.clock_label = QLabel("--:--:-- UTC")
        self.clock_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #C8D0DE;"
        )
        row2.addWidget(self.clock_label)

        # keep hazard_indicator as a hidden member so existing callers don't break
        self.hazard_indicator = QLabel("● DATA OFFLINE")
        self.hazard_indicator.setVisible(False)

        pill.addLayout(row0)
        pill.addLayout(row1)
        pill.addLayout(row2)


    def _status_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #394056; margin: 4px 0;")
        return div

    def closeEvent(self, event):
        _s = QSettings("NSSL", "STORM")
        _s.setValue("geometry", self.saveGeometry())
        _s.setValue("windowState", self.saveState())
        from ui.layer_order_pill import save_layer_order
        save_layer_order(self._layer_pill.current_order())

        # Stop all background workers before closing so threads don't outlive
        # the window and fire signals on deleted objects.
        self._clock_timer.stop()
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
            # wide-mode scales toolbar controls slightly on larger windows
            wide_mode = r.width() >= 1500
            if self._floating_toolbar.property("wide") != wide_mode:
                self._floating_toolbar.setProperty("wide", wide_mode)
                self._floating_toolbar.style().unpolish(self._floating_toolbar)
                self._floating_toolbar.style().polish(self._floating_toolbar)
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
                widget.adjustSize()
                w = widget.width()
                x = max(0, (r.width() - w) // 2)
                h = widget.height()
                widget.setGeometry(x, _stack_y, w, h)
                widget.raise_()
                _stack_y += h + 6

            if hasattr(self, "radar_controls") and self.btn_radar.isChecked():
                _stack(self.radar_controls)
            if hasattr(self, "vehicle_panel") and self.vehicle_panel.isVisible():
                _stack(self.vehicle_panel)
            if hasattr(self, "vehicle_detail_panel") and self.vehicle_detail_panel.isVisible():
                _stack(self.vehicle_detail_panel)
            if hasattr(self, "deploy_locs_controls") and self.btn_prev_locs.isChecked():
                _stack(self.deploy_locs_controls)
            if hasattr(self, "hazard_controls") and self.btn_hazards.isChecked():
                _stack(self.hazard_controls)
            if hasattr(self, "satellite_controls") and self.btn_satellite.isChecked():
                _stack(self.satellite_controls)
            if hasattr(self, "surface_controls") and self.btn_surface.isChecked():
                _stack(self.surface_controls)
            if hasattr(self, "sounding_controls") and self.btn_sounding.isChecked():
                _stack(self.sounding_controls)
            if hasattr(self, "annotation_tools") and self.btn_annotate.isChecked():
                _stack(self.annotation_tools)
            if hasattr(self, "routing_controls") and self.btn_route.isChecked():
                _stack(self.routing_controls)

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
        if hasattr(self, "_archive_controls"):
            ac = self._archive_controls
            ac_w = min(r.width() - 2 * MARGIN, 820)
            ac.setFixedWidth(ac_w)
            ac.adjustSize()
            arc_bar_h = ac.sizeHint().height() + 6
            ac_x = max(MARGIN, (r.width() - ac_w) // 2)
            ac.setGeometry(ac_x, r.height() - arc_bar_h, ac_w, arc_bar_h)
            ac.raise_()

        # debug pill — bottom-center, sits above archive controls (or above bottom margin)
        if hasattr(self, "_debug_pill"):
            dp = self._debug_pill
            dp_w = dp.width()
            dp_h = dp.height()
            dp_x = max(MARGIN, (r.width() - dp_w) // 2)
            dp_bottom = r.height() - arc_bar_h - MARGIN if arc_bar_h else r.height() - MARGIN
            dp.move(dp_x, dp_bottom - dp_h)
            dp.raise_()

        # left status pill — bottom-left corner (never offset by centered archive bar)
        if hasattr(self, "_status_left"):
            self._status_left.adjustSize()
            sl = self._status_left.size()
            _status_y = r.height() - sl.height() - MARGIN
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

        # re-center button — bottom-right, above MapLibre zoom controls (~70px tall)
        if hasattr(self, "btn_recenter"):
            _ZOOM_CTRL_H = 70   # approximate height of MapLibre NavigationControl
            _GAP = 6
            btn_w = self.btn_recenter.width()
            btn_h = self.btn_recenter.height()
            self.btn_recenter.move(
                r.width() - btn_w - MARGIN,
                r.height() - _ZOOM_CTRL_H - _GAP - btn_h,
            )
            self.btn_recenter.raise_()


    def _start_layout_pulse(self):
        """Re-layout at ~60 fps for 220 ms to track drawer open/close animations."""
        if not hasattr(self, "_pulse_timer"):
            self._pulse_timer = QTimer()
            self._pulse_timer.setInterval(16)
            self._pulse_timer.timeout.connect(self._layout_overlays)
        self._pulse_timer.start()
        QTimer.singleShot(220, self._pulse_timer.stop)


    def update_coordinates(self, lat: float, lon: float):
        self.coord_label.setText(f"LAT: {lat:>9.4f}  LON: {lon:>10.4f}")

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
            self.net_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        elif state == "slow":
            self.net_indicator.setText("● NET SLOW")
            self.net_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #FFD166;"
            )
        else:
            self.net_indicator.setText("● NO INTERNET")
            self.net_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )
        self.net_indicator.setVisible(True)
        self._layout_overlays()

    def _update_gps_indicator(self):
        """Refresh GPS fix status label based on age of last local vehicle observation."""
        age = time.monotonic() - self._last_local_obs_ts
        if self._last_local_obs_ts == 0.0 or age > 30:
            self.gps_indicator.setText("● NO GPS FIX")
            self.gps_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )
        elif age > 5:
            self.gps_indicator.setText("● GPS STALE")
            self.gps_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #FFD166;"
            )
        else:
            self.gps_indicator.setText("● GPS OK")
            self.gps_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        self._layout_overlays()

    # ── In-ops update check ───────────────────────────────────────────────────

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
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #00CFFF; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(True)
        elif commits_behind == -2:
            self.update_indicator.setText("DEV BUILD")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; "
                "color: #3A3B4A; background: transparent; border: none; padding: 0;"
            )
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
        self.update_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
            "color: #5A5B6A; background: transparent; border: none; padding: 0;"
        )
        self._update_worker.start_pull()

    def _on_update_pull_done(self, success: bool, deps_changed: bool):
        import os, sys
        if success and deps_changed:
            # Can't auto-restart safely if conda env changed — tell the user
            _cmd = "conda env update -f envs/storm.yml --prune"
            self.update_indicator.setText(f"↑ DEPS CHANGED — RUN: {_cmd}  THEN RESTART")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #FFB800; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(False)
            self.update_indicator.setVisible(True)
            self._layout_overlays()
        elif success:
            self.update_indicator.setText("↑ RESTARTING...")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #39D98A; background: transparent; border: none; padding: 0;"
            )
            self._layout_overlays()
            QTimer.singleShot(600, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))
        else:
            self.update_indicator.setText("↑ UPDATE FAILED — RETRY")
            self.update_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                "color: #E53935; background: transparent; border: none; padding: 0;"
            )
            self.update_indicator.setEnabled(True)
            self.update_indicator.setVisible(True)
            self._layout_overlays()

    def set_connection_status(self, connected: bool):
        if connected:
            self.conn_indicator.setText("● AWS OK")
            self.conn_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        else:
            self.conn_indicator.setText("● AWS OFFLINE")
            self.conn_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )

    # ── Vehicle Panel (Floating Pill) ─────────────────────────────────────────

    def _init_vehicle_panel(self):
        self.vehicle_panel = QWidget(self._map_container)
        self.vehicle_panel.setObjectName("vehiclePill")
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
        placeholder.setStyleSheet("color: #3A3B5A; font-size: 10px; font-weight: 500; letter-spacing: 0.5px; padding: 6px 0;")
        self._vehicle_placeholder = placeholder
        layout.addWidget(placeholder)

        self._vehicle_rows_widget = QWidget()
        self._vehicle_rows_widget.setObjectName("vehicleRowsContainer")
        self._vehicle_rows_layout = QVBoxLayout(self._vehicle_rows_widget)
        self._vehicle_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._vehicle_rows_layout.setSpacing(0)
        self._vehicle_rows_widget.setVisible(False)
        layout.addWidget(self._vehicle_rows_widget)

        # no stretch so the pill shrink-wraps to content

        # start hidden — opened via toolbar toggle
        self.vehicle_panel.hide()
        self.btn_vehicles.toggled.connect(self.vehicle_panel.setVisible)
        self.btn_vehicles.toggled.connect(self._start_layout_pulse)
        self.btn_prev_locs.toggled.connect(self.map_widget.set_deploy_locs_visible)
        self.btn_prev_locs.toggled.connect(self._apply_deploy_locs_filter_on_show)
        self.deploy_locs_controls.metric_changed.connect(self.map_widget.set_deploy_locs_metric)
        self.deploy_locs_controls.filter_changed.connect(self.map_widget.set_deploy_locs_filter)
        self.deploy_locs_controls.size_changed.connect(self.map_widget.set_deploy_locs_size)

        # detail pill (hidden until a vehicle is selected)
        self._selected_vehicle_ids = []
        self._vehicle_age_display_state: dict[str, tuple[str, str]] = {}
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

    # ── Radar ─────────────────────────────────────────────────────────────────

    def _init_radar(self):
        self._radar_overlay = RadarOverlay(self.map_widget)
        self._radar_fetcher = RadarFetcher()
        self._scan_cache: dict[str, list] = {}   # key: "site/product" → list of RadarScan

        # background thread pool for NEXRAD decode+render — keeps heavy MetPy/numpy/scipy
        # work off the main thread so the UI stays responsive during backfill bursts.
        # Two separate pools: decode is sequential (order matters for cache), render can
        # run concurrently with the next decode but we keep it single-threaded to avoid
        # multiple large numpy arrays in memory simultaneously.
        from concurrent.futures import ThreadPoolExecutor
        self._render_scan_to_png = _render_scan_to_png
        self._decode_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="radar-decode"
        )
        self._render_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="radar-render"
        )
        # incremented on site change so in-flight decodes/renders for old site are discarded
        self._render_generation = 0

        # wire decoded-scan and render-ready signals (emitted from bg threads → main thread)
        self._scan_decoded.connect(self._on_scan_decoded)
        self._render_ready.connect(self._on_render_ready)
        self._radar_decode_failed.connect(
            lambda site, prod: self.status_msg_label.setText(
                f"Radar decode failed: {site}/{prod}"
            )
        )

        # 500 ms between loop frames — fast enough to feel animated, slow enough to read
        self._loop_timer = QTimer()
        self._loop_timer.setInterval(500)
        self._loop_timer.timeout.connect(self._advance_loop_frame)

        # pending render scan — latest scan waiting to be rendered.
        # With URL-based PNG injection (storm://app/radar/overlay.png?t=...) each
        # inject call sends only a short URL over IPC, so there is no Chromium
        # congestion concern.  The _render_in_flight guard ensures at most one
        # background render runs at a time; when it completes, any pending scan
        # is submitted immediately.  No debounce needed.
        self._pending_render_scan = None

        # guard: only one background render in flight at a time.
        self._render_in_flight = False

        # Inject throttle — rate-limits updateImage calls to MapLibre so the
        # Chromium renderer isn't overwhelmed during backfill.  Without this,
        # 6 rapid image updates freeze the renderer (can't process mouse/keyboard).
        self._last_inject_time = 0.0
        self._deferred_inject_result = None
        self._inject_throttle_timer = QTimer()
        self._inject_throttle_timer.setSingleShot(True)
        self._inject_throttle_timer.timeout.connect(self._flush_deferred_inject)

        # ── wire radar controls → fetcher ─────────────────────────────────
        self.radar_controls.radar_toggled.connect(self._on_radar_toggled)
        self.radar_controls.site_changed.connect(self._on_radar_site_changed)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)
        self.radar_controls.product_changed.connect(self._on_radar_product_changed)
        self.radar_controls.fetch_requested.connect(self._radar_fetcher.fetch_now)
        self.radar_controls.frame_requested.connect(self._display_cached_frame)
        self.radar_controls.loop_toggled.connect(self._on_loop_toggled)
        self.radar_controls.speed_changed.connect(self._on_radar_speed_changed)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)

        # ── wire fetcher → decoder → overlay ─────────────────────────────
        self._radar_fetcher.new_data.connect(self._on_radar_data)
        self._radar_fetcher.fetch_error.connect(self._on_radar_error)
        self._radar_error_clear_timer = QTimer()
        self._radar_error_clear_timer.setSingleShot(True)
        self._radar_error_clear_timer.timeout.connect(self._clear_radar_error)

        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.radar_controls.set_selected_site("KTLX")

        initial_site = self.radar_controls.current_site()
        self._radar_fetcher.set_site(initial_site)
        self._radar_fetcher.set_products(["N0B", "N0U"])

        # ── sounding ──────────────────────────────────────────────────────
        self._sounding_fetcher      = SoundingFetcher(self)
        self._obs_sounding_fetcher  = ObsSoundingFetcher(self)
        self._clamps_sounding_fetcher = ClampsSoundingFetcher(self)
        self._sounding_dialog       = SoundingDialog(self)

        self._sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._obs_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._obs_sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._clamps_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._clamps_sounding_fetcher.fetch_error.connect(self._on_sounding_error)

        self.map_widget.sounding_clicked.connect(self._on_sounding_map_click)
        self.map_widget.obs_sounding_station_clicked.connect(self._on_obs_station_click)
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)

        # Pre-build stations GeoJSON once (static asset, ~15 KB)
        self._sounding_stations_geojson = build_stations_geojson()

    def _init_hazards(self):
        self._hazard_fetcher = HazardFetcher(parent=self)
        self.hazard_controls.spc_mode_changed.connect(self._on_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_spc_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_spc_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_nws_warnings_toggled)
        self.hazard_controls.fetch_requested.connect(self._on_hazard_fetch_requested)

        self._hazard_fetcher.spc_received.connect(self._on_spc_received)
        self._hazard_fetcher.nws_received.connect(self._on_nws_received)
        self._hazard_fetcher.spc_watches_received.connect(self._on_spc_watches_received)
        self._hazard_fetcher.spc_mds_received.connect(self._on_spc_mds_received)
        self._hazard_fetcher.fetch_error.connect(self._on_hazard_error)
        self._hazard_fetcher.connectivity_changed.connect(self._on_hazard_connectivity)

        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self._hazard_error_clear_timer = QTimer()
        self._hazard_error_clear_timer.setSingleShot(True)
        self._hazard_error_clear_timer.timeout.connect(self._clear_hazard_error)

        # Seed NWS bbox from MBTiles domain extent so warnings are filtered
        # to the loaded tile set regardless of current map position.
        try:
            import sqlite3 as _sqlite3
            from ui.map_widget import TILES_PATH as _TILES_PATH
            _conn = _sqlite3.connect(_TILES_PATH)
            _row = _conn.execute(
                "SELECT value FROM metadata WHERE name='bounds'"
            ).fetchone()
            _conn.close()
            if _row:
                _lon_min, _lat_min, _lon_max, _lat_max = (
                    float(x) for x in _row[0].split(",")
                )
                self._hazard_fetcher.set_nws_bbox(
                    _lon_min, _lat_min, _lon_max, _lat_max
                )
        except Exception:
            pass

        self._hazard_fetcher.start()

        # keep top drawers mutually exclusive for clean placement
        self.btn_hazards.toggled.connect(
            lambda on: self.btn_radar.setChecked(False) if on else None
        )
        self.btn_hazards.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_radar.toggled.connect(
            lambda on: self.btn_hazards.setChecked(False) if on else None
        )

    def _init_satellite(self):
        self._satellite_fetcher  = SatelliteFetcher(parent=self)
        self._satellite_cache: dict[str, list] = {"conus": [], "meso1": [], "meso2": []}
        self._satellite_loop_timer = QTimer(self)
        self._satellite_loop_timer.setInterval(600)   # ms per frame during loop
        self._satellite_loop_timer.timeout.connect(self._satellite_loop_tick)

        # control signals → handlers
        self.satellite_controls.mode_changed.connect(self._on_satellite_mode_changed)
        self.satellite_controls.opacity_changed.connect(self.map_widget.set_satellite_opacity)
        self.satellite_controls.frame_requested.connect(self._on_satellite_frame_requested)
        self.satellite_controls.loop_toggled.connect(self._on_satellite_loop_toggled)
        self.satellite_controls.speed_changed.connect(self._on_satellite_speed_changed)
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        # fetcher signals → handlers
        self._satellite_fetcher.meso_sectors_updated.connect(self._on_meso_sectors_updated)
        self._satellite_fetcher.frames_updated.connect(self._on_satellite_frames_updated)
        self._satellite_fetcher.fetch_error.connect(self._on_satellite_error)

        self._satellite_error_clear_timer = QTimer(self)
        self._satellite_error_clear_timer.setSingleShot(True)
        self._satellite_error_clear_timer.timeout.connect(self._clear_satellite_error)

        self._satellite_fetcher.start()

        # drawer mutually exclusive with radar/hazards/annotate
        for btn, other in [
            (self.btn_satellite, self.btn_radar),
            (self.btn_satellite, self.btn_hazards),
            (self.btn_satellite, self.btn_surface),
            (self.btn_satellite, self.btn_annotate),
            (self.btn_radar,     self.btn_satellite),
            (self.btn_radar,     self.btn_surface),
            (self.btn_hazards,   self.btn_satellite),
            (self.btn_hazards,   self.btn_surface),
            (self.btn_surface,   self.btn_satellite),
            (self.btn_surface,   self.btn_radar),
            (self.btn_surface,   self.btn_hazards),
            (self.btn_surface,   self.btn_annotate),
            (self.btn_annotate,  self.btn_satellite),
            (self.btn_annotate,  self.btn_surface),
        ]:
            btn.toggled.connect(
                lambda on, o=other: o.setChecked(False) if on else None
            )

    def _init_surface_obs(self):
        self._surface_fetcher = SurfaceFetcher(parent=self)
        self._surface_layer = SurfacePlotLayer(self.map_widget)
        self._surface_station_ids: set[str] = set()

        self.surface_controls.ok_toggled.connect(self._surface_fetcher.set_ok_enabled)
        self.surface_controls.wtm_toggled.connect(self._surface_fetcher.set_wtm_enabled)
        self.surface_controls.plots_toggled.connect(self._surface_layer.set_visible)

        self._surface_fetcher.observations_updated.connect(self._on_surface_observations_updated)
        self._surface_fetcher.status_updated.connect(self.surface_controls.set_status)
        self._surface_fetcher.status_updated.connect(self.status_msg_label.setText)
        self._surface_fetcher.error.connect(self._on_surface_error)

        self._surface_fetcher.start()
        QTimer.singleShot(1200, lambda: self._surface_layer.set_visible(
            self.surface_controls.plots_visible()
        ))

    def _on_surface_observations_updated(self, items: list[dict]):
        incoming: set[str] = set()
        for item in items:
            obs = item["obs"]
            station_id = item["id"]
            incoming.add(station_id)
            self._surface_layer.update(
                station_id, obs.lat, obs.lon, obs, name=item.get("name", station_id)
            )

        for station_id in self._surface_station_ids - incoming:
            self._surface_layer.remove(station_id)
        self._surface_station_ids = incoming

    def _on_surface_error(self, msg: str):
        self.status_msg_label.setText(f"Surface: {msg}")
        self._layout_overlays()

    def _on_satellite_toggled(self, checked: bool):
        self._set_layer_active("satellite", checked)
        if self._archive:
            # Archive mode: simply show/hide the overlay the archive fetcher is updating.
            has_data = getattr(self, "_archive_sat_has_data", False)
            self.map_widget.set_satellite_visible(checked and has_data)
            return
        if not checked:
            # Closing the drawer stops playback but leaves the overlay visible.
            self._satellite_loop_timer.stop()
            self.satellite_controls.stop_loop()
        else:
            mode = self.satellite_controls.current_mode()
            if not mode:
                return
            self.map_widget.set_satellite_mode(mode)
            frames = self._satellite_cache.get(mode, [])
            if frames:
                self._render_satellite_frame(frames[-1])
                self.map_widget.set_satellite_visible(True)

    def _on_satellite_mode_changed(self, mode: str):
        if self._archive:
            self.satellite_controls.stop_loop()
            self.satellite_controls.reset_cache_ui()
            self._archive_sat_has_data = False

            if not mode:
                self.map_widget.clear_satellite_frame()
                self.map_widget.set_satellite_visible(False)
                return

            self.map_widget.set_satellite_mode(mode)
            self.map_widget.clear_satellite_frame()
            self.map_widget.set_satellite_visible(False)
            self._archive_satellite.set_mode(mode)
            self.status_msg_label.setText(f"Fetching GOES {mode.upper()}…")
            self._layout_overlays()
            return

        self._satellite_loop_timer.stop()
        self.satellite_controls.stop_loop()
        self.satellite_controls.reset_cache_ui()

        if not mode:
            self.map_widget.set_satellite_visible(False)
            return

        self.map_widget.set_satellite_mode(mode)
        frames = self._satellite_cache.get(mode, [])
        if frames:
            self.satellite_controls.set_cache_size(len(frames))
            self._render_satellite_frame(frames[-1])
            self.satellite_controls.set_scan_time(frames[-1].time_str)
            self.map_widget.set_satellite_visible(True)
            self.status_msg_label.setText(f"GOES {mode.upper()} {frames[-1].time_str}")
            self._layout_overlays()
        else:
            # Clear the previous mode's frame so CONUS doesn't linger
            # while waiting on the first MESO frame.
            self.map_widget.clear_satellite_frame()
            self.map_widget.set_satellite_visible(False)
            # Backfill recent frames on first select so loop playback works immediately.
            self._satellite_fetcher.fetch_history(mode, 10)
            self.status_msg_label.setText(f"Fetching GOES {mode.upper()}…")
            self._layout_overlays()

    def _on_satellite_frames_updated(self, mode: str, frames: list):
        self._satellite_cache[mode] = frames
        active_mode = self.satellite_controls.current_mode()
        if mode != active_mode:
            return
        was_live = self.satellite_controls.is_at_latest_frame()
        self.satellite_controls.set_cache_size(len(frames))
        if was_live:
            self._render_satellite_frame(frames[-1])
            self.satellite_controls.set_scan_time(frames[-1].time_str)
            self.status_msg_label.setText(f"GOES {mode.upper()} {frames[-1].time_str}")
            self._layout_overlays()
            if not self.satellite_controls.is_looping():
                self.map_widget.set_satellite_visible(True)

    def _on_satellite_frame_requested(self, idx: int):
        mode   = self.satellite_controls.current_mode()
        frames = self._satellite_cache.get(mode, [])
        if not frames or idx >= len(frames):
            return
        frame = frames[idx]
        self._render_satellite_frame(frame)
        self.satellite_controls.set_scan_time(frame.time_str)

    def _on_satellite_speed_changed(self, ms: int):
        self._satellite_loop_timer.setInterval(ms)

    def _on_satellite_loop_toggled(self, looping: bool):
        if looping:
            self._satellite_loop_timer.start()
        else:
            self._satellite_loop_timer.stop()

    def _satellite_loop_tick(self):
        mode   = self.satellite_controls.current_mode()
        frames = self._satellite_cache.get(mode, [])
        if not frames:
            return
        current = self.satellite_controls.current_frame()
        nxt     = (current + 1) % len(frames)
        self.satellite_controls.set_frame(nxt)
        self._render_satellite_frame(frames[nxt])
        self.satellite_controls.set_scan_time(frames[nxt].time_str)

    def _render_satellite_frame(self, frame):
        if frame.b64:
            w, s, e, n = frame.bbox
            self.map_widget.set_satellite_frame(frame.b64, w, s, e, n)
        else:
            self.map_widget.set_satellite_time(frame.time_iso)

    def _on_meso_sectors_updated(self, sectors: dict):
        for idx in (1, 2):
            bbox = sectors.get(idx)
            # In archive mode buttons stay enabled even before bbox is known;
            # the bbox is populated lazily on first frame fetch.
            available = bbox is not None or self._archive
            self.satellite_controls.set_meso_available(idx, available, bbox)
        self.map_widget.set_meso_sectors(sectors)

    def _on_meso_preview(self, idx: int, active: bool):
        self.map_widget.preview_meso_sector(idx if active else None)

    def _auto_start_radar(self):
        self._radar_fetcher.start()
        self._radar_fetcher.fetch_now()

    def _on_radar_error(self, msg: str):
        self.status_msg_label.setText(f"Radar: {msg}")
        self._layout_overlays()
        self._radar_error_clear_timer.start(10_000)

    def _clear_radar_error(self):
        if self.status_msg_label.text().startswith("Radar:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _clear_hazard_error(self):
        if self.status_msg_label.text().startswith("Hazards:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_satellite_error(self, msg: str):
        self.status_msg_label.setText(f"Satellite: {msg}")
        self._layout_overlays()
        self._satellite_error_clear_timer.start(10_000)

    def _clear_satellite_error(self):
        if self.status_msg_label.text().startswith("Satellite:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_radar_toggled(self, enabled: bool):
        self._set_layer_active("radar", enabled)
        if enabled:
            self._radar_fetcher.start()
            self._radar_fetcher.fetch_now()
        else:
            # stop everything and clear all state when disabled
            self._set_radar_station_picker_visible(False)
            self._loop_timer.stop()
            self.radar_controls.reset_cache_ui()
            self._scan_cache.clear()
            self._pending_render_scan = None
            self._radar_fetcher.reset_history()   # force full backfill on re-enable
            self._radar_fetcher.stop()
            self._radar_overlay.clear()
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_hazard_error(self, msg: str):
        self.status_msg_label.setText(f"Hazards: {msg}")
        self._layout_overlays()
        self._hazard_error_clear_timer.start(10_000)

    def _on_hazard_connectivity(self, online: bool):
        self.hazard_indicator.setVisible(not online)
        self._layout_overlays()

    def _on_spc_received(self, cat_str: str, wind_str: str, hail_str: str, tor_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_spc_geojson(cat_str, wind_str, hail_str, tor_str)

    def _on_nws_received(self, warnings_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_nws_warnings_geojson(warnings_str)
        try:
            fc = json.loads(warnings_str)
            self._nws_active_phenoms = {
                str(f.get("properties", {}).get("phenom", "")).upper()
                for f in fc.get("features", [])
                if f.get("properties", {}).get("phenom")
            }
        except (json.JSONDecodeError, AttributeError):
            self._nws_active_phenoms = set()
        self._update_hazard_legend()

    def _on_spc_watches_received(self, watches_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_spc_watches_geojson(watches_str)

    def _on_spc_mds_received(self, mds_str: str):
        self._clear_hazard_fetch_msg()
        self.map_widget.set_spc_mds_geojson(mds_str)

    def _clear_hazard_fetch_msg(self):
        txt = self.status_msg_label.text()
        if txt.startswith("Fetching ") or txt.startswith("Refreshing "):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_hazard_fetch_requested(self):
        self.status_msg_label.setText("Refreshing hazards…")
        self._layout_overlays()
        self._hazard_fetcher.fetch_now()

    def _on_spc_mds_toggled(self, enabled: bool):
        self._set_layer_active("spc_mds", enabled)
        self._hazard_fetcher.set_spc_mds_enabled(enabled)
        self.map_widget.set_spc_mds_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_mds_fresh():
                self._hazard_fetcher.emit_cached_mds()
            else:
                self.status_msg_label.setText("Fetching SPC MDs…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _update_hazard_legend(self):
        """Recompute which hazard layers are active and update the pill legend."""
        if not hasattr(self, "_hazard_fetcher"):
            # Archive mode: derive active layers from hazard_controls button states.
            hc = self.hazard_controls
            active = []
            if hc._btn_outlook.isChecked():
                active.append("spc-cat")
            for k in ("tor", "wind", "hail"):
                btn = getattr(hc, f"_btn_{k}")
                if btn.isChecked():
                    active.append(f"spc-{k}")
            if hc._btn_watches.isChecked():
                active.append("spc-watches")
            if hc._btn_nws_warnings.isChecked():
                active.append("nws-warnings")
            hc.update_legend(active, nws_phenoms=self._nws_active_phenoms)
            self._start_layout_pulse()
            return
        fc = self._hazard_fetcher
        active = []
        if any(fc._spc_categories.values()):
            active.append("spc-cat")
        for k in ("tor", "wind", "hail"):
            if fc._spc_products.get(k):
                active.append(f"spc-{k}")
        if fc._spc_watches_enabled:
            active.append("spc-watches")
        if fc._spc_mds_enabled:
            active.append("spc-mds")
        if fc._nws_enabled:
            active.append("nws-warnings")
        self.hazard_controls.update_legend(active, nws_phenoms=self._nws_active_phenoms)
        # Drive _layout_overlays during the legend resize animation so the
        # floating overlay geometry tracks the new sizeHint().
        self._start_layout_pulse()

    def _on_spc_feature_clicked(self, payload: str):
        """Handle a click on one or more overlapping hazard polygons."""
        import re as _re
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return

        # JS sends a list of all unique features under the click point.
        items = raw if isinstance(raw, list) else [raw]

        fetch_targets: list[tuple[str, str, str | None]] = []  # (title, kind, identifier)
        for data in items:
            source = data.get("source", "")
            props = data.get("properties", {})

            # For archive mode, encode the current archive time into identifiers
            # that use time-windowed IEM text fetches (swo, watch).
            _archive_ts = (
                self._time_ctrl.current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self._archive and hasattr(self, "_time_ctrl") else None
            )

            if source == "spc-cat":
                fetch_targets.append(("DAY 1 CONVECTIVE OUTLOOK", "swo", _archive_ts))
            elif source == "spc-mds":
                name = str(props.get("name", "")).strip()
                _m = _re.search(r'\d+', name)
                num = _m.group().zfill(4) if _m else "0000"
                fetch_targets.append((f"MESOSCALE DISCUSSION {num}", "mcd", num))
            elif source == "spc-watches":
                watch_num = str(props.get("watch_num", "")).strip()
                if not watch_num:
                    continue
                event_label = str(props.get("event", "Watch")).upper()
                # Append archive timestamp so text fetch targets the correct date.
                ident = f"{watch_num}|{_archive_ts}" if _archive_ts else watch_num
                fetch_targets.append((f"{event_label} {watch_num}", "watch", ident))
            elif source == "nws-warnings":
                warning_url = str(props.get("warning_url", "")).strip()
                if not warning_url:
                    continue
                prod_type = str(props.get("prod_type", "Warning")).title()
                wfo = str(props.get("wfo", "")).strip()
                title = f"{prod_type} — {wfo}" if wfo else prod_type
                fetch_targets.append((title, "warning", warning_url))

        if not fetch_targets:
            return

        self._fetch_generation += 1
        gen = self._fetch_generation
        self.outlook_panel.show_loading([t[0] for t in fetch_targets])
        self._layout_overlays()
        for title, kind, identifier in fetch_targets:
            threading.Thread(
                target=self._fetch_outlook_text,
                args=(gen, title, kind, identifier),
                daemon=True,
            ).start()

    def _fetch_outlook_text(self, generation: int, title: str, kind: str, identifier: str | None):
        """Fetch SPC discussion text in a background thread.

        Sources:
          Day 1 Outlook: IEM Mesonet AFOS API  (PIL: SWODY1)
          MDs:           SPC direct .txt URL    (https://www.spc.noaa.gov/products/md/md{nnnn}.txt)

        IEM rejects the SPCMCD{nnnn} PIL as too long, so MDs are fetched
        directly from SPC's own text product archive instead.
        """
        from urllib.request import Request, urlopen

        HEADERS = {
            "User-Agent": "STORM/1.0 (contact: support)",
            "Accept": "application/geo+json, application/ld+json, application/json, text/plain",
        }

        def _fetch(url: str) -> str:
            import time as _time
            last_exc = None
            for attempt in range(3):
                if attempt:
                    _time.sleep(0.8)
                try:
                    req = Request(url, headers=HEADERS)
                    with urlopen(req, timeout=15) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                    return raw.strip("\x01\x02\x03\r\n").strip()
                except Exception as _e:
                    last_exc = _e
            raise last_exc

        try:
            if kind == "swo":
                # identifier is an ISO archive timestamp in archive mode, None in live mode.
                if identifier:
                    from datetime import datetime as _dt2, timedelta as _td2
                    ts = _dt2.strptime(identifier, "%Y-%m-%dT%H:%M:%SZ")
                    # IEM AFOS ignores after/before in ISO format; sdate/edate (date-only)
                    # correctly filter to the session's UTC date.
                    sdate = ts.strftime("%Y-%m-%d")
                    edate = (ts + _td2(days=1)).strftime("%Y-%m-%d")
                    url = (
                        "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
                        f"?pil=SWODY1&limit=1&fmt=text&sdate={sdate}&edate={edate}"
                    )
                else:
                    url = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SWODY1&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "mcd":
                url = f"https://www.spc.noaa.gov/products/md/md{identifier}.txt"
                text = _fetch(url)
            elif kind == "watch":
                # identifier is "NNNN" in live mode or "NNNN|ISO_TS" in archive mode.
                watch_num = identifier
                archive_ts = None
                if identifier and "|" in identifier:
                    watch_num, archive_ts = identifier.split("|", 1)
                sel_digit = str(int(watch_num) % 10)
                if archive_ts:
                    from datetime import datetime as _dt2, timedelta as _td2
                    ts = _dt2.strptime(archive_ts, "%Y-%m-%dT%H:%M:%SZ")
                    sdate = (ts - _td2(days=1)).strftime("%Y-%m-%d")
                    edate = (ts + _td2(days=1)).strftime("%Y-%m-%d")
                    url = (
                        "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
                        f"?pil=SEL{sel_digit}&limit=1&fmt=text&sdate={sdate}&edate={edate}"
                    )
                else:
                    url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SEL{sel_digit}&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "warning":
                if not identifier:
                    text = "(No warning URL available)"
                elif self._archive:
                    # Archive mode: identifier is the IEM VTEC viewer URL with query params:
                    # ?year=YYYY&wfo=KWFO&phenomena=SV&significance=W&eventid=0003
                    from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
                    import json as _json
                    _p = _urlparse(identifier)
                    _q = _parse_qs(_p.query)
                    year     = (_q.get("year",         [None])[0])
                    wfo      = (_q.get("wfo",           [None])[0])
                    phenom   = (_q.get("phenomena",     [None])[0])
                    sig      = (_q.get("significance",  [None])[0])
                    etn_raw  = (_q.get("eventid",       [None])[0])
                    if year and wfo and phenom and sig and etn_raw:
                        etn_int = int(etn_raw.lstrip("0") or "0")
                        api_url = (
                            "https://mesonet.agron.iastate.edu/json/vtec_event.py"
                            f"?wfo={wfo}&year={year}&phenomena={phenom}"
                            f"&significance={sig}&etn={etn_int}"
                        )
                        raw = _fetch(api_url)
                        data = _json.loads(raw)
                        if data.get("event_exists"):
                            text = (data.get("report") or {}).get("text", "") or "(No text in archive)"
                        else:
                            text = "(Warning event not found in IEM archive)"
                    else:
                        text = "(Could not parse VTEC parameters from warning URL)"
                else:
                    import json as _json
                    raw = _fetch(identifier)
                    try:
                        raw_json = _json.loads(raw)
                        wp = raw_json.get("properties", {})
                        headline = wp.get("headline", "")
                        description = wp.get("description", "")
                        instruction = wp.get("instruction", "")
                        text = "\n\n".join(x for x in [headline, description, instruction] if x)
                    except _json.JSONDecodeError:
                        text = "(Warning text unavailable)"
            else:
                text = ""
            if not text:
                text = "(No discussion text found)"
        except Exception as exc:
            text = f"Failed to load discussion:\n{exc}"

        self._panel_text_ready.emit(generation, title, text)

    def _on_panel_text_ready(self, generation: int, title: str, text: str):
        if generation == self._fetch_generation:
            self.outlook_panel.show_text(title, text)

    def _on_spc_mode_changed(self, mode: str):
        self._set_layer_active("spc_outlook", mode == "outlook")
        self._set_layer_active("spc_tor",     mode == "tor")
        self._set_layer_active("spc_wind",    mode == "wind")
        self._set_layer_active("spc_hail",    mode == "hail")
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self._hazard_fetcher.set_spc_category_enabled(key, outlook_on)
            self.map_widget.set_spc_category_visible(key, outlook_on)

        for key in ("tor", "wind", "hail"):
            on = mode == key
            self._hazard_fetcher.set_spc_product_enabled(key, on)
            self.map_widget.set_spc_product_visible(key, on)

        if mode:
            _spc_labels = {
                "outlook": "SPC outlook", "tor": "SPC tornado",
                "wind": "SPC wind", "hail": "SPC hail",
            }
            _fetch_label = f"Fetching {_spc_labels.get(mode, 'SPC')}…"
            needs_refresh = False
            if mode == "outlook":
                needs_refresh = not self._hazard_fetcher.spc_category_cached()
            elif mode in ("tor", "wind", "hail"):
                needs_refresh = not self._hazard_fetcher.spc_product_cached(mode)
            if needs_refresh:
                self._hazard_fetcher.force_spc_refresh()
                self.status_msg_label.setText(_fetch_label)
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
            elif self._hazard_fetcher.is_spc_fresh():
                self._hazard_fetcher.emit_cached_spc()
            else:
                self.status_msg_label.setText(_fetch_label)
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_spc_watches_toggled(self, enabled: bool):
        self._set_layer_active("spc_watches", enabled)
        self._hazard_fetcher.set_spc_watches_enabled(enabled)
        self.map_widget.set_spc_watches_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_watches_fresh():
                self._hazard_fetcher.emit_cached_watches()
            else:
                self.status_msg_label.setText("Fetching SPC watches…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_nws_warnings_toggled(self, enabled: bool):
        self._set_layer_active("nws_warnings", enabled)
        self._hazard_fetcher.set_nws_enabled(enabled)
        self.map_widget.set_nws_warnings_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_nws_fresh():
                self._hazard_fetcher.emit_cached_nws()
            else:
                self.status_msg_label.setText("Fetching NWS warnings…")
                self._layout_overlays()
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_radar_site_changed(self, site: str):
        # increment generation so any in-flight decodes/renders for old site are discarded
        self._render_generation += 1
        self._pending_render_scan = None
        self._render_in_flight = False   # previous render's result will be discarded by gen check
        # clear cache when site changes — old data belongs to a different location
        self._radar_fetcher.set_site(site)
        self._loop_timer.stop()
        self.radar_controls.reset_cache_ui()
        self._scan_cache.clear()
        if getattr(self, '_site_change_from_map_click', False):
            # JS click handler already set raster-opacity to 0 — just sync
            # Python state.  Sending JS here would freeze the renderer (the
            # Chromium process is still processing the click event).
            self._radar_overlay._hidden = True
            self._radar_overlay._current_scan = None
        else:
            # Non-click path (auto-site, etc.) — safe to send JS
            self._radar_overlay.hide()
        # Reset inject throttle so the first frame for the new site appears immediately
        self._last_inject_time = 0.0
        self._deferred_inject_result = None
        self._inject_throttle_timer.stop()

    def _on_sounding_mode_toggled(self, active: bool):
        if not active:
            # Deactivate both sub-modes and clear station layer
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            if hasattr(self, "sounding_controls"):
                self.sounding_controls.reset_to_hrrr()
            return
        # Activate the currently selected sub-mode
        mode = self.sounding_controls.active_mode if hasattr(self, "sounding_controls") else "hrrr"
        self._on_sounding_mode_changed(mode)
        # Untoggle other exclusive map-click modes
        if self.btn_measure.isChecked():
            self.btn_measure.setChecked(False)
        if hasattr(self, "btn_annotate") and self.btn_annotate.isChecked():
            self.btn_annotate.setChecked(False)
        # Cancel active annotation sub-tool even if annotate drawer was already closed
        if getattr(self, "_active_annotation_type", "") or getattr(self, "_active_drawing_type", ""):
            self._on_annotation_tool_selected("")
        # Cancel route pick mode
        if hasattr(self, "map_widget"):
            self.map_widget.set_route_pick_mode(False)

    def _on_sounding_mode_changed(self, mode: str):
        """Called when the user switches between HRRR, OBS, and NSSL in the sub-bar."""
        if not self.btn_sounding.isChecked():
            return
        if self._archive:
            # Archive mode: HRRR → model sounding, OBS → radiosonde, NSSL not supported.
            if mode == "hrrr":
                self.map_widget.set_obs_sounding_mode(False)
                self.map_widget.clear_sounding_stations()
                self.map_widget.set_sounding_mode(True)
            elif mode == "obs":
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_sounding_stations(self._sounding_stations_geojson)
                self.map_widget.set_obs_sounding_mode(True)
            else:  # nssl
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_obs_sounding_mode(False)
                self.map_widget.clear_sounding_stations()
                self.status_msg_label.setText("Fetching NSSL soundings…")
                self._archive_sounding.fetch_nssl_sounding()
            return
        if not hasattr(self, "_sounding_fetcher"):
            return
        if mode == "hrrr":
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            self.map_widget.set_sounding_mode(True)
        elif mode == "obs":
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_sounding_stations(self._sounding_stations_geojson)
            self.map_widget.set_obs_sounding_mode(True)
        else:  # nssl
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            self.status_msg_label.setText("Fetching NSSL soundings…")
            self._clamps_sounding_fetcher.fetch()

    def _on_sounding_map_click(self, lat: float, lon: float):
        self.status_msg_label.setText("Fetching HRRR sounding…")
        self._sounding_fetcher.fetch(lat, lon)

    def _on_obs_station_click(self, station_id: str, name: str, lat: float, lon: float, elev: float):
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._obs_sounding_fetcher.fetch(station_id, name, lat, lon, elev)

    def _on_sounding_ready(self, sset):
        self.status_msg_label.setText("")
        self._sounding_dialog.load(sset)

    def _on_sounding_error(self, msg: str):
        self.status_msg_label.setText(f"Sounding error: {msg}")

    def _toggle_radar_station_picker(self):
        self._set_radar_station_picker_visible(not self._radar_station_picker_visible)

    def _set_radar_station_picker_visible(self, visible: bool):
        self._radar_station_picker_visible = visible
        self.map_widget.set_radar_stations_visible(visible)

    def _on_radar_station_clicked(self, site: str):
        # JS already called stormSetRadarStationsVisible(false) and set
        # radar-overlay opacity to 0 before the bridge call — just update
        # Python-side flags without any redundant runJavaScript calls.
        self._radar_station_picker_visible = False
        if self._archive:
            # In archive mode, start fetching from the selected station.
            self._archive_session.radar_station = site
            self._start_archive_radar(site)
            return
        self._site_change_from_map_click = True
        self._select_radar_site(site, user_selected=True)
        self._site_change_from_map_click = False

    def _select_radar_site(self, site: str, user_selected: bool):
        if user_selected:
            self._radar_auto_site_pending = False
        if site == self.radar_controls.current_site():
            self.radar_controls.set_selected_site(site, emit=False)
            return
        self.radar_controls.set_selected_site(site, emit=True)

    def _on_radar_product_changed(self, product: str):
        # both products are always cached — just switch what's displayed
        self._loop_timer.stop()
        key = f"{self.radar_controls.current_site()}/{product}"
        cache = self._scan_cache.get(key, [])
        self.radar_controls.reset_cache_ui()
        if cache:
            self.radar_controls.set_cache_size(len(cache))
            self._show_scan(cache[-1])
        else:
            self._radar_overlay.clear()

    def _on_radar_data(self, site: str, product: str, raw_bytes: bytes):
        """Called on the main thread by the fetcher signal.  Returns immediately —
        the actual decode is submitted to a background thread so the UI is never
        blocked by MetPy/numpy work."""
        log.debug("radar data received: %s/%s (%d bytes)", site, product, len(raw_bytes))
        gen = self._render_generation
        self._decode_executor.submit(self._bg_decode, gen, site, product, raw_bytes)

    def _bg_decode(self, gen: int, site: str, product: str, raw_bytes: bytes):
        """Runs in the decode thread pool — NOT on the main thread.
        Decodes raw NEXRAD bytes and emits _scan_decoded (auto-queued to main thread)."""
        # bail early if the site was changed while we were queued
        if gen != self._render_generation:
            log.debug("bg_decode: discarding stale decode gen=%d (current=%d)", gen, self._render_generation)
            return
        scan = decode_nexrad_l3(site, product, raw_bytes)
        if scan is None:
            self._radar_decode_failed.emit(site, product)
            return
        # check again after decode in case site changed mid-decode
        if gen != self._render_generation:
            log.debug("bg_decode: discarding post-decode stale result gen=%d", gen)
            return
        self._scan_decoded.emit(gen, site, product, scan)

    def _on_scan_decoded(self, gen: int, site: str, product: str, scan):
        """Runs on the main thread (PyQt queues the signal from the decode thread).
        Updates the scan cache and submits a background render for the latest frame."""
        if gen != self._render_generation:
            return

        key = f"{site}/{product}"
        cache = self._scan_cache.setdefault(key, [])

        # skip duplicate scan times — THREDDS sometimes returns the same file twice
        if cache and cache[-1].scan_time == scan.scan_time:
            return

        cache.append(scan)

        # trim to 35-minute rolling window, hard cap at 6 scans per product (12 total)
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
        while cache and cache[0].scan_time < cutoff:
            cache.pop(0)
        while len(cache) > 6:
            cache.pop(0)

        log.debug("cache updated: key=%s, n=%d frames", key, len(cache))

        # only update display for the currently visible product;
        # background product data is still cached above for instant switching
        if product != self.radar_controls.current_product():
            return

        was_live = self.radar_controls.is_at_latest_frame()
        self.radar_controls.set_cache_size(len(cache))

        # Update the pending scan and submit a render immediately if none is in
        # flight.  The _render_in_flight guard ensures at most one background
        # render runs at a time; if one is already running, the latest scan is
        # kept as pending and submitted the moment the current render completes.
        if not self.radar_controls.is_looping() and was_live:
            self._pending_render_scan = scan
            self._submit_pending_render()

    def _submit_pending_render(self):
        """Submit a background render for the latest pending scan.
        No-op if a render is already in flight — the pending scan will be
        submitted by _on_render_ready when the current render completes."""
        scan = self._pending_render_scan
        if scan is None:
            return
        if self._render_in_flight:
            # Don't queue a second render — _on_render_ready will re-check pending
            return
        self._pending_render_scan = None
        self._render_in_flight = True
        gen = self._render_generation
        grid_size = self._radar_overlay._grid_size
        self._render_executor.submit(self._bg_render, gen, scan, grid_size)

    def _bg_render(self, gen: int, scan, grid_size: int):
        """Runs in the render thread pool — NOT on the main thread.
        Renders scan to PNG then emits _render_ready (auto-queued to main thread)."""
        if gen != self._render_generation:
            log.debug("bg_render: discarding stale render gen=%d (current=%d)", gen, self._render_generation)
            return
        try:
            png_bytes, bounds, elapsed_ms = self._render_scan_to_png(scan, grid_size)
        except Exception as e:
            log.error("bg_render: render failed: %s", e, exc_info=True)
            return
        if gen != self._render_generation:
            log.debug("bg_render: discarding post-render stale result gen=%d", gen)
            return
        self._render_ready.emit({
            "gen":        gen,
            "scan":       scan,
            "png_bytes":  png_bytes,
            "bounds":     bounds,
            "elapsed_ms": elapsed_ms,
        })

    # Minimum interval between inject calls (ms).  MapLibre's renderer runs in
    # Chromium's renderer process — each updateImage triggers image fetch + decode
    # + GPU re-render, all on the renderer's main thread where mouse/keyboard
    # events are also processed.  Without throttling, 6 rapid injects during
    # backfill saturate the renderer and freeze the map for 1-2 seconds.
    _INJECT_COOLDOWN_MS = 600

    def _on_render_ready(self, result: dict):
        """Runs on the main thread — injects the pre-rendered PNG into the map."""
        import time as _time
        self._render_in_flight = False
        if result["gen"] != self._render_generation:
            if self._pending_render_scan is not None:
                self._submit_pending_render()
            return
        scan = result["scan"]
        if scan.product != self.radar_controls.current_product():
            if self._pending_render_scan is not None:
                self._submit_pending_render()
            return

        now = _time.monotonic()
        elapsed_since_inject = (now - self._last_inject_time) * 1000

        if elapsed_since_inject >= self._INJECT_COOLDOWN_MS:
            # Enough time has passed — inject immediately
            self._do_inject(result)
        else:
            # Too soon — defer this result; timer will flush it
            self._deferred_inject_result = result
            if not self._inject_throttle_timer.isActive():
                remaining = self._INJECT_COOLDOWN_MS - int(elapsed_since_inject)
                self._inject_throttle_timer.start(max(20, remaining))

        # Keep the render pipeline moving regardless of inject throttle
        if self._pending_render_scan is not None:
            self._submit_pending_render()

    def _flush_deferred_inject(self):
        """Timer callback — inject the most recent deferred render result."""
        result = self._deferred_inject_result
        self._deferred_inject_result = None
        if result is None:
            return
        if result["gen"] != self._render_generation:
            return
        self._do_inject(result)

    def _do_inject(self, result: dict):
        """Actually inject the rendered PNG into the map."""
        import time as _time
        scan = result["scan"]
        self._radar_overlay.inject(result["png_bytes"], result["bounds"])
        self._last_inject_time = _time.monotonic()
        self._radar_overlay._maybe_adjust_grid(result["elapsed_ms"])
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"))
        self._radar_error_clear_timer.stop()
        self.status_msg_label.setText(scan.label)
        self._layout_overlays()

    def _show_scan(self, scan):
        self._radar_overlay.update(scan)
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"))
        self._radar_error_clear_timer.stop()
        self.status_msg_label.setText(scan.label)
        self._layout_overlays()

    def _display_cached_frame(self, idx: int):
        log.debug("displaying cached frame %d of %d",
                  idx, len(self._scan_cache.get(
                      f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}", []
                  )))
        key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
        cache = self._scan_cache.get(key, [])
        if 0 <= idx < len(cache):
            self._show_scan(cache[idx])

    def _on_radar_speed_changed(self, ms: int):
        self._loop_timer.setInterval(ms)

    def _on_loop_toggled(self, looping: bool):
        if looping:
            self._loop_timer.start()
        else:
            self._loop_timer.stop()
            # snap back to the latest (live) frame when loop stops
            key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
            cache = self._scan_cache.get(key, [])
            if cache:
                self.radar_controls.set_frame(len(cache) - 1)
                self._show_scan(cache[-1])

    def _advance_loop_frame(self):
        key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
        cache = self._scan_cache.get(key, [])
        if not cache:
            return
        # wrap around so loop plays continuously
        next_frame = (self.radar_controls.current_frame() + 1) % len(cache)
        self.radar_controls.set_frame(next_frame)
        self._show_scan(cache[next_frame])

    # ── MQTT ──────────────────────────────────────────────────────────────────

    def _init_mqtt(self):
        self._mqtt_client = MQTTClient(client_id=config.VEHICLE_ID, parent=self)
        self._mqtt_client.connected.connect(self._on_mqtt_connected)
        self._mqtt_client.disconnected.connect(self._on_mqtt_disconnected)

        # local vehicles publish to the broker; remote vehicles subscribe back in
        self._vehicle_sync = VehicleSync(self._mqtt_client, parent=self)
        self._vehicle_sync.vehicle_received.connect(self._on_remote_vehicle_obs)
        self._storm_cone_sync = StormConeSync(self._mqtt_client, read_only=self._viewer, parent=self)

        # connect after a short delay so the window is fully painted first
        if config.MQTT_HOST:
            QTimer.singleShot(500, self._mqtt_connect)
        else:
            log.info("MQTT host not configured — running offline")

    def _on_remote_vehicle_obs(self, obs):
        # If this machine is producing local data (not in monitor mode),
        # prefer the local stream for its own vehicle ID.
        if not self._monitor and obs.vehicle_id == config.VEHICLE_ID:
            return
        self.update_vehicle_obs(obs)

    def _on_local_vehicle_obs(self, obs: Observation):
        if self._startup_local_pending and obs.vehicle_id == config.VEHICLE_ID:
            self._complete_local_startup_phase()
        # Track GPS fix age for the local vehicle status indicator
        if obs.vehicle_id == config.VEHICLE_ID and obs.lat is not None:
            self._last_local_obs_ts = time.monotonic()
        # Always update local GUI at the full poll rate (1 Hz)
        self.update_vehicle_obs(obs)
        # Throttle MQTT publishes independently — other vehicles don't need 1 Hz
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

    # ── Annotations ───────────────────────────────────────────────────────────

    def _init_annotations(self):
        self._annotations: dict[str, Annotation] = {}
        self._active_annotation_type: str = ""
        self._annotation_sync = AnnotationSync(self._mqtt_client, read_only=self._viewer, parent=self)

        # mutual exclusion: opening one drawer closes the other
        self.btn_radar.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_hazards.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_radar.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_hazards.setChecked(False) if on else None
        )

        # tool selection → set cursor mode
        self.annotation_tools.tool_selected.connect(self._on_annotation_tool_selected)

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
        """Show an accent-colored status prompt."""
        suffix = "  —  click map to place" if needs_click else ""
        self.status_msg_label.setText(f"  ▶  {msg}{suffix}")
        self.status_msg_label.setStyleSheet(
            f"color: {ACCENT}; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        self._layout_overlays()

    def _clear_placement_prompt(self):
        self.status_msg_label.setText("")
        self.status_msg_label.setStyleSheet("")
        self._layout_overlays()

    def _on_annotation_tool_selected(self, type_key: str):
        # Selecting any tool deactivates sounding mode
        if type_key and hasattr(self, "btn_sounding") and self.btn_sounding.isChecked():
            self.btn_sounding.setChecked(False)
        # cancel any in-progress drawing when tool switches
        if getattr(self, "_active_drawing_type", ""):
            self._cancel_drawing()

        self._pending_cone_params = None
        self._active_annotation_type = ""
        self._active_drawing_type = ""
        self.map_widget.set_storm_cone_placement_mode(False)

        if type_key in DRAWING_TYPE_MAP:
            # Drawing tool (front or custom shape)
            self._active_drawing_type = type_key
            self.map_widget.set_annotation_mode(False)
            self.map_widget.set_drawing_mode(True, type_key)
            meta = DRAWING_TYPE_MAP[type_key]
            self._set_placement_prompt(
                f"{meta['label']} — click to add points, double-click to finish",
                needs_click=False,
            )
        elif type_key == "storm_motion":
            self._active_annotation_type = type_key
            self.map_widget.set_drawing_mode(False)
            dlg = StormConeInputDialog(edit_mode=False, parent=self)
            if dlg.exec() == StormConeInputDialog.DialogCode.Accepted:
                self._pending_cone_params = {
                    "heading": dlg.heading(),
                    "speed_kts": dlg.speed_kts(),
                }
                self.map_widget.set_annotation_mode(False)
                self.map_widget.set_storm_cone_placement_mode(True)
                self._set_placement_prompt("storm cone — click and drag to place", needs_click=False)
            else:
                self._active_annotation_type = ""
                self.annotation_tools.deactivate_tool()
                self._clear_placement_prompt()
        elif type_key:
            self._active_annotation_type = type_key
            self.map_widget.set_drawing_mode(False)
            self.map_widget.set_annotation_mode(True)
            label = ANNOTATION_TYPE_MAP.get(type_key, {}).get("label", "annotation")
            self._set_placement_prompt(label)
        else:
            self.map_widget.set_drawing_mode(False)
            self.map_widget.set_annotation_mode(False)
            self._clear_placement_prompt()

    def _on_map_click(self, lat: float, lon: float):
        if getattr(self, "_measure_active", False):
            self._on_measure_click(lat, lon)
            return
        if getattr(self, "_active_drawing_type", ""):
            self._on_drawing_click(lat, lon)
            return
        if self._active_annotation_type == "fork":
            # remove any existing fork annotations before placing new one
            existing_forks = [aid for aid, a in self._annotations.items() if a.type_key == "fork"]
            for fid in existing_forks:
                self._delete_annotation(fid)
            annotation = Annotation.new(type_key="fork", lat=lat, lon=lon)
            self._active_annotation_type = ""
            self.map_widget.set_annotation_mode(False)
            self.annotation_tools.deactivate_tool()
            self._clear_placement_prompt()
            self._place_annotation(annotation)
        elif self._active_annotation_type:
            dlg = AnnotationPlaceDialog(self._active_annotation_type, lat, lon, viewer_mode=self._viewer, parent=self)
            if dlg.exec() == AnnotationPlaceDialog.DialogCode.Accepted:
                annotation = Annotation.new(
                    type_key=self._active_annotation_type,
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
                self.status_msg_label.setText("  ▶  Drag the annotation to its new location")
                self.status_msg_label.setStyleSheet(
                    f"color: {ACCENT}; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
                )

    def _on_annotation_drag_end(self, annotation_id: str, lat: float, lon: float):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        self.map_widget.set_annotation_draggable(annotation_id, False)
        self._moving_annotation_id = None
        self.status_msg_label.setText("")

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
        log.info("annotation placed: %s at (%.4f, %.4f)", annotation.type_key, annotation.lat, annotation.lon)

    def _delete_annotation(self, annotation_id: str):
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._annotation_sync.publish_delete(annotation_id)
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
        log.info("remote annotation received: %s (%s)", annotation.id, annotation.type_key)

    def _recv_remote_annotation_deleted(self, annotation_id: str, deleted_at: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        log.info("remote annotation deleted: %s at %s", annotation_id, deleted_at or "unknown")

    # ── Drawing Annotations (Fronts & Custom Shapes) ──────────────────────────

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
        if not getattr(self, "_active_drawing_type", ""):
            return
        self._finalize_drawing(lat, lon)

    def _finalize_drawing(self, lat: float, lon: float):
        pts = self._drawing_points[:]
        # Remove trailing duplicate(s) added by the single-click events that
        # fire before the dblclick event.
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

        self._cancel_drawing()   # clear state + preview before showing dialog

        if drawing_type in ("polyline", "polygon"):
            dlg = DrawingTitleDialog(drawing_type, parent=self)
            if dlg.exec() != DrawingTitleDialog.DialogCode.Accepted:
                return
            title = dlg.title()
        else:
            title = DRAWING_TYPE_MAP.get(drawing_type, {}).get("label", drawing_type)

        drawing = DrawingAnnotation.new(
            drawing_type=drawing_type,
            coordinates=pts,
            title=title,
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
        if not getattr(self, "_active_drawing_type", ""):
            return
        self._cancel_drawing()
        if hasattr(self, "annotation_tools"):
            self.annotation_tools.deactivate_tool()

    def _on_drawing_clicked(self, drawing_id: str):
        # Ignore if a tool is currently active
        if self._active_annotation_type or getattr(self, "_active_drawing_type", ""):
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
        log.info("drawing placed: %s at %d points", drawing.drawing_type, len(drawing.coordinates))

    def _delete_drawing(self, drawing_id: str):
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._drawing_sync.publish_delete(drawing_id)
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
        log.info("remote drawing received: %s (%s)", drawing.id, drawing.drawing_type)

    def _recv_remote_drawing_deleted(self, drawing_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        log.info("remote drawing deleted: %s", drawing_id)

    # ── Storm Motion Cone ─────────────────────────────────────────────────────

    def _init_storm_cone(self):
        self._storm_cones: dict[str, StormCone] = {}
        self._pending_cone_params: dict | None = None
        self._moving_cone_id: str | None = None
        self._moving_cone_original_location: tuple[float, float] | None = None

        # cone placed via ANNOTATE drawer — map cone-click → edit dialog
        self.map_widget.storm_cone_clicked.connect(self._on_storm_cone_clicked)
        self.map_widget.storm_cone_drag_ended.connect(self._on_storm_cone_drag_end)
        self.map_widget.storm_cone_place_drag_ended.connect(self._on_storm_cone_place_drag_end)

        # remote cones arriving over MQTT — update map without re-publishing
        self._storm_cone_sync.cone_received.connect(self._recv_remote_storm_cone)
        self._storm_cone_sync.cone_deleted.connect(self._recv_remote_storm_cone_deleted)

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
        self._active_annotation_type = ""
        self.map_widget.set_storm_cone_placement_mode(False)
        self.annotation_tools.deactivate_tool()
        self._clear_placement_prompt()
        self._place_storm_cone(cone)

    def _place_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._storm_cone_sync.publish_create(cone)
        log.info("storm cone placed: id=%s lat=%.4f lon=%.4f hdg=%.0f spd=%.0f",
                 cone.id, cone.lat, cone.lon, cone.heading, cone.speed_kts)

    def _delete_storm_cone(self, cone_id: str):
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._storm_cone_sync.publish_delete(cone_id)
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
        log.info("remote storm cone received: %s", cone.id)

    def _recv_remote_storm_cone_deleted(self, cone_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        log.info("remote storm cone deleted: %s", cone_id)

    # ── Distance Measure ──────────────────────────────────────────────────────

    def _init_measure(self):
        self._measure_active = False
        self._measure_has_anchor = False
        self._measure_complete = False

        # mutual exclusion: MEASURE, ANNOTATE, and SOUNDING all consume map clicks
        # connect exclusion BEFORE _on_measure_toggled so deactivation fires first
        self.btn_measure.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_measure.toggled.connect(
            lambda on: self.btn_sounding.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_measure.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_sounding.setChecked(False) if on else None
        )
        self.btn_measure.toggled.connect(self._on_measure_toggled)

    def _on_measure_toggled(self, active: bool):
        if active:
            self._measure_active = True
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(True)
            self._set_placement_prompt("measure — click first point")
        else:
            # If user exits mid-measure after first point, clear partial artifacts.
            if self._measure_has_anchor or self._measure_complete:
                self.map_widget.clear_measure()
            self._measure_active = False
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(False)
            self._clear_placement_prompt()

    def _on_measure_click(self, lat: float, lon: float):
        if self._measure_complete:
            self._set_placement_prompt("measure complete — toggle off to clear", needs_click=False)
            return

        self.map_widget.measure_click(lat, lon)
        if not self._measure_has_anchor:
            self._measure_has_anchor = True
            self._set_placement_prompt("measure — click second point")
        else:
            # Second point placed — keep tool selected so user can toggle off to clear.
            self._measure_has_anchor = False
            self._measure_complete = True
            self.map_widget.set_measure_mode(False)   # reset cursor while keeping line visible
            self._set_placement_prompt("measure complete — toggle off to clear", needs_click=False)

    # ── Stations ──────────────────────────────────────────────────────────────

    def _init_stations(self):
        self._vehicles: dict[str, Vehicle] = {}
        self._vehicle_history: dict[str, deque] = {}  # vehicle_id → deque[Observation]
        self._vehicle_timeseries_dialogs: dict[str, "VehicleTimeseriesDialog"] = {}  # vehicle_id → dialog
        self._follow_mode = False
        self._station_layer = StationPlotLayer(self.map_widget)
        self._chk_station_plots.toggled.connect(self._station_layer.set_visible)
        self.map_widget.user_dragged.connect(self._on_user_dragged)
        # station plots on by default — delayed until map is ready
        QTimer.singleShot(1200, lambda: self._station_layer.set_visible(
            self._chk_station_plots.isChecked()
        ))

        # ── Re-center button (floating, above MapLibre zoom controls) ─────────
        self.btn_recenter = QToolButton(self._map_container)
        self.btn_recenter.setIcon(_make_loc_icon(18))
        self.btn_recenter.setIconSize(QSize(18, 18))
        self.btn_recenter.setToolTip("Re-center on vehicle")
        self.btn_recenter.setFixedSize(32, 32)
        self.btn_recenter.setStyleSheet("""
            QToolButton {
                background: #1E2433;
                border: 1px solid #2A3045;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: #252D42;
                border-color: #4A9EFF;
            }
            QToolButton:pressed {
                background: #1A1F30;
            }
        """)
        self.btn_recenter.clicked.connect(self._on_recenter_clicked)
        self.btn_recenter.hide()

    # ── Deployment Locations ──────────────────────────────────────────────────

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
        
        # Append to vehicle history if this is NOT the local vehicle and has met data
        if obs.vehicle_id != config.VEHICLE_ID:
            has_met_data = any([
                obs.temperature_c is not None,
                obs.dewpoint_c is not None,
                obs.wind_speed_ms is not None,
                obs.pressure_mb is not None,
            ])
            if has_met_data:
                if obs.vehicle_id not in self._vehicle_history:
                    self._vehicle_history[obs.vehicle_id] = deque()
                self._vehicle_history[obs.vehicle_id].append(obs)
                # Live-update timeseries dialog if open
                if obs.vehicle_id in self._vehicle_timeseries_dialogs:
                    dlg = self._vehicle_timeseries_dialogs[obs.vehicle_id]
                    if dlg.isVisible():
                        dlg.load(obs.vehicle_id, list(self._vehicle_history[obs.vehicle_id]))

        marker_color = self._obs_age_color(obs)
        age_label = self._obs_age_label(obs)
        self._vehicle_age_display_state[obs.vehicle_id] = (marker_color, age_label)
        self.map_widget.add_vehicle(obs.vehicle_id, obs.lat, obs.lon, marker_color, v.icon_type)
        if obs.vehicle_id == config.VEHICLE_ID and hasattr(self, "routing_controls"):
            self.routing_controls.update_own_position(obs.lat, obs.lon)
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

    def _maybe_seed_initial_radar_site(self, obs: Observation) -> None:
        if not self._radar_auto_site_pending or self._monitor:
            return
        if obs.vehicle_id != config.VEHICLE_ID:
            return
        if not hasattr(self, "radar_controls"):
            return
        nearest = self._nearest_radar_site(obs.lat, obs.lon)
        self._radar_auto_site_pending = False
        if nearest != self.radar_controls.current_site():
            self._select_radar_site(nearest, user_selected=False)

    def _should_display_vehicle_obs(self, obs: Observation) -> bool:
        return self._obs_age_minutes(obs) <= 10.0 * 60.0

    def _hide_vehicle(self, vehicle_id: str) -> None:
        removed = self._vehicles.pop(vehicle_id, None)
        self._vehicle_age_display_state.pop(vehicle_id, None)
        if removed is None:
            return

        self.map_widget.remove_vehicle(vehicle_id)
        self._station_layer.remove(vehicle_id)
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
            return "#39D98A"  # fresh
        if age_min <= 3.0:
            return "#FFD166"  # caution
        if age_min <= 5.0:
            return "#FF9F43"  # aging
        return "#E53935"      # stale

    def _obs_age_label(self, obs: Observation) -> str:
        age_min = self._obs_age_minutes(obs)
        if age_min < 1.0:
            return "<1m"
        if age_min < 60.0:
            return f"{age_min:.0f}m"
        hours = age_min / 60.0
        return f"{hours:.1f}h"

    def _refresh_vehicle_panel(self):
        if not hasattr(self, "_vehicle_rows_layout"):
            return
        _clear_layout(self._vehicle_rows_layout)
        if not self._vehicles:
            self._vehicle_rows_widget.setVisible(False)
            return
        self._vehicle_rows_widget.setVisible(True)
        for vid in sorted(self._vehicles.keys()):
            v = self._vehicles[vid]
            self._vehicle_rows_layout.addWidget(self._make_vehicle_row(v))
        self._layout_overlays()

    def _make_vehicle_row(self, v) -> QWidget:
        obs = v.latest_obs
        selected = v.id in self._selected_vehicle_ids

        row = QFrame()
        row.setStyleSheet(
            "QFrame { background-color: rgba(74,158,255,0.08); border-bottom: 1px solid #1E2434; }"
            if selected else
            "QFrame { background: transparent; border-bottom: 1px solid #1E2434; }"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 5, 0, 5)
        rl.setSpacing(6)

        badge_color = self._obs_age_color(obs) if obs else "#6E7A8F"
        badge = QLabel("●")
        badge.setStyleSheet(f"color: {badge_color}; font-size: 12px; background: transparent; border: none;")
        rl.addWidget(badge)

        name_btn = QPushButton(v.id)
        name_btn.setFlat(True)
        name_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #E8EAF0; "
            "font-weight: 600; font-size: 10px; padding: 0; text-align: left; }"
            "QPushButton:hover { color: #4A9EFF; }"
        )
        name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        name_btn.clicked.connect(lambda checked=False, vid=v.id: self._on_vehicle_row_clicked(vid))
        rl.addWidget(name_btn)

        if obs is None:
            no_obs = QLabel("no observations")
            no_obs.setStyleSheet("color: #9DA6B8; font-size: 10px; background: transparent; border: none;")
            rl.addWidget(no_obs)
        else:
            sep = QLabel("·")
            sep.setStyleSheet("color: #6E7A8F; background: transparent; border: none;")
            rl.addWidget(sep)
            age = QLabel(f"{self._obs_age_label(obs)} old")
            age.setStyleSheet("color: #C8D0DE; font-size: 10px; background: transparent; border: none;")
            rl.addWidget(age)

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
        """Open or raise the timeseries dialog for the given vehicle."""
        # Check if dialog already exists and is visible
        if vehicle_id in self._vehicle_timeseries_dialogs:
            dlg = self._vehicle_timeseries_dialogs[vehicle_id]
            if dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return
        
        # Get observation history
        if self._archive:
            # Archive mode: extract from ArchiveMQTTReader
            if not hasattr(self, "_archive_mqtt"):
                return
            observations = self._get_archive_vehicle_history(vehicle_id)
        else:
            # Live mode: use in-memory history
            observations = list(self._vehicle_history.get(vehicle_id, []))
        
        if not observations:
            return
        
        # Create or reuse dialog
        if vehicle_id not in self._vehicle_timeseries_dialogs:
            self._vehicle_timeseries_dialogs[vehicle_id] = VehicleTimeseriesDialog(self)
        
        dlg = self._vehicle_timeseries_dialogs[vehicle_id]
        dlg.load(vehicle_id, observations)
    
    def _get_archive_vehicle_history(self, vehicle_id: str) -> list:
        """Extract vehicle observation history from archive MQTT data."""
        if not hasattr(self, "_archive_mqtt") or not hasattr(self, "_time_ctrl"):
            return []
        
        from network import vehicle_sync
        
        current_time = self._time_ctrl.current_time
        observations = []
        
        # Archive MQTT records are stored as list of (timestamp, dict) tuples
        vehicle_records = self._archive_mqtt._data.get("vehicles", [])
        for ts, record in vehicle_records:
            # Only include records up to current archive time
            if ts > current_time:
                break
            
            if record.get("vehicle_id") != vehicle_id:
                continue
            
            # Convert to Observation
            try:
                obs = vehicle_sync._observation_from_payload(record)
                # Only include if has met data
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
        section.setStyleSheet("QFrame { background: transparent; border-bottom: 1px solid #1E2434; }")
        sl = QVBoxLayout(section)
        sl.setContentsMargins(0, 6, 0, 6)
        sl.setSpacing(4)

        if obs is None:
            top = QHBoxLayout()
            name = QLabel(vid)
            name.setStyleSheet("color: #E8EAF0; font-weight: 600; background: transparent; border: none;")
            top.addWidget(name)
            no_obs = QLabel("no observations")
            no_obs.setStyleSheet("color: #9DA6B8; margin-left: 6px; background: transparent; border: none;")
            top.addWidget(no_obs)
            top.addStretch()
            sl.addLayout(top)
            return section

        badge_color = self._obs_age_color(obs)

        # Top row: badge · name · age · TIMESERIES button · lat/lon
        top = QHBoxLayout()
        top.setSpacing(8)

        badge = QLabel("●")
        badge.setStyleSheet(f"color: {badge_color}; font-size: 12px; background: transparent; border: none;")
        top.addWidget(badge)

        name_lbl = QLabel(vid)
        name_lbl.setStyleSheet("color: #E8EAF0; font-weight: 600; background: transparent; border: none;")
        top.addWidget(name_lbl)

        age_lbl = QLabel(f"{self._obs_age_label(obs)} old")
        age_lbl.setStyleSheet("color: #C8D0DE; background: transparent; border: none;")
        top.addWidget(age_lbl)
        
        # Timeseries button — only shown if vehicle has met data history
        has_history = vid in self._vehicle_history and len(self._vehicle_history[vid]) > 0
        if has_history:
            ts_btn = QPushButton("TIMESERIES")
            ts_btn.setFlat(True)
            ts_btn.setStyleSheet(
                "QPushButton { background: transparent; border: 1px solid #4A9EFF; "
                "color: #4A9EFF; font-size: 8px; font-weight: 600; padding: 2px 6px; "
                "border-radius: 3px; }"
                "QPushButton:hover { background: rgba(74,158,255,0.1); }"
            )
            ts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ts_btn.clicked.connect(lambda checked=False, v=vid: self._on_timeseries_button_clicked(v))
            top.addWidget(ts_btn)

        top.addStretch()

        for key, val in [("lat", f"{obs.lat:.4f}"), ("lon", f"{obs.lon:.4f}")]:
            k_lbl = QLabel(key)
            k_lbl.setStyleSheet("color: #C8D0DE; background: transparent; border: none;")
            top.addWidget(k_lbl)
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet("color: #E8EAF0; background: transparent; border: none;")
            top.addWidget(v_lbl)

        sl.addLayout(top)

        # Timestamp
        ts = obs.timestamp.astimezone(timezone.utc).strftime("%d %b %Y %H%M UTC")
        ts_lbl = QLabel(ts)
        ts_lbl.setStyleSheet("color: #C8D0DE; font-size: 10px; background: transparent; border: none;")
        sl.addWidget(ts_lbl)

        # Obs values grid
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
                    "color: #E8EAF0; background: transparent; border: none;"
                    if is_val else
                    "color: #C8D0DE; background: transparent; border: none;"
                )
                grid.addWidget(cell, row_idx, col)

        sl.addLayout(grid)
        return section

    # ── Data inputs (GPS + file watcher) ──────────────────────────────────────

    def _init_data_inputs(self):
        """Start Track A (file watcher) and/or Track B (GPS) if configured."""
        self._gps_reader: GPSReader | None = None
        self._obs_watcher: ObsFileWatcher | None = None
        self._last_mqtt_publish = 0.0   # monotonic timestamp of last MQTT vehicle publish

        if self._monitor:
            log.info("Monitor mode — no local data inputs started")
            QTimer.singleShot(1500, self._show_monitor_mode_status)
            return

        # Track B — GPS puck auto-detect (used when Track A file watcher is not configured)
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

        # Track A — instrument file watcher (surface obs vehicles)
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

    # ── Config warning ────────────────────────────────────────────────────────

    def _show_monitor_mode_status(self):
        self.status_msg_label.setText(
            "  Monitor mode — no local obs data"
        )
        self.status_msg_label.setStyleSheet(
            "color: #4A9EFF; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        self._layout_overlays()

    def _load_radar_station_sites(self) -> list[dict]:
        bounds = self._load_mbtiles_bounds()
        sites: list[dict] = []
        for site_id, name, lat, lon in NEXRAD_SITES:
            if bounds and not self._point_in_bounds(lat, lon, bounds):
                continue
            sites.append({
                "site_id": site_id,
                "name": name,
                "lat": lat,
                "lon": lon,
            })
        return sites

    def _nearest_radar_site(self, lat: float, lon: float) -> str:
        candidates = self._radar_station_sites or [
            {"site_id": site_id, "lat": site_lat, "lon": site_lon}
            for site_id, _, site_lat, site_lon in NEXRAD_SITES
        ]
        nearest = min(
            candidates,
            key=lambda site: self._haversine_km(lat, lon, site["lat"], site["lon"]),
        )
        return nearest["site_id"]

    def _load_mbtiles_bounds(self) -> tuple[float, float, float, float] | None:
        try:
            conn = sqlite3.connect(TILES_PATH)
            row = conn.execute(
                "SELECT value FROM metadata WHERE name='bounds'"
            ).fetchone()
            conn.close()
            if not row or not row[0]:
                return None
            west, south, east, north = (float(value) for value in row[0].split(","))
            return west, south, east, north
        except Exception:
            return None

    @staticmethod
    def _point_in_bounds(
        lat: float,
        lon: float,
        bounds: tuple[float, float, float, float],
    ) -> bool:
        west, south, east, north = bounds
        return west <= lon <= east and south <= lat <= north

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        import math

        r = 6371.0
        lat1r = math.radians(lat1)
        lat2r = math.radians(lat2)
        dlat = lat2r - lat1r
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c

    # ── Clock ─────────────────────────────────────────────────────────────────

    def _update_clock(self):
        now = datetime.now(timezone.utc)
        self.clock_label.setText(now.strftime("%H:%M:%S UTC"))
        self.date_label.setText(f"{now.day} {now.strftime('%b %Y')}")
        hidden_vehicle_ids: list[str] = []
        vehicle_panel_needs_refresh = False
        vehicle_detail_needs_refresh = False
        for v in list(self._vehicles.values()):
            obs = v.latest_obs
            if obs is None:
                continue
            if not self._should_display_vehicle_obs(obs):
                hidden_vehicle_ids.append(v.id)
                continue
            color = self._obs_age_color(obs)
            age_label = self._obs_age_label(obs)
            prev_state = self._vehicle_age_display_state.get(v.id)
            if prev_state == (color, age_label):
                continue
            self._vehicle_age_display_state[v.id] = (color, age_label)
            self.map_widget.add_vehicle(v.id, v.lat, v.lon, color, v.icon_type)
            vehicle_panel_needs_refresh = True
            if v.id in self._selected_vehicle_ids:
                vehicle_detail_needs_refresh = True

        for vehicle_id in hidden_vehicle_ids:
            self._hide_vehicle(vehicle_id)

        if vehicle_panel_needs_refresh:
            self._refresh_vehicle_panel()
        if vehicle_detail_needs_refresh:
            self._refresh_vehicle_detail()
        if not self._clock_layout_synced:
            self._layout_overlays()
            self._clock_layout_synced = True

    # ── Layer Order ────────────────────────────────────────────────────────────

    def _apply_layer_order(self, order: list[str]) -> None:
        """Reorder MapLibre layers to match the confirmed layer stack (bottom → top)."""
        # Walk bottom→top.  For each group, move every layer in the group
        # before the first layer of the *next* group that has real MapLibre IDs.
        def _first_ml_id(key: str) -> str | None:
            for lid in MAPLIBRE_LAYERS.get(key, []):
                return lid
            return None

        for i, key in enumerate(order):
            ml_ids = MAPLIBRE_LAYERS.get(key, [])
            if not ml_ids:
                continue
            # Find the before-anchor: first MapLibre ID of the next group above
            before = None
            for j in range(i + 1, len(order)):
                before = _first_ml_id(order[j])
                if before:
                    break
            for lid in ml_ids:
                self.map_widget.move_layer_before(lid, before)

    def _set_layer_active(self, key: str, active: bool) -> None:
        """Notify the layer pill that a layer's visibility changed."""
        if hasattr(self, "_layer_pill"):
            self._layer_pill.set_layer_active(key, active)

    # ── Debug Pill ────────────────────────────────────────────────────────────

    def _init_debug_panel(self):
        """Create the floating debug pill and wire up the status refresh timer."""
        self._debug_pill = DebugPill(self._map_container)
        self._debug_pill.size_changed.connect(self._layout_overlays)
        self._debug_pill.show()
        self._layout_overlays()

        self._debug_timer = QTimer()
        self._debug_timer.timeout.connect(self._refresh_debug_panel)
        self._debug_timer.start(1000)

    def _refresh_debug_panel(self):
        if not hasattr(self, "_debug_pill"):
            return

        lines = ["─── RADAR ───────────────────────────────"]
        fetcher = getattr(self, "_radar_fetcher", None)
        if fetcher is None:
            lines.append("radar disabled or not initialized")
        else:
            loop_timer = getattr(self, "_loop_timer", None)
            scan_cache = getattr(self, "_scan_cache", {})
            lines.append(
                f"fetcher running: {fetcher._running}  "
                f"site: {fetcher._site}  products: {fetcher._products}"
            )
            lines.append(
                f"map ready: {getattr(self.map_widget, '_map_ready', '?')}  "
                f"loop active: {loop_timer.isActive() if loop_timer else False}  "
                f"interval: {loop_timer.interval() if loop_timer else 0}ms"
            )
            lines.append(f"cache keys: {list(scan_cache.keys())}")
            for key, scans in scan_cache.items():
                if scans:
                    ages = [f"{s.age_seconds:.0f}s" for s in scans]
                    lines.append(f"  {key}: {len(scans)} frames  ages=[{', '.join(ages)}]")

        lines.append("─── MQTT ────────────────────────────────")
        mqtt = getattr(self, "_mqtt_client", None)
        if mqtt:
            connected = self.conn_indicator.text() == "● AWS OK"
            lines.append(
                f"host: {config.MQTT_HOST or '(not configured)'}:{config.MQTT_PORT}  "
                f"connected: {connected}"
            )
        else:
            lines.append("mqtt client not initialized")

        lines.append("─── DATA INPUTS ─────────────────────────")
        gps = getattr(self, "_gps_reader", None)
        if gps:
            alive = gps._thread is not None and gps._thread.is_alive()
            lines.append(f"GPS reader: port={gps._port}  thread alive: {alive}")
        else:
            lines.append("GPS reader: not configured")

        watcher = getattr(self, "_obs_watcher", None)
        if watcher:
            lines.append(
                f"obs watcher: dir={watcher._data_dir}  "
                f"file={watcher._current_path.name if watcher._current_path else '?'}  "
                f"pos={watcher._last_pos}  timer active: {watcher._timer.isActive()}"
            )
        else:
            lines.append("obs watcher: not configured")

        lines.append("─── VEHICLES ────────────────────────────")
        lines.append(f"tracked vehicles: {list(self._vehicles.keys())}")
        spl = getattr(self, "_station_layer", None)
        if spl:
            lines.append(f"station plot cache: {list(spl._cache.keys())}")

        self._debug_pill.set_status("\n".join(lines))

    def _toggle_debug_panel(self):
        if not hasattr(self, "_debug_pill"):
            self._init_debug_panel()
        else:
            self._debug_pill.toggle()

    def _cleanup_debug_panel(self):
        if hasattr(self, "_debug_pill"):
            self._debug_pill.cleanup()

    # ── Error Log Panel ────────────────────────────────────────────────────────

    def _init_error_log_panel(self):
        import os
        self._error_log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "storm_errors.log"
        )

        self._error_log_dock = QDockWidget("ERROR LOG  (Ctrl+E to close)", self)
        self._error_log_dock.setObjectName("errorLogDock")
        self._error_log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self._error_log_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._error_log_text = QLabel("no errors logged")
        self._error_log_text.setFont(QFont("Courier New", 9))
        self._error_log_text.setStyleSheet(
            "color: #FFD166; background: #050508; padding: 6px; border-radius: 4px;"
        )
        self._error_log_text.setWordWrap(True)
        self._error_log_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._error_log_text)

        self._error_log_dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._error_log_dock)

        self._error_log_timer = QTimer()
        self._error_log_timer.timeout.connect(self._refresh_error_log_panel)
        self._error_log_timer.start(3000)
        self._refresh_error_log_panel()

    def _refresh_error_log_panel(self):
        if not hasattr(self, "_error_log_text"):
            return
        import os
        path = self._error_log_path
        if not os.path.exists(path):
            self._error_log_text.setText("no errors logged yet")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tail = lines[-50:] if len(lines) > 50 else lines
            self._error_log_text.setText("".join(tail).rstrip() or "no errors logged yet")
        except Exception as exc:
            self._error_log_text.setText(f"(could not read log: {exc})")

    def _toggle_error_log_panel(self):
        if not hasattr(self, "_error_log_dock"):
            self._init_error_log_panel()
        else:
            self._error_log_dock.setVisible(not self._error_log_dock.isVisible())
