# ui/main_window.py
# top-level application window for STORM.
# assembles the layout: toolbar, map widget, status bar, and collapsible panels.

import json
import logging
import threading
import runtime_flags
import html
from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QLabel, QDockWidget, QVBoxLayout, QHBoxLayout,
    QToolButton, QFrame, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from ui.theme import DARK_THEME, ACCENT, TEXT_MUTED, BG_PANEL
from ui.map_widget import MapWidget
from ui.radar_controls import RadarControls
from ui.hazard_controls import HazardControls
from ui.outlook_panel import OutlookPanel
from ui.radar_overlay import RadarOverlay
from ui.annotation_tools import AnnotationTools
from ui.annotation_dialog import AnnotationPlaceDialog, AnnotationEditDialog
from ui.drawing_dialog import DrawingTitleDialog, DrawingEditDialog
from ui.storm_cone_dialog import StormConeInputDialog
from data.radar_fetcher import RadarFetcher
from data.hazard_fetcher import HazardFetcher
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
from network.vehicle_fetcher import VehicleFetcher
from data.gps_reader import GPSReader
from data.obs_file_watcher import ObsFileWatcher, FieldMap
from ui.station_plot_layer import StationPlotLayer

log = logging.getLogger(__name__)


def _coords_close(a, b, tol: float = 1e-4) -> bool:
    """Return True if two [lat, lon] points are within ~10 m of each other."""
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


