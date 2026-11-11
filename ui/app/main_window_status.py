"""Status pill, network and GPS indicators, update checker and MQTT connection."""

import logging
import threading
import time
import runtime_flags
import config
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
from PyQt6.QtCore import Qt, QTimer
from ui.theme import set_pill_status
from data.update_checker import UpdateWorker
from network.mqtt_client import MQTTClient
from network.storm_cone_sync import StormConeSync
from network.scan_sector_sync import ScanSectorSync
from network.vehicle_sync import VehicleSync
from ui.app.main_window_util import _NetChecker

log = logging.getLogger("ui.app.main_window")


class MainWindowStatusMixin:

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

        # shown while the radar or satellite loop is off the live frame; click for live
        self.loop_chip = QPushButton("")
        self.loop_chip.setObjectName("loopChip")
        self.loop_chip.setFlat(True)
        self.loop_chip.setProperty("pillRole", "badge")
        set_pill_status(self.loop_chip, "warn")
        self.loop_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.loop_chip.setToolTip("Map is showing a past loop frame. Click to return to live")
        self.loop_chip.setVisible(False)
        row2.addWidget(self.loop_chip)

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
