"""ARCHIVE mode: startup, archive fetchers and playback-driven slots."""

import logging
import runtime_flags
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from ui.map.radar_overlay import RadarOverlay
from data.stations.sounding_stations import build_stations_geojson

log = logging.getLogger("ui.app.main_window")


class MainWindowArchiveMixin:

    def _begin_archive_startup(self):
        """Called once the map is ready in archive mode."""
        from archive.session import ArchiveSession
        from archive.time_controller import TimeController
        from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher
        from archive.fetchers.satellite_archive_fetcher import ArchiveSatelliteFetcher
        from archive.fetchers.hazard_archive_fetcher import ArchiveHazardFetcher
        from archive.fetchers.sounding_archive_fetcher import ArchiveSoundingFetcher
        from archive.fetchers.mqtt_reader import ArchiveMQTTReader
        from ui.controls.archive_controls import ArchiveControls
        from ui.dialogs.archive_loading_dialog import ArchiveLoadingDialog

        self._archive_session = ArchiveSession(start_time=self._archive_time)

        # time controller — central archive clock.
        self._time_ctrl = TimeController(start_time=self._archive_time, parent=self)
        # data age in archive mode is measured against playback time
        self._layer_status.set_clock(lambda: self._time_ctrl.current_time, archive=True)
        self._time_ctrl.time_changed.connect(lambda _t: self._layer_status.refresh())

        # archive controls bar at bottom of screen.
        self._archive_controls = ArchiveControls(self._time_ctrl, self._map_container)
        self._archive_controls.setObjectName("archiveControls")
        self._archive_controls.set_radar_status("Radar: waiting")
        self._archive_controls.set_satellite_status("Sat: waiting")
        self._archive_controls.set_obs_status(
            "OBS: probing" if runtime_flags.FLAGS.admin_mode else "OBS: MQTT"
        )
        self._archive_controls.show()

        # mqtt reader — vehicles, annotations, cones, drawings.
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
        self._archive_mqtt.scan_sector_received.connect(self._recv_remote_scan_sector)
        self._archive_mqtt.scan_sectors_cleared.connect(self._clear_archive_scan_sectors)
        self._archive_vehicle_obs = None
        self._archive_vehicle_obs_started = False

        # hazard fetcher.
        self._archive_hazard = ArchiveHazardFetcher(
            session_date=self._archive_time, parent=self
        )
        self._archive_hazard.spc_received.connect(self._on_spc_received)
        self._archive_hazard.nws_received.connect(self._on_nws_received)
        self._archive_hazard.watches_received.connect(self._on_spc_watches_received)
        self._archive_hazard.spc_mds_received.connect(self._on_spc_mds_received)

        # satellite fetcher.
        self._archive_satellite = ArchiveSatelliteFetcher(
            session_date=self._archive_time, parent=self
        )
        self._archive_satellite.frame_ready.connect(self._on_archive_satellite_frame)
        self._archive_satellite.meso_sectors_updated.connect(self._on_meso_sectors_updated)

        # sounding dialog (built on first use) and archive fetcher (on-demand).
        self._sounding_dialog_obj = None
        self._archive_sounding = ArchiveSoundingFetcher(parent=self)
        self._archive_sounding.sounding_ready.connect(self._on_sounding_ready)
        self._archive_sounding.fetch_error.connect(
            lambda msg: self.status_msg_label.setText(f"Sounding: {msg}")
        )
        # also initialise the sounding-station layer so the map shows clickable sites.
        self._sounding_stations_geojson = build_stations_geojson()

        # radar overlay (reuses existing renderer).
        self._radar_overlay = RadarOverlay(self.map_widget)

        # radar fetcher — created once the station is known.
        self._archive_radar: "ArchiveRadarFetcher | None" = None

        # single-worker pool for archive-mode renders — keeps the PNG render
        # (numpy/scipy work) off the UI thread during archive playback. Set
        # up here (not in _init_radar, which is skipped when radar is
        # disabled) since archive mode doesn't depend on the live radar path.
        from concurrent.futures import ThreadPoolExecutor
        self._archive_render_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="archive-radar-render"
        )
        # incremented on station change / new scan so stale in-flight renders are discarded
        self._archive_render_generation = 0
        self._archive_pending_render_scan = None
        self._archive_render_in_flight = False
        self._archive_render_ready.connect(self._on_archive_render_ready)

        # wire time controller to archive fetchers.
        self._time_ctrl.time_changed.connect(self._archive_mqtt.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_hazard.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_satellite.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_sounding.on_time_changed)

        # show loading dialog while initial data fetches run.
        loading_tasks = ["Vehicle tracks", "Radar index", "Hazard data", "Satellite index"]
        self._archive_loading = ArchiveLoadingDialog(
            session_label=self._archive_time.strftime("%Y-%m-%d  %H:%M UTC"),
            tasks=loading_tasks,
            parent=self,
        )

        # start background fetches; mark tasks done via callbacks.
        self._archive_mqtt.load()
        self._archive_hazard.load_day_data()
        self._archive_satellite.load_capabilities()

        # track completion of loading tasks.
        def _check_mqtt_loaded():
            if self._archive_mqtt._loaded:
                self._archive_loading.set_task_done("Vehicle tracks")
                self._start_archive_vehicle_obs()
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
        # trigger an initial data load for all fetchers once loading completes.
        self._archive_loading.accepted.connect(
            lambda: self._time_ctrl.set_time(self._time_ctrl.current_time)
        )
        self._archive_loading.rejected.connect(self._on_archive_loading_aborted)

        self.hazard_controls.spc_day_changed.connect(self._on_archive_spc_day_changed)
        self.hazard_controls.spc_mode_changed.connect(self._on_archive_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_archive_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_archive_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_archive_nws_toggled)
        self.hazard_controls.fetch_requested.connect(self._archive_hazard.refresh_now)
        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self.satellite_controls.configure_for_archive(True)
        self.satellite_controls.mode_changed.connect(self._on_satellite_mode_changed)
        self.satellite_controls.opacity_changed.connect(self.map_widget.set_satellite_opacity)
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        self.map_widget.sounding_clicked.connect(self._on_archive_sounding_map_click)
        self.map_widget.obs_sounding_station_clicked.connect(self._on_archive_obs_station_click)
        # mode_changed is normally connected in _init_soundings (not called in archive).
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)

        self.radar_controls.configure_for_archive(True)
        self.radar_controls.product_changed.connect(self._on_archive_product_changed)
        self.radar_controls.tilt_changed.connect(self._on_archive_tilt_changed)

        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)

        QTimer.singleShot(200, lambda: self._time_ctrl.set_time(self._archive_time))

        # lay out the archive controls bar at the bottom of the screen.
        QTimer.singleShot(0, self._layout_overlays)

    def _start_archive_vehicle_obs(self) -> None:
        """Start the optional admin one-second observation source."""
        if (
            not runtime_flags.FLAGS.admin_mode
            or self._archive_vehicle_obs_started
            or not hasattr(self, "_archive_mqtt")
        ):
            return
        self._archive_vehicle_obs_started = True

        vehicles = self._archive_mqtt.vehicle_metadata()
        if not vehicles:
            self._archive_controls.set_obs_status("OBS: MQTT fallback")
            return

        from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher

        self._archive_vehicle_obs = ArchiveVehicleObsFetcher(
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_vehicle_obs.observation_ready.connect(
            self._on_archive_dense_vehicle_position
        )
        self._archive_vehicle_obs.load_finished.connect(
            self._on_archive_vehicle_obs_loaded
        )
        self._archive_vehicle_obs.error.connect(
            lambda msg: log.warning("One-second archive unavailable: %s", msg)
        )
        # MQTT is connected first so its rewind clear/fallback pass happens before
        # dense observations overlay the vehicles they cover.
        self._time_ctrl.time_changed.connect(
            self._archive_vehicle_obs.on_time_changed
        )
        self._archive_vehicle_obs.load(vehicles)

    def _on_archive_vehicle_obs_loaded(self, vehicle_ids: set[str]) -> None:
        total = len(self._archive_mqtt.vehicle_metadata())
        if not vehicle_ids:
            self._archive_controls.set_obs_status("OBS: MQTT fallback")
            return

        self._archive_controls.set_precision_mode(True)
        if len(vehicle_ids) == total:
            status = f"OBS: 1-second ({len(vehicle_ids)})"
        else:
            status = f"OBS: partial {len(vehicle_ids)}/{total}"
        self._archive_controls.set_obs_status(status, active=True)
        self._archive_vehicle_obs.on_time_changed(self._time_ctrl.current_time)
        self._layout_overlays()

    def _start_archive_radar(self, station: str) -> None:
        """Instantiate and wire an ArchiveRadarFetcher for the given station."""
        from archive.fetchers.radar_archive_fetcher import ArchiveRadarFetcher

        # discard any in-flight/pending render for the old station
        self._archive_render_generation += 1
        self._archive_pending_render_scan = None
        self._archive_render_in_flight = False

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

    def _on_archive_radar_scan(self, scan) -> None:
        """Handle a newly decoded Level-2 scan from the archive fetcher.

        Updates tilt/product selectors immediately (cheap, UI-thread) and
        hands the PNG render (numpy/scipy work) off to a background thread
        so archive playback doesn't stall the UI on every frame.
        """
        self._current_radar_scan = scan

        # add tilt/product selectors to archive controls the first time;
        if hasattr(scan, "available_products"):
            from core.level2_radar_scan import L2_PRODUCTS
            products = [
                (f, L2_PRODUCTS[f]["label"]) for f in scan.available_products
                if f in L2_PRODUCTS
            ]
            self.radar_controls.set_archive_products(products)
            current_tilt_idx = getattr(self._archive_radar, "_tilt_idx", 0)
            self.radar_controls.set_archive_tilts(scan.available_tilts, current_tilt_idx)

        self._archive_pending_render_scan = scan
        self._submit_pending_archive_render()

    def _submit_pending_archive_render(self) -> None:
        """Submit a background render for the latest pending archive scan.
        No-op if a render is already in flight — _on_archive_render_ready
        will submit the newest pending scan when the current render completes."""
        scan = self._archive_pending_render_scan
        if scan is None or self._archive_render_in_flight:
            return
        self._archive_pending_render_scan = None
        self._archive_render_in_flight = True
        gen = self._archive_render_generation
        from ui.map.radar_overlay import RENDER_GRID_SIZE
        archive_grid = max(RENDER_GRID_SIZE, 768)
        self._archive_render_executor.submit(self._bg_render_archive, gen, scan, archive_grid)

    def _bg_render_archive(self, gen: int, scan, grid_size: int) -> None:
        """Runs in the archive-render thread pool — NOT on the main thread.
        Renders scan to PNG then emits _archive_render_ready (auto-queued to main thread)."""
        if gen != self._archive_render_generation:
            return
        from ui.map.radar_overlay import render_scan_to_png
        try:
            png, bounds, _ = render_scan_to_png(scan, grid_size)
        except Exception as exc:
            log.error("Archive radar render failed: %s", exc)
            self._archive_render_ready.emit({"gen": gen, "scan": scan, "error": str(exc)})
            return
        if gen != self._archive_render_generation:
            return
        self._archive_render_ready.emit({
            "gen": gen, "scan": scan, "png": png, "bounds": bounds,
        })

    def _on_archive_render_ready(self, result: dict) -> None:
        """Runs on the main thread — injects the pre-rendered archive PNG into the map."""
        self._archive_render_in_flight = False
        if result["gen"] == self._archive_render_generation:
            scan = result["scan"]
            if "error" in result:
                self._layer_status["radar"].mark_error(f"render error: {result['error']}")
                if hasattr(self, "_archive_controls"):
                    self._archive_controls.set_radar_status("Radar: render error", error=True)
            else:
                self._radar_overlay.inject(result["png"], result["bounds"])
                self._layer_status["radar"].mark_success(scan.scan_time)
                if hasattr(self, "radar_controls"):
                    self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"), scan.scan_time)
                if hasattr(self, "_archive_controls"):
                    self._archive_controls.set_radar_status(
                        f"Radar: {scan.pyart_field} {scan.tilt_deg:.1f}deg {scan.scan_time.strftime('%H:%MZ')}"
                    )
        if self._archive_pending_render_scan is not None:
            self._submit_pending_archive_render()

    def _on_archive_satellite_frame(self, frame) -> None:
        """Update the archive satellite frame; only show if the user has toggled it on."""
        w, s, e, n = frame.bbox
        self.map_widget.set_satellite_frame(frame.mode, frame.frame_id, frame.png, w, s, e, n)
        self._archive_sat_has_data = True
        self._layer_status["satellite"].mark_success(frame.timestamp)
        self.satellite_controls.set_scan_time(frame.time_str, frame.timestamp)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status(
                f"Sat: AWS {frame.mode.upper()} {frame.time_str}"
            )
        self._layout_overlays()
        if self.btn_satellite.isChecked():
            self.map_widget.set_satellite_visible(True)

    def _on_archive_vehicle_position(self, obs) -> None:
        """Update a vehicle marker from the MQTT archive."""
        dense_obs = getattr(self, "_archive_vehicle_obs", None)
        if (
            dense_obs is not None
            and dense_obs.has_fresh_observation(
                obs.vehicle_id,
                self._time_ctrl.current_time,
            )
        ):
            return
        self.update_vehicle_obs(obs)
        
        # also update timeseries dialog if open
        if (self._vehicle_timeseries_dlg is not None
                and self._vehicle_timeseries_dlg.isVisible()):
            observations = self._get_archive_vehicle_history(obs.vehicle_id)
            if observations:
                self._vehicle_timeseries_dlg.update_vehicle(
                    obs.vehicle_id, observations)

    def _on_archive_dense_vehicle_position(self, obs) -> None:
        """Update a vehicle from the optional one-second archive."""
        if not self._archive_vehicle_obs.has_fresh_observation(
            obs.vehicle_id,
            self._time_ctrl.current_time,
        ):
            return
        self.update_vehicle_obs(obs)
        if (
            self._vehicle_timeseries_dlg is not None
            and self._vehicle_timeseries_dlg.isVisible()
        ):
            observations = self._get_archive_vehicle_history(obs.vehicle_id)
            if observations:
                self._vehicle_timeseries_dlg.update_vehicle(
                    obs.vehicle_id,
                    observations,
                )

    def _on_archive_vehicles_cleared(self) -> None:
        """Remove all vehicle markers when time jumps backward."""
        for vid in list(self._vehicles.keys()):
            self.map_widget.remove_vehicle(vid)
        self._vehicles.clear()
        self.update_vehicle_count(0)
        self._sync_routing_vehicle_snapshot()

    def _clear_archive_scan_sectors(self) -> None:
        """Remove scan-sector overlays when archive time jumps backward."""
        self._scan_sectors.clear()
        self._refresh_scan_sectors()

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
        self._layer_status["radar"].mark_error(msg)

    def _on_archive_loading_aborted(self) -> None:
        """User clicked Abort on the loading dialog — quit the application."""
        self.session_aborted.emit()
        QApplication.quit()

    def _on_archive_satellite_error(self, msg: str) -> None:
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status("Sat: error", error=True)
        self._layer_status["satellite"].mark_error(msg)

    def _on_archive_spc_mode_changed(self, mode: str) -> None:
        """Toggle SPC outlook/product layers in archive mode (no live fetcher)."""
        outlook_on = mode == "outlook"
        for key in ("MRGL", "SLGHT", "ENH", "MDT", "HIGH"):
            self.map_widget.set_spc_category_visible(key, outlook_on)
        for key in ("tor", "wind", "hail", "prob", "sig"):
            on = mode == key
            self.map_widget.set_spc_product_visible(key, on)
        self._update_hazard_legend()

    def _on_archive_spc_day_changed(self, day: int) -> None:
        self._archive_hazard.set_spc_day(day)
        empty = '{"type":"FeatureCollection","features":[]}'
        self.map_widget.set_spc_geojson(empty, empty, empty, empty, empty, empty)
        self._on_archive_spc_mode_changed(self.hazard_controls._active_spc_mode())

    def _on_archive_watches_toggled(self, enabled: bool) -> None:
        self.map_widget.set_spc_watches_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_nws_toggled(self, enabled: bool) -> None:
        self.map_widget.set_nws_warnings_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_mds_toggled(self, enabled: bool) -> None:
        self.map_widget.set_spc_mds_visible(enabled)
        self._update_hazard_legend()

    def _on_archive_sounding_map_click(self, lat: float, lon: float) -> None:
        self.status_msg_label.setText("Fetching archive sounding…")
        self._archive_sounding.fetch_model_sounding(lat, lon)

    def _on_archive_obs_station_click(
        self, station_id: str, name: str, lat: float, lon: float, elev: float
    ) -> None:
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._archive_sounding.fetch_obs_sounding(station_id, lat, lon, elev)