class MainWindow(QMainWindow):
    # Emitted from background threads to update the discussion text panel safely.
    _panel_text_ready = pyqtSignal(str, str)

    def __init__(self, debug: bool = False, monitor: bool = False):
        super().__init__()
        self._debug = debug
        self._monitor = monitor

        self.setWindowTitle(f"STORM  v{config.VERSION}")
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

        # Fine-grained startup toggles are for crash-isolation only.
        # Keep them opt-in so normal runs always start full functionality.
        self._disable_radar = runtime_flags.FLAGS.disable_radar
        self._disable_mqtt = runtime_flags.FLAGS.disable_mqtt
        self._disable_vehicle_fetcher = runtime_flags.FLAGS.disable_vehicle_fetcher
        self._disable_annotations = runtime_flags.FLAGS.disable_annotations
        self._disable_deploy_locs = runtime_flags.FLAGS.disable_deploy_locs
        self._disable_data_inputs = runtime_flags.FLAGS.disable_data_inputs

        # Features that require MQTT should be disabled when MQTT is disabled.
        if self._disable_mqtt:
            self._disable_vehicle_fetcher = True
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
                "Startup toggles: radar=%s mqtt=%s fetcher=%s annotations=%s deploy_locs=%s data_inputs=%s",
                "off" if self._disable_radar else "on",
                "off" if self._disable_mqtt else "on",
                "off" if self._disable_vehicle_fetcher else "on",
                "off" if self._disable_annotations else "on",
                "off" if self._disable_deploy_locs else "on",
                "off" if self._disable_data_inputs else "on",
            )

            if not self._disable_radar:
                self._init_radar()
            self._init_hazards()
            if not self._disable_mqtt:
                self._init_mqtt()
            if not self._disable_vehicle_fetcher:
                self._init_vehicle_fetcher()
            if not self._disable_annotations:
                self._init_annotations()
                self._init_storm_cone()

            self._init_measure()
            self._init_stations()

            if not self._disable_deploy_locs:
                self._init_deploy_locs()
            if not self._disable_data_inputs:
                self._init_data_inputs()

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

        # Restore window geometry and dock layout from last session.
        _s = QSettings("NSSL", "STORM")
        if _s.contains("geometry"):
            self.restoreGeometry(_s.value("geometry"))
        if _s.contains("windowState"):
            self.restoreState(_s.value("windowState"))
            # keep toolbar button in sync with whatever the dock restored to
            self.btn_vehicles.setChecked(self.vehicle_dock.isVisible())

        # Extra startup layout passes avoid first-paint clipping in floating pills.
        QTimer.singleShot(0, self._layout_overlays)
        QTimer.singleShot(220, self._layout_overlays)

        # ctrl+d toggles debug panel even outside --debug mode (emergency diagnostic)
        self._debug_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self._debug_shortcut.activated.connect(self._toggle_debug_panel)
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
        # pulse layout updates for the duration of the open/close animation
        self.btn_radar.toggled.connect(self._start_layout_pulse)

        self._add_separator(tb)

        # ── vehicles ──────────────────────────────────────────────────────
        self.btn_vehicles = self._toolbar_toggle("VEHICLES", "Toggle vehicle panel", tb)

        # ── previous deployment locations ─────────────────────────────────
        self.btn_prev_locs = self._toolbar_toggle(
            "PREV LOCS", "Show previous truck deployment locations", tb
        )

        self._add_separator(tb)

        # ── hazards ───────────────────────────────────────────────────────
        self.btn_hazards = self._toolbar_toggle(
            "HAZARDS", "Show/hide SPC and NWS hazard overlays", tb
        )
        self.hazard_controls = HazardControls(self._map_container)
        self.hazard_controls.setObjectName("floatingToolbar")
        self.btn_hazards.toggled.connect(self.hazard_controls.toggle_drawer)
        self.btn_hazards.toggled.connect(self._start_layout_pulse)

        self.outlook_panel = OutlookPanel(self._map_container)
        self.outlook_panel.closed.connect(self._layout_overlays)
        self._panel_text_ready.connect(self.outlook_panel.show_text)

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
        # ── left overlay pill — coords, vehicle count, messages ───────────
        self._status_left = QWidget(self._map_container)
        self._status_left.setObjectName("statusOverlayLeft")
        left = QHBoxLayout(self._status_left)
        left.setContentsMargins(8, 4, 8, 4)
        left.setSpacing(8)

        self.coord_label = QLabel("LAT: ---.---- LON: ---.----")
        coord_probe = "LAT: -180.0000  LON: -180.0000"
        self.coord_label.setMinimumWidth(self.coord_label.fontMetrics().horizontalAdvance(coord_probe) + 8)
        self.vehicle_count_label = QLabel("VEHICLES: 0")
        self.status_msg_label = QLabel("")

        for lbl in [self.coord_label, self.vehicle_count_label]:
            lbl.setStyleSheet("color: #B5BDCC; font-size: 10px; letter-spacing: 0.5px;")

        if self._monitor:
            monitor_badge = QLabel("● OBSERVER")
            monitor_badge.setStyleSheet(
                "color: #FFD166; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
            )
            left.addWidget(monitor_badge)
            left.addWidget(self._status_divider())

        left.addWidget(self.coord_label)
        left.addWidget(self._status_divider())
        left.addWidget(self.vehicle_count_label)
        left.addWidget(self._status_divider())
        left.addWidget(self.status_msg_label)

        # ── right overlay pill — connection status + date + time ─────────
        self._status_right = QWidget(self._map_container)
        self._status_right.setObjectName("statusOverlayRight")
        right = QVBoxLayout(self._status_right)
        right.setContentsMargins(10, 6, 10, 6)
        right.setSpacing(2)

        self.conn_indicator = QLabel("● OFFLINE")
        self.conn_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
        )
        self.conn_indicator.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.conn_indicator)

        self.date_label = QLabel("-- --- ----")
        self.date_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #B5BDCC;"
        )
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.date_label)

        self.clock_label = QLabel("--:--:-- UTC")
        self.clock_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #B5BDCC;"
        )
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.clock_label)


    def _status_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #394056; margin: 4px 0;")
        return div

    def closeEvent(self, event):
        _s = QSettings("NSSL", "STORM")
        _s.setValue("geometry", self.saveGeometry())
        _s.setValue("windowState", self.saveState())
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

            # Radar controls and annotation tools drop down below the toolbar
            # as separate floating pills; each centers horizontally independently.
            _drop_y = MARGIN + tb_h + 4
            if hasattr(self, "radar_controls"):
                rc = self.radar_controls
                rc_w = rc.sizeHint().width()
                rc_x = max(0, (r.width() - rc_w) // 2)
                rc.setGeometry(rc_x, _drop_y, rc_w, rc.sizeHint().height())
                rc.raise_()
            if hasattr(self, "annotation_tools"):
                at = self.annotation_tools
                at_w = at.sizeHint().width()
                at_x = max(0, (r.width() - at_w) // 2)
                at.setGeometry(at_x, _drop_y, at_w, at.sizeHint().height())
                at.raise_()
            if hasattr(self, "hazard_controls"):
                hz = self.hazard_controls
                hz_w = hz.sizeHint().width()
                hz_x = max(0, (r.width() - hz_w) // 2)
                hz.setGeometry(hz_x, _drop_y, hz_w, hz.sizeHint().height())
                hz.raise_()

        # outlook panel — right side, below toolbar, above status pill
        if hasattr(self, "outlook_panel"):
            op = self.outlook_panel
            top = _drop_y if hasattr(self, "_floating_toolbar") else MARGIN
            bottom_pad = 40  # clear status pills
            panel_h = max(100, r.height() - top - MARGIN - bottom_pad)
            op.setGeometry(r.width() - OutlookPanel.PANEL_WIDTH - MARGIN, top,
                           OutlookPanel.PANEL_WIDTH, panel_h)
            op.raise_()

        # left status pill — bottom-left corner
        if hasattr(self, "_status_left"):
            self._status_left.adjustSize()
            sl = self._status_left.size()
            self._status_left.setGeometry(
                MARGIN, r.height() - sl.height() - MARGIN,
                sl.width(), sl.height()
            )
            self._status_left.raise_()

        # right status pill — bottom-right corner
        if hasattr(self, "_status_right"):
            self._status_right.adjustSize()
            sr = self._status_right.size()
            self._status_right.setGeometry(
                r.width() - sr.width() - MARGIN, r.height() - sr.height() - MARGIN,
                sr.width(), sr.height()
            )
            self._status_right.raise_()

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

    def update_zoom(self, zoom: float):
        # Zoom readout intentionally removed from the field status pill.
        pass

    def update_vehicle_count(self, count: int):
        self.vehicle_count_label.setText(f"VEHICLES: {count}")

    def set_connection_status(self, connected: bool):
        if connected:
            self.conn_indicator.setText("● CONNECTED")
            self.conn_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #39D98A;"
            )
        else:
            self.conn_indicator.setText("● OFFLINE")
            self.conn_indicator.setStyleSheet(
                "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
            )

    # ── Vehicle Panel (Dock) ──────────────────────────────────────────────────

    def _init_vehicle_panel(self):
        self.vehicle_dock = QDockWidget("", self)
        self.vehicle_dock.setObjectName("vehicleDock")
        self.vehicle_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.vehicle_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        container = QWidget()
        container.setStyleSheet(f"background-color: {BG_PANEL};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel("ACTIVE VEHICLES")
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        self._chk_station_plots = QCheckBox("show station plots")
        self._chk_station_plots.setChecked(True)
        self._chk_station_plots.setStyleSheet(
            f"font-size: 11px; color: {TEXT_MUTED}; padding: 2px 0;"
        )
        layout.addWidget(self._chk_station_plots)

        # placeholder until vehicle list is populated via MQTT
        placeholder = QLabel("No vehicles connected")
        placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; padding: 8px 0;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._vehicle_placeholder = placeholder
        layout.addWidget(placeholder)

        self._vehicle_info_label = QLabel("")
        self._vehicle_info_label.setWordWrap(True)
        self._vehicle_info_label.setTextFormat(Qt.TextFormat.RichText)
        self._vehicle_info_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; padding: 2px 0;"
        )
        self._vehicle_info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._vehicle_info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._vehicle_info_label.linkActivated.connect(self._on_vehicle_panel_link)
        layout.addWidget(self._vehicle_info_label)

        layout.addStretch()

        self.vehicle_dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.vehicle_dock)

        # start hidden — opened via toolbar toggle
        self.vehicle_dock.hide()
        self.btn_vehicles.toggled.connect(self.vehicle_dock.setVisible)
        self.btn_vehicles.toggled.connect(self._start_layout_pulse)
        self.vehicle_dock.visibilityChanged.connect(self._start_layout_pulse)
        self.btn_prev_locs.toggled.connect(self.map_widget.set_deploy_locs_visible)

    # ── Radar ─────────────────────────────────────────────────────────────────

    def _init_radar(self):
        self._radar_overlay = RadarOverlay(self.map_widget)
        self._radar_fetcher = RadarFetcher()
        self._scan_cache: dict[str, list] = {}   # key: "site/product" → list of RadarScan

        # 500 ms between loop frames — fast enough to feel animated, slow enough to read
        self._loop_timer = QTimer()
        self._loop_timer.setInterval(500)
        self._loop_timer.timeout.connect(self._advance_loop_frame)

        # ── wire radar controls → fetcher ─────────────────────────────────
        self.radar_controls.radar_toggled.connect(self._on_radar_toggled)
        self.radar_controls.site_changed.connect(self._on_radar_site_changed)
        self.radar_controls.product_changed.connect(self._on_radar_product_changed)
        self.radar_controls.fetch_requested.connect(self._radar_fetcher.fetch_now)
        self.radar_controls.frame_requested.connect(self._display_cached_frame)
        self.radar_controls.loop_toggled.connect(self._on_loop_toggled)

        # ── wire fetcher → decoder → overlay ─────────────────────────────
        self._radar_fetcher.new_data.connect(self._on_radar_data)
        self._radar_fetcher.fetch_error.connect(self._on_radar_error)
        self._radar_error_clear_timer = QTimer()
        self._radar_error_clear_timer.setSingleShot(True)
        self._radar_error_clear_timer.timeout.connect(self._clear_radar_error)

        # seed site list from config home location (overrides the hardcoded Norman default)
        self.radar_controls.set_reference_location(config.HOME_LAT, config.HOME_LON)

        initial_site = self.radar_controls.current_site()
        self._radar_fetcher.set_site(initial_site)
        self._radar_fetcher.set_products(["N0Q", "N0U"])

        # delay auto-start so map has time to initialize
        QTimer.singleShot(800, self._auto_start_radar)

    def _init_hazards(self):
        self._hazard_fetcher = HazardFetcher(parent=self)
        self.hazard_controls.spc_mode_changed.connect(self._on_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_spc_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_spc_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_nws_warnings_toggled)
        self.hazard_controls.fetch_requested.connect(self._hazard_fetcher.fetch_now)

        self._hazard_fetcher.spc_received.connect(self._on_spc_received)
        self._hazard_fetcher.nws_received.connect(self._on_nws_received)
        self._hazard_fetcher.spc_watches_received.connect(self._on_spc_watches_received)
        self._hazard_fetcher.spc_mds_received.connect(self._on_spc_mds_received)
        self._hazard_fetcher.fetch_error.connect(self._on_hazard_error)

        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self._hazard_error_clear_timer = QTimer()
        self._hazard_error_clear_timer.setSingleShot(True)
        self._hazard_error_clear_timer.timeout.connect(self._clear_radar_error)
        self._hazard_fetcher.start()

        self.map_widget.map_moved.connect(self._on_map_moved_for_bbox)

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

    def _auto_start_radar(self):
        self._radar_fetcher.start()
        self._radar_fetcher.fetch_now()

    def _on_radar_error(self, msg: str):
        self.status_msg_label.setText(f"Radar: {msg}")
        self._layout_overlays()
        self._radar_error_clear_timer.start(10_000)

    def _clear_radar_error(self):
        if self.status_msg_label.text().startswith("Radar:") or self.status_msg_label.text().startswith("Hazards:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_radar_toggled(self, enabled: bool):
        if enabled:
            self._radar_fetcher.start()
            self._radar_fetcher.fetch_now()
        else:
            # stop everything and clear all state when disabled
            self._loop_timer.stop()
            self.radar_controls.reset_cache_ui()
            self._scan_cache.clear()
            self._radar_fetcher.reset_history()   # force full backfill on re-enable
            self._radar_fetcher.stop()
            self._radar_overlay.clear()
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_hazard_error(self, msg: str):
        self.status_msg_label.setText(f"Hazards: {msg}")
        self._layout_overlays()
        self._hazard_error_clear_timer.start(10_000)

    def _on_map_moved_for_bbox(self, lat: float, lon: float, zoom: float):
        # Approximate visible bounding box from map center + zoom level.
        # lon_span ≈ 360/2^zoom; lat_span uses a modest aspect ratio estimate.
        lon_half = 180.0 / (2 ** zoom)
        lat_half = lon_half * 0.65
        self._hazard_fetcher.set_nws_bbox(
            lon - lon_half, lat - lat_half, lon + lon_half, lat + lat_half
        )

    def _on_spc_received(self, cat_fc: dict, wind_fc: dict, hail_fc: dict, tor_fc: dict):
        self.map_widget.set_spc_geojson(cat_fc, wind_fc, hail_fc, tor_fc)

    def _on_nws_received(self, warnings_fc: dict):
        self.map_widget.set_nws_warnings_geojson(warnings_fc)

    def _on_spc_watches_received(self, watches_fc: dict):
        self.map_widget.set_spc_watches_geojson(watches_fc)

    def _on_spc_mds_received(self, mds_fc: dict):
        self.map_widget.set_spc_mds_geojson(mds_fc)

    def _on_spc_mds_toggled(self, enabled: bool):
        self._hazard_fetcher.set_spc_mds_enabled(enabled)
        self.map_widget.set_spc_mds_visible(enabled)
        if enabled:
            self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _update_hazard_legend(self):
        """Recompute which hazard layers are active and update the pill legend."""
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
        self.hazard_controls.update_legend(active)
        # Drive _layout_overlays during the legend resize animation so the
        # floating overlay geometry tracks the new sizeHint().
        self._start_layout_pulse()

    def _on_spc_feature_clicked(self, payload: str):
        """Handle a click on an SPC cat or MD polygon — fetch and show discussion text."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return
        source = data.get("source", "")
        props = data.get("properties", {})

        if source == "spc-cat":
            title = "DAY 1 CONVECTIVE OUTLOOK"
            kind, identifier = "swo", None
        elif source == "spc-mds":
            name = str(props.get("name", "")).strip()
            # name is e.g. "MD 0176" — extract the number
            num = name.replace("MD", "").strip().zfill(4)
            title = f"MESOSCALE DISCUSSION {num}"
            kind, identifier = "mcd", num
        else:
            return

        self.outlook_panel.show_loading(title)
        self._layout_overlays()
        threading.Thread(
            target=self._fetch_outlook_text,
            args=(title, kind, identifier),
            daemon=True,
        ).start()

    def _fetch_outlook_text(self, title: str, kind: str, identifier: str | None):
        """Fetch SPC discussion text in a background thread via IEM Mesonet AFOS API.

        Iowa State's Mesonet archives every NWS/SPC text product by AFOS PIL and
        is much more reliable than scraping SPC's website or using the NWS products
        API (which uses a different product-type taxonomy than the SPC AFOS PILs).

        AFOS PILs used:
          Day 1 Outlook: SWODY1   (Severe Weather Outlook, Day 1)
          MDs:           SPCMCD{nnnn}  (e.g. SPCMCD0176)
        """
        from urllib.request import Request, urlopen

        IEM_BASE = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
        HEADERS = {"User-Agent": "STORM/1.0 (contact: support)"}

        def _iem_fetch(pil: str) -> str:
            url = f"{IEM_BASE}?pil={pil}&limit=1&fmt=text"
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            # Strip AFOS framing bytes (SOH=\x01, STX=\x02, ETX=\x03) if present
            return raw.strip("\x01\x02\x03\r\n").strip()

        try:
            if kind == "swo":
                text = _iem_fetch("SWODY1")
            elif kind == "mcd":
                text = _iem_fetch(f"SPCMCD{identifier}")
            else:
                text = ""
            if not text:
                text = "(No discussion text found)"
        except Exception as exc:
            text = f"Failed to load discussion:\n{exc}"

        self._panel_text_ready.emit(title, text)

    def _on_spc_mode_changed(self, mode: str):
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self._hazard_fetcher.set_spc_category_enabled(key, outlook_on)
            self.map_widget.set_spc_category_visible(key, outlook_on)

        for key in ("tor", "wind", "hail"):
            on = mode == key
            self._hazard_fetcher.set_spc_product_enabled(key, on)
            self.map_widget.set_spc_product_visible(key, on)

        if mode:
            self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_spc_watches_toggled(self, enabled: bool):
        self._hazard_fetcher.set_spc_watches_enabled(enabled)
        self.map_widget.set_spc_watches_visible(enabled)
        if enabled:
            self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_nws_warnings_toggled(self, enabled: bool):
        self._hazard_fetcher.set_nws_enabled(enabled)
        self.map_widget.set_nws_warnings_visible(enabled)
        if enabled:
            self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_radar_site_changed(self, site: str):
        # clear cache when site changes — old data belongs to a different location
        self._radar_fetcher.set_site(site)
        self._loop_timer.stop()
        self.radar_controls.reset_cache_ui()
        self._scan_cache.clear()
        self._radar_overlay.clear()

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
        log.debug("radar data received: %s/%s (%d bytes)", site, product, len(raw_bytes))

        scan = decode_nexrad_l3(site, product, raw_bytes)
        if scan is None:
            self.status_msg_label.setText(f"Radar decode failed: {site}/{product}")
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

        # only update UI and display for the currently visible product;
        # background product data is still cached above for instant switching
        if product != self.radar_controls.current_product():
            return

        was_live = self.radar_controls.is_at_latest_frame()
        self.radar_controls.set_cache_size(len(cache))

        # only auto-advance display if not looping and user was viewing live frame
        if not self.radar_controls.is_looping() and was_live:
            self._show_scan(scan)

    def _show_scan(self, scan):
        self._radar_overlay.update(scan)
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"))
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

        # publish-only — used by GPS-only vehicles to push position to broker
        self._vehicle_sync = VehicleSync(self._mqtt_client, parent=self)
        self._storm_cone_sync = StormConeSync(self._mqtt_client, parent=self)

        # connect after a short delay so the window is fully painted first
        if config.MQTT_HOST:
            QTimer.singleShot(500, self._mqtt_connect)
        else:
            log.info("MQTT host not configured — running offline")

    def _init_vehicle_fetcher(self):
        self._vehicle_fetcher = VehicleFetcher(parent=self)
        self._vehicle_fetcher.obs_ready.connect(self._on_fetched_vehicle_obs)
        if config.VEHICLES_URL:
            self._vehicle_fetcher.start(config.VEHICLES_URL, config.VEHICLES_POLL_S)
        else:
            log.info("vehicles_url not configured — vehicle fetcher disabled")

    def _on_fetched_vehicle_obs(self, obs):
        self.update_vehicle_obs(obs)

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
        if self.status_msg_label.text().startswith("MQTT:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_mqtt_disconnected(self, code: int):
        self.set_connection_status(False)
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
        self._annotation_sync = AnnotationSync(self._mqtt_client, parent=self)

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

        # remote annotations arriving over MQTT — update map without re-publishing
        self._annotation_sync.annotation_received.connect(self._recv_remote_annotation)
        self._annotation_sync.annotation_deleted.connect(self._recv_remote_annotation_deleted)

        self._init_drawings()

    def _init_drawings(self):
        self._drawings: dict[str, DrawingAnnotation] = {}
        self._active_drawing_type: str = ""
        self._drawing_points: list = []
        self._drawing_sync = DrawingSync(self._mqtt_client, parent=self)

        self.map_widget.map_double_clicked.connect(self._on_map_dblclick)
        self.map_widget.drawing_clicked.connect(self._on_drawing_clicked)
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
        # cancel any in-progress drawing when tool switches
        if getattr(self, "_active_drawing_type", ""):
            self._cancel_drawing()

        self._pending_cone_params = None
        self._active_annotation_type = ""
        self._active_drawing_type = ""

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
                self.map_widget.set_annotation_mode(True)
                self._set_placement_prompt("storm cone")
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
        if self._pending_cone_params is not None:
            cone = StormCone.new(lat, lon, **self._pending_cone_params)
            self._pending_cone_params = None
            self._active_annotation_type = ""
            self.map_widget.set_annotation_mode(False)
            self.annotation_tools.deactivate_tool()
            self._clear_placement_prompt()
            self._place_storm_cone(cone)
        elif self._active_annotation_type:
            dlg = AnnotationPlaceDialog(self._active_annotation_type, lat, lon, parent=self)
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
        dlg = AnnotationEditDialog(annotation, parent=self)
        if dlg.exec() == AnnotationEditDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_annotation(annotation_id)
            elif dlg.action() == "save":
                annotation.label = dlg.result_label()
                self._update_annotation(annotation)

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

    def _recv_remote_annotation_deleted(self, annotation_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        log.info("remote annotation deleted: %s", annotation_id)

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
        elif action == "save":
            drawing.title = dlg.result_title()
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

        # cone placed via ANNOTATE drawer — map cone-click → edit dialog
        self.map_widget.storm_cone_clicked.connect(self._on_storm_cone_clicked)

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

        # mutual exclusion: MEASURE and ANNOTATE both consume map clicks
        # connect exclusion BEFORE _on_measure_toggled so deactivation fires first
        self.btn_measure.toggled.connect(
            lambda on: self.btn_annotate.setChecked(False) if on else None
        )
        self.btn_annotate.toggled.connect(
            lambda on: self.btn_measure.setChecked(False) if on else None
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
        self._station_layer = StationPlotLayer(self.map_widget)
        self._chk_station_plots.toggled.connect(self._station_layer.set_visible)
        # station plots on by default — delayed until map is ready
        QTimer.singleShot(1200, lambda: self._station_layer.set_visible(
            self._chk_station_plots.isChecked()
        ))

    # ── Deployment Locations ──────────────────────────────────────────────────

    def _init_deploy_locs(self):
        if config.DEPLOY_LOCS_FILE:
            QTimer.singleShot(1200, self._load_deploy_locs)

    def _load_deploy_locs(self):
        try:
            with open(config.DEPLOY_LOCS_FILE) as f:
                points = json.load(f)
            self.map_widget.load_deploy_locs(points)
            log.info("deploy locs: loaded %d points from %s", len(points), config.DEPLOY_LOCS_FILE)
        except Exception as e:
            log.warning("deploy locs: could not load %s: %s", config.DEPLOY_LOCS_FILE, e)

    def update_vehicle_obs(self, obs: Observation) -> None:
        """Public entry point for all vehicle observation updates (MQTT, file watcher, GPS)."""
        v = self._vehicles.setdefault(
            obs.vehicle_id,
            Vehicle(id=obs.vehicle_id, lat=obs.lat, lon=obs.lon),
        )
        v.lat, v.lon, v.latest_obs = obs.lat, obs.lon, obs
        marker_color = self._obs_age_color(obs)
        self.map_widget.add_vehicle(obs.vehicle_id, obs.lat, obs.lon, marker_color)
        count = len(self._vehicles)
        self.update_vehicle_count(count)
        if hasattr(self, "_vehicle_placeholder"):
            if count == 0:
                self._vehicle_placeholder.setText("No vehicles connected")
            elif count == 1:
                self._vehicle_placeholder.setText("1 vehicle connected")
            else:
                self._vehicle_placeholder.setText(f"{count} vehicles connected")
        self._refresh_vehicle_panel()
        self._station_layer.update(obs.vehicle_id, obs.lat, obs.lon, obs)

    def _obs_age_minutes(self, obs: Observation) -> float:
        age = datetime.now(timezone.utc) - obs.timestamp
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

    def _refresh_vehicle_panel(self):
        if not hasattr(self, "_vehicle_info_label"):
            return
        if not self._vehicles:
            self._vehicle_info_label.setText("")
            return

        blocks: list[str] = []
        for vid in sorted(self._vehicles.keys()):
            v = self._vehicles[vid]
            obs = v.latest_obs
            if obs is None:
                blocks.append(
                    "<div style='margin-bottom:10px'>"
                    f"<span style='color:#5A5B6A'>●</span> "
                    f"<span style='color:#E8EAF0; font-weight:600;'>{html.escape(v.id)}</span><br/>"
                    "<span style='color:#394056;'>----------------------------------</span><br/>"
                    "<span style='color:#8E97AB;'>No observations yet</span>"
                    "</div>"
                )
                continue

            badge_color = self._obs_age_color(obs)
            temp_txt = "--"
            if obs.temperature_c is not None:
                temp_txt = f"{obs.temperature_c * 9 / 5 + 32:.0f}F"
            dew_txt = "--"
            if obs.dewpoint_c is not None:
                dew_txt = f"{obs.dewpoint_c * 9 / 5 + 32:.0f}F"
            wind_txt = "--"
            if obs.wind_speed_ms is not None and obs.wind_dir_deg is not None:
                wind_kts = obs.wind_speed_ms * 1.94384
                wind_txt = f"{wind_kts:.0f}kt @ {obs.wind_dir_deg:.0f}"
            pres_txt = "--"
            if obs.pressure_mb is not None:
                pres_txt = f"{obs.pressure_mb:.1f}mb"
            ts = obs.timestamp.astimezone(timezone.utc).strftime("%d %b %Y %H%M UTC")

            blocks.append(
                "<div style='margin-bottom:8px'>"
                f"<span style='color:{badge_color}; font-size:12px;'>●</span> "
                f"<a href='focus:{html.escape(v.id)}' style='color:#E8EAF0; font-weight:600; text-decoration:none;'>{html.escape(v.id)}</a>"
                f"<span style='color:{TEXT_MUTED}; font-size:9px;'> ↗</span><br/>"
                "<span style='color:#394056;'>----------------------------------</span><br/>"
                f"<span style='color:#B5BDCC;'>{ts}</span><br/>"
                f"<span style='color:#8E97AB;'>{obs.lat:.4f}, {obs.lon:.4f}</span><br/>"
                f"<span style='color:#B5BDCC;'>P: {pres_txt}</span><br/>"
                f"<span style='color:#B5BDCC;'>T: {temp_txt}</span><br/>"
                f"<span style='color:#B5BDCC;'>Td: {dew_txt}</span><br/>"
                f"<span style='color:#B5BDCC;'>Wind: {wind_txt}</span>"
                "</div>"
            )
        self._vehicle_info_label.setText("".join(blocks))

    def _on_vehicle_panel_link(self, href: str):
        """Called when a vehicle name link is clicked in the vehicle panel."""
        if not href.startswith("focus:"):
            return
        vid = href[len("focus:"):]
        v = self._vehicles.get(vid)
        if v is not None:
            self.map_widget.fly_to(v.lat, v.lon, zoom=13)

    # ── Data inputs (GPS + file watcher) ──────────────────────────────────────

    def _init_data_inputs(self):
        """Start Track A (file watcher) and/or Track B (GPS) if configured."""
        self._gps_reader: GPSReader | None = None
        self._obs_watcher: ObsFileWatcher | None = None

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
            self._gps_reader.obs_ready.connect(self.update_vehicle_obs)
            self._gps_reader.obs_ready.connect(self._vehicle_sync.publish_obs)
            self._gps_reader.start()
            log.info("GPS reader started in auto-detect mode")

        # Track A — instrument file watcher (surface obs vehicles)
        if config.OBS_FILE_DIR:
            field_map = FieldMap(
                lat=config.OBS_FILE_COL_LAT,
                lon=config.OBS_FILE_COL_LON,
                date_col=config.OBS_FILE_COL_DATE,
                time_col=config.OBS_FILE_COL_TIME,
                timestamp_col=config.OBS_FILE_COL_TIMESTAMP,
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
                parent=self,
            )
            self._obs_watcher.obs_ready.connect(self.update_vehicle_obs)
            self._obs_watcher.obs_ready.connect(self._vehicle_sync.publish_obs)
            self._obs_watcher.start()
            log.info("Obs file watcher started: dir=%s", config.OBS_FILE_DIR)
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

    # ── Clock ─────────────────────────────────────────────────────────────────

    def _update_clock(self):
        now = datetime.now(timezone.utc)
        self.clock_label.setText(now.strftime("%H:%M:%S UTC"))
        self.date_label.setText(f"{now.day} {now.strftime('%b %Y')}")
        # Refresh freshness color + panel age text even without new incoming samples.
        for v in self._vehicles.values():
            if v.latest_obs is not None:
                self.map_widget.add_vehicle(
                    v.id, v.lat, v.lon, self._obs_age_color(v.latest_obs)
                )
        self._refresh_vehicle_panel()
        if not self._clock_layout_synced:
            self._layout_overlays()
            self._clock_layout_synced = True

    # ── Debug Panel ───────────────────────────────────────────────────────────

    def _init_debug_panel(self):
        # collapsible dock showing live fetch/cache/loop state
        self._debug_dock = QDockWidget("DEBUG", self)
        self._debug_dock.setObjectName("debugDock")
        self._debug_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self._debug_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._debug_text = QLabel("initializing...")
        self._debug_text.setFont(QFont("Courier New", 9))
        self._debug_text.setStyleSheet(
            "color: #39D98A; background: #050508; padding: 6px; border-radius: 4px;"
        )
        self._debug_text.setWordWrap(True)
        layout.addWidget(self._debug_text)

        self._debug_dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._debug_dock)

        # refresh debug text every second
        self._debug_timer = QTimer()
        self._debug_timer.timeout.connect(self._refresh_debug_panel)
        self._debug_timer.start(1000)

    def _refresh_debug_panel(self):
        if not hasattr(self, "_debug_text"):
            return

        lines = ["─── RADAR ───────────────────────────────"]
        fetcher = self._radar_fetcher
        lines.append(
            f"fetcher running: {fetcher._running}  "
            f"site: {fetcher._site}  products: {fetcher._products}"
        )
        lines.append(
            f"map ready: {getattr(self.map_widget, '_map_ready', '?')}  "
            f"loop active: {self._loop_timer.isActive()}  "
            f"interval: {self._loop_timer.interval()}ms"
        )
        lines.append(f"cache keys: {list(self._scan_cache.keys())}")
        for key, scans in self._scan_cache.items():
            if scans:
                ages = [f"{s.age_seconds:.0f}s" for s in scans]
                lines.append(f"  {key}: {len(scans)} frames  ages=[{', '.join(ages)}]")

        lines.append("─── MQTT ────────────────────────────────")
        mqtt = getattr(self, "_mqtt_client", None)
        if mqtt:
            connected = self.conn_indicator.text().startswith("● C")
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

        self._debug_text.setText("\n".join(lines))

    def _toggle_debug_panel(self):
        if not hasattr(self, "_debug_dock"):
            self._init_debug_panel()
        else:
            self._debug_dock.setVisible(not self._debug_dock.isVisible())
