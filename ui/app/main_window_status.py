"""Status pill, network and GPS indicators, update checker and MQTT connection."""

import logging
import threading
import time
from dataclasses import replace
import runtime_flags
import config
import settings
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
from PyQt6.QtCore import Qt, QTimer, QSettings
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
        # in-ops update indicator — hidden until an update is detected
        self.update_indicator = QPushButton("↑ UPDATE AVAILABLE")
        self.update_indicator.setFlat(True)
        self.update_indicator.setProperty("pillRole", "action")
        set_pill_status(self.update_indicator, "accent")
        self.update_indicator.setToolTip("Click to apply update and restart STORM")
        self.update_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_indicator.setVisible(False)
        self.update_indicator.clicked.connect(self._on_update_indicator_clicked)

        # shown while the radar or satellite loop is off the live frame; click for live
        self.loop_chip = QPushButton("")
        self.loop_chip.setObjectName("loopChip")
        self.loop_chip.setFlat(True)
        self.loop_chip.setProperty("pillRole", "badge")
        set_pill_status(self.loop_chip, "warn")
        self.loop_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.loop_chip.setToolTip("Map is showing a past loop frame. Click to return to live")
        self.loop_chip.setVisible(False)

        # gps fix status — vehicle mode only (hidden in monitor/viewer/archive)
        self.gps_indicator = QLabel("● NO GPS FIX")
        self.gps_indicator.setProperty("pillRole", "badge")
        set_pill_status(self.gps_indicator, "error")
        show_gps = not (self._monitor or self._viewer or self._archive)
        self.gps_indicator.setVisible(show_gps)

        self.conn_indicator = QLabel("● MQTT OFFLINE")
        self.conn_indicator.setProperty("pillRole", "badge")
        set_pill_status(self.conn_indicator, "error")
        self._mqtt_connected = False

        self.date_label = QLabel("-- --- ----")
        self.clock_label = QLabel("--:--:-- UTC")
        self.clock_label.setProperty("pillRole", "clock")

        # keep hazard_indicator as a hidden member so existing callers don't break
        self.hazard_indicator = QLabel("● DATA OFFLINE")
        self.hazard_indicator.setVisible(False)

        if self._archive:
            self._init_archive_status()
        else:
            self._init_status_pill(show_gps)

        from ui.map.html import basemap_source
        if basemap_source() == "online":
            self.status_msg_label.setText("Map: online tiles (no tiles/storm.mbtiles)")
            self.status_msg_label.setToolTip("The base map loads from OpenFreeMap while connected. "
                                             "Put storm.mbtiles in the tiles folder to use it offline.")

    def _init_archive_status(self):
        """ARCHIVE: a coordinates chip and a status popover in place of the
        pill (ui/widgets/archive_status.py). GPS, MQTT and the wall clock mean
        nothing on a replay, so those members stay unparented and unshown."""
        from ui.widgets.archive_status import ArchiveStatus
        self._archive_status = ArchiveStatus(config.VERSION, self._map_container)
        self._archive_status.layout_requested.connect(self._layout_overlays)
        pop = self._archive_status.popover
        self._status_left = self._archive_status.chip
        self.coord_label = self._status_left.coord_label
        self.status_msg_label = pop.status_msg_label
        self.vehicle_count_label = pop.vehicle_count_label
        self.net_indicator = pop.net_indicator
        pop.loop_slot.insertWidget(1, self.loop_chip)
        pop.update_slot.addWidget(self.update_indicator)

    def _init_status_pill(self, show_gps: bool):
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

        # mode badge: VEHICLE / MONITOR / VIEWER — always leftmost in row 1
        if self._monitor:
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

        row2.addWidget(self.gps_indicator)
        if show_gps:
            row2.addWidget(self._status_divider())
        row2.addWidget(self.conn_indicator)
        row2.addWidget(self._status_divider())

        self.net_indicator = QLabel("")
        self.net_indicator.setProperty("pillRole", "badge")
        set_pill_status(self.net_indicator, "off")
        self.net_indicator.setVisible(False)
        row2.addWidget(self.net_indicator)

        row2.addStretch()
        row2.addWidget(self.loop_chip)
        row2.addWidget(self.date_label)
        row2.addWidget(self._status_divider())
        row2.addWidget(self.clock_label)

        pill.addLayout(row0)
        pill.addLayout(row1)
        pill.addLayout(row2)

    def update_coordinates(self, lat: float, lon: float):
        if self._archive:
            from ui.widgets.archive_status import format_coordinates
            self.coord_label.setText(format_coordinates(lat, lon))
            return
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
        self._net_state = state
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
        if hasattr(self, "_archive_status"):
            self._archive_status.set_net_state(state)
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
        self._mqtt_connected = connected
        if connected:
            self.conn_indicator.setText("● MQTT OK")
            set_pill_status(self.conn_indicator, "ok")
        else:
            self.conn_indicator.setText("● MQTT OFFLINE")
            set_pill_status(self.conn_indicator, "error")

    def _init_mqtt(self):
        # VIEWER uses [mqtt.viewer] (or the [mqtt] login) and never publishes
        self._mqtt_settings = settings.SETTINGS.mqtt_for(viewer=self._viewer)
        broker = self._mqtt_settings
        self._mqtt_client = MQTTClient(client_id=broker.client_id(config.VEHICLE_ID),
                                       read_only=self._viewer or broker.read_only,
                                       topic_root=broker.topic_root,
                                       daily_reset_utc=broker.daily_reset_utc, parent=self)
        self._mqtt_client.connected.connect(self._on_mqtt_connected)
        self._mqtt_client.disconnected.connect(self._on_mqtt_disconnected)
        self._mqtt_client.connect_failed.connect(self._on_mqtt_connect_failed)
        self._mqtt_client.subscribe_refused.connect(self._on_mqtt_subscribe_refused)

        self._vehicle_sync = VehicleSync(self._mqtt_client, parent=self)
        self._vehicle_sync.vehicle_received.connect(self._on_remote_vehicle_obs)
        self._storm_cone_sync = StormConeSync(self._mqtt_client, read_only=self._viewer, parent=self)
        self._scan_sector_sync = ScanSectorSync(self._mqtt_client, read_only=self._viewer, parent=self)
        self._scan_sector_sync.scan_received.connect(self._recv_remote_scan_sector)

        # connect after a short delay so the window is fully painted first
        if broker.configured:
            QTimer.singleShot(500, self._mqtt_connect)
        else:
            log.info("MQTT host not configured (storm.toml) — running offline")
            self.conn_indicator.setText("● NO BROKER")
            set_pill_status(self.conn_indicator, "off")
            self.conn_indicator.setToolTip("No [mqtt] host in storm.toml: no live vehicles, "
                                           "annotations or drawings. See https://bobbysaba.com/docs/storm/ref/configuration/.")
        if self._monitor and broker.configured:
            self._apply_recording(self._record_action.isChecked())

    # -- recording broker traffic (MONITOR) ---------------------------------
    def _set_recording(self, on: bool):
        """File > Record Broker Traffic: write what the broker sends to
        [archive] recordings for ARCHIVE to replay (archive/recorder.py).
        Remembered for the next MONITOR launch."""
        QSettings().setValue("monitor/record", on)
        self._apply_recording(on)

    def _apply_recording(self, on: bool):
        client = getattr(self, "_mqtt_client", None)
        recorder = getattr(self, "_recorder", None)
        if client is None:
            return      # MQTT starts later; _init_mqtt applies the menu's state
        if on and recorder is None:
            from archive.recorder import Recorder
            folder = settings.SETTINGS.archive.recordings
            self._recorder = Recorder(folder, client.topic_root)
            client.add_tap(self._recorder.handle)
            self.status_msg_label.setText(f"Recording broker traffic to {folder}")
            log.info("recording broker traffic to %s", folder)
        elif not on and recorder is not None:
            client.remove_tap(recorder.handle)
            recorder.close()
            self._recorder = None
            self.status_msg_label.setText("Stopped recording broker traffic")
            log.info("stopped recording after %d messages", recorder.count)

    def _mqtt_connect(self):
        broker = self._mqtt_settings
        if runtime_flags.FLAGS.mqtt_no_tls:
            log.warning("MQTT TLS disabled via --mqtt-no-tls (diagnostic mode)")
            broker = replace(broker, tls="none")
        self._mqtt_client.connect_to_broker(broker)

    def _on_mqtt_connected(self):
        self.set_connection_status(True)
        if self._startup_mqtt_pending:
            self._mqtt_startup_timer.start(1500)
        if self.status_msg_label.text().startswith("MQTT:"):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _on_mqtt_connect_failed(self, reason: str):
        """The broker couldn't be reached or TLS failed (STORM keeps retrying)."""
        self.set_connection_status(False)
        if self._startup_mqtt_pending:
            self._complete_mqtt_startup_phase()
        self.status_msg_label.setText(f"MQTT: offline: {reason}")
        self.conn_indicator.setToolTip(f"Can't connect to the broker: {reason}. "
                                       "STORM keeps trying; https://bobbysaba.com/docs/storm/team/broker/ has fixes.")
        self._layout_overlays()

    def _on_mqtt_subscribe_refused(self, topic: str, reason: str):
        """Connected, but the login may not read these topics: without this the
        map just stays empty."""
        root = self._mqtt_settings.topic_root
        self.status_msg_label.setText(
            f"MQTT: the broker won't send {topic} ({reason.lower()}): this login may not use "
            f"topic_root \"{root}\"; check topic_root and the broker's access list")
        self._layout_overlays()

    def _on_mqtt_disconnected(self, code: int):
        self.set_connection_status(False)
        if self._startup_mqtt_pending:
            self._complete_mqtt_startup_phase()
        code_map = {
            -1: "setup error (cert/key/path)",
            7: "connection lost",
            128: "connection dropped (the broker restarted or the network went away; retrying)",
            129: "malformed packet",
            130: "protocol error",
            131: "implementation-specific error",
            132: "unsupported protocol version",
            133: "client ID invalid",
            134: "bad username/password",
            135: "not authorized (wrong login, or it may not use these topics)",
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
