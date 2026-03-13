# ui/main_window.py
# top-level application window for STORM.
# assembles the layout: toolbar, map widget, status bar, and collapsible panels.

import json
import logging
import threading
import runtime_flags
from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QLabel, QDockWidget, QVBoxLayout, QHBoxLayout,
    QToolButton, QFrame, QCheckBox, QSizePolicy, QPushButton, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer, QSettings, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from ui.theme import DARK_THEME, ACCENT, TEXT_MUTED, BG_PANEL
from ui.map_widget import MapWidget
from ui.radar_controls import RadarControls
from ui.hazard_controls import HazardControls
from ui.satellite_controls import SatelliteControls
from ui.outlook_panel import OutlookPanel
from ui.radar_overlay import RadarOverlay
from ui.annotation_tools import AnnotationTools
from ui.annotation_dialog import AnnotationPlaceDialog, AnnotationEditDialog
from ui.drawing_dialog import DrawingTitleDialog, DrawingEditDialog
from ui.storm_cone_dialog import StormConeInputDialog
from data.radar_fetcher import RadarFetcher
from data.hazard_fetcher import HazardFetcher
from data.satellite_fetcher import SatelliteFetcher
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


def _clear_layout(layout):
    """Remove and schedule deletion of all widgets in a layout."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


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

            # 1) Local data sources first (requires MQTT to be ready for publish)
            if not self._disable_mqtt:
                self._init_mqtt()
            if not self._disable_data_inputs:
                self._init_data_inputs()

            # 2) Vehicle locations fetcher (after local data)
            if not self._disable_vehicle_fetcher:
                self._init_vehicle_fetcher()

            # 3) Heavier network fetchers after vehicles are live
            if not self._disable_radar:
                self._init_radar()
            self._init_hazards()
            self._init_satellite()

            if not self._disable_annotations:
                self._init_annotations()
                self._init_storm_cone()

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

        # Restore window geometry and dock layout from last session.
        _s = QSettings("NSSL", "STORM")
        if _s.contains("geometry"):
            self.restoreGeometry(_s.value("geometry"))
        if _s.contains("windowState"):
            self.restoreState(_s.value("windowState"))
            # keep toolbar button in sync with current vehicle panel visibility
            self.btn_vehicles.setChecked(self.vehicle_panel.isVisible())

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
            lbl.setStyleSheet("color: #C8D0DE; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;")

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

        self.hazard_indicator = QLabel("● DATA OFFLINE")
        self.hazard_indicator.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; color: #E53935;"
        )
        self.hazard_indicator.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hazard_indicator.setVisible(False)
        right.addWidget(self.hazard_indicator)

        self.date_label = QLabel("-- --- ----")
        self.date_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #C8D0DE;"
        )
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.date_label)

        self.clock_label = QLabel("--:--:-- UTC")
        self.clock_label.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.5px; color: #C8D0DE;"
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
            if hasattr(self, "hazard_controls") and self.btn_hazards.isChecked():
                _stack(self.hazard_controls)
            if hasattr(self, "satellite_controls") and self.btn_satellite.isChecked():
                _stack(self.satellite_controls)
            if hasattr(self, "annotation_tools") and self.btn_annotate.isChecked():
                _stack(self.annotation_tools)

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
        if hasattr(self, "_vehicle_count_badge"):
            self._vehicle_count_badge.setText(str(count))

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

        # placeholder until vehicle list is populated via MQTT
        placeholder = QLabel("")
        placeholder.setObjectName("vehiclePillEmpty")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignTop)
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

        # detail pill (hidden until a vehicle is selected)
        self._selected_vehicle_ids = []
        self._last_selected_vehicle_id = None
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
        self._radar_fetcher.set_products(["N0B", "N0U"])

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
        self._hazard_fetcher.connectivity_changed.connect(self._on_hazard_connectivity)

        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self._hazard_error_clear_timer = QTimer()
        self._hazard_error_clear_timer.setSingleShot(True)
        self._hazard_error_clear_timer.timeout.connect(self._clear_radar_error)

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
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        # fetcher signals → handlers
        self._satellite_fetcher.meso_sectors_updated.connect(self._on_meso_sectors_updated)
        self._satellite_fetcher.frames_updated.connect(self._on_satellite_frames_updated)

        self._satellite_fetcher.start()

        # drawer mutually exclusive with radar/hazards/annotate
        for btn, other in [
            (self.btn_satellite, self.btn_radar),
            (self.btn_satellite, self.btn_hazards),
            (self.btn_satellite, self.btn_annotate),
            (self.btn_radar,     self.btn_satellite),
            (self.btn_hazards,   self.btn_satellite),
            (self.btn_annotate,  self.btn_satellite),
        ]:
            btn.toggled.connect(
                lambda on, o=other: o.setChecked(False) if on else None
            )

    def _on_satellite_toggled(self, checked: bool):
        if not checked:
            self._satellite_loop_timer.stop()
            self.satellite_controls.stop_loop()
            self.map_widget.set_satellite_visible(False)
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
        else:
            # Clear the previous mode's frame so CONUS doesn't linger
            # while waiting on the first MESO frame.
            self.map_widget.clear_satellite_frame()
            self.map_widget.set_satellite_visible(False)
            # Backfill recent frames on first select so loop playback works immediately.
            self._satellite_fetcher.fetch_history(mode, 10)

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
        w, s, e, n = frame.bbox
        self.map_widget.set_satellite_frame(frame.b64, w, s, e, n)

    def _on_meso_sectors_updated(self, sectors: dict):
        for idx in (1, 2):
            bbox = sectors.get(idx)
            self.satellite_controls.set_meso_available(idx, bbox is not None, bbox)
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

    def _on_hazard_connectivity(self, online: bool):
        self.hazard_indicator.setVisible(not online)
        self._layout_overlays()

    def _on_spc_received(self, cat_str: str, wind_str: str, hail_str: str, tor_str: str):
        self.map_widget.set_spc_geojson(cat_str, wind_str, hail_str, tor_str)

    def _on_nws_received(self, warnings_str: str):
        self.map_widget.set_nws_warnings_geojson(warnings_str)

    def _on_spc_watches_received(self, watches_str: str):
        self.map_widget.set_spc_watches_geojson(watches_str)

    def _on_spc_mds_received(self, mds_str: str):
        self.map_widget.set_spc_mds_geojson(mds_str)

    def _on_spc_mds_toggled(self, enabled: bool):
        self._hazard_fetcher.set_spc_mds_enabled(enabled)
        self.map_widget.set_spc_mds_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_mds_fresh():
                self._hazard_fetcher.emit_cached_mds()
            else:
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
            import re as _re
            name = str(props.get("name", "")).strip()
            # name may be "MD 0176", "MCD 0176", "MCD0176", "176", etc.
            # Robustly extract the numeric portion regardless of prefix.
            _m = _re.search(r'\d+', name)
            num = _m.group().zfill(4) if _m else "0000"
            title = f"MESOSCALE DISCUSSION {num}"
            kind, identifier = "mcd", num
        elif source == "spc-watches":
            watch_num = str(props.get("watch_num", "")).strip()
            if not watch_num:
                return
            event_label = str(props.get("event", "Watch")).upper()
            title = f"{event_label} {watch_num}"
            kind, identifier = "watch", watch_num
        elif source == "nws-warnings":
            warning_url = str(props.get("warning_url", "")).strip()
            if not warning_url:
                return
            prod_type = str(props.get("prod_type", "Warning")).title()
            wfo = str(props.get("wfo", "")).strip()
            title = f"{prod_type} — {wfo}" if wfo else prod_type
            kind, identifier = "warning", warning_url
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
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return raw.strip("\x01\x02\x03\r\n").strip()

        try:
            if kind == "swo":
                url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SWODY1&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "mcd":
                url = f"https://www.spc.noaa.gov/products/md/md{identifier}.txt"
                text = _fetch(url)
            elif kind == "watch":
                # SEL PIL cycles on the last digit of the watch number.
                # e.g. watch 0029 → SEL9, watch 0028 → SEL8.
                sel_digit = str(int(identifier) % 10)
                url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SEL{sel_digit}&limit=1&fmt=text"
                text = _fetch(url)
            elif kind == "warning":
                if not identifier:
                    text = "(No warning URL available)"
                else:
                    import json as _json
                    raw_json = _json.loads(_fetch(identifier))
                    wp = raw_json.get("properties", {})
                    headline = wp.get("headline", "")
                    description = wp.get("description", "")
                    instruction = wp.get("instruction", "")
                    text = "\n\n".join(x for x in [headline, description, instruction] if x)
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
            needs_refresh = False
            if mode == "outlook":
                needs_refresh = not self._hazard_fetcher.spc_category_cached()
            elif mode in ("tor", "wind", "hail"):
                needs_refresh = not self._hazard_fetcher.spc_product_cached(mode)
            if needs_refresh:
                self._hazard_fetcher.force_spc_refresh()
                self._hazard_fetcher.fetch_now()
            elif self._hazard_fetcher.is_spc_fresh():
                self._hazard_fetcher.emit_cached_spc()
            else:
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_spc_watches_toggled(self, enabled: bool):
        self._hazard_fetcher.set_spc_watches_enabled(enabled)
        self.map_widget.set_spc_watches_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_watches_fresh():
                self._hazard_fetcher.emit_cached_watches()
            else:
                self._hazard_fetcher.fetch_now()
        self._update_hazard_legend()

    def _on_nws_warnings_toggled(self, enabled: bool):
        self._hazard_fetcher.set_nws_enabled(enabled)
        self.map_widget.set_nws_warnings_visible(enabled)
        if enabled:
            if self._hazard_fetcher.is_nws_fresh():
                self._hazard_fetcher.emit_cached_nws()
            else:
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
        # If this machine is producing local data (not in monitor mode),
        # prefer the local stream for its own vehicle ID.
        if not self._monitor and obs.vehicle_id == config.VEHICLE_ID:
            return
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
            self._vehicle_placeholder.setVisible(False)
        self._refresh_vehicle_panel()
        self._station_layer.update(obs.vehicle_id, obs.lat, obs.lon, obs)
        self._refresh_vehicle_detail()

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
        self._last_selected_vehicle_id = vid
        v = self._vehicles.get(vid)
        if v is not None:
            self.map_widget.fly_to(v.lat, v.lon, zoom=13)
        self._refresh_vehicle_panel()
        self._refresh_vehicle_detail()
        self._sync_vehicle_detail_visibility()
        self._layout_overlays()

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

        # Top row: badge · name · age · lat/lon
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
