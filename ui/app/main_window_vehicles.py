"""Vehicles: obs ingest, the vehicle panel, follow mode, scan sectors and timeseries."""

import logging
import csv
import time
import runtime_flags
import config
from datetime import datetime, timezone
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QToolButton, QFrame, QCheckBox, QPushButton,
    QGridLayout, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QStandardPaths
from ui.controls.routing_controls import _make_loc_icon
from archive.vehicle_speed import calculate_vehicle_speed, format_vehicle_speed
from ui.dialogs.scan_sector_dialog import ScanSectorDialog
from core.scan_sector import ScanSector, feature_collection
from core.observation import Observation
from core.vehicle_history import VehicleHistoryStore
from core.vehicle import Vehicle
from data.ingest.gps_reader import GPSReader
from data.ingest.obs_file_watcher import ObsFileWatcher, FieldMap
from ui.layers.station_plot_layer import StationPlotLayer
from ui import palette as P
from ui.app.main_window_util import (
    SCAN_MOVE_FIXES_TO_STOP, SCAN_MOVE_THRESHOLD_KM, VEHICLE_PANEL_WIDTH, _clear_layout,
)

log = logging.getLogger("ui.app.main_window")


class MainWindowVehiclesMixin:

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

        # archive: observation trails are the bottom of this same panel
        if self._archive:
            self._init_trail_controls(layout)

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
        self._later(1200, lambda: self._station_layer.set_visible(
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
            # ranked points, for the command palette's "nearest deployment location"
            self._deploy_points = [p for p in points if p["rank_abi"] is not None]
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
        if obs.timestamp is not None:
            v.track.append(obs.timestamp.timestamp(), obs.lat, obs.lon)
        loop_active = self._vehicle_loop_active()
        
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
        if loop_active:
            # the marker shows the loop position; it returns to live when the loop stops
            self._apply_vehicle_loop_time()
        else:
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
        if self._follow_mode and not loop_active and obs.vehicle_id == self._follow_target_id():
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

        surface = getattr(self, "_archive_clamps_surface", None)
        if surface is not None and vehicle_id in surface.rows:
            return surface.history(vehicle_id, self._time_ctrl.current_time)
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
