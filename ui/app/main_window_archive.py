"""ARCHIVE mode: startup, archive fetchers and playback-driven slots."""

import logging
from datetime import timedelta
from pathlib import Path

from PyQt6.QtCore import QTimer
from ui.map.radar_overlay import RadarOverlay
from data.stations.sounding_stations import build_stations_geojson

log = logging.getLogger("ui.app.main_window")


def _velocity_status_tag(scan) -> str:
    """Note for the archive bar's radar status on processed velocity."""
    notes = getattr(scan, "velocity_processing", "") or ""
    if notes.startswith("dealiased"):
        return " · dealiased"
    if notes.startswith("raw:"):
        return " · not dealiased"
    return ""


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
        from ui.widgets.archive_loading_indicator import ArchiveLoadingIndicator

        self._archive_session = ArchiveSession(start_time=self._archive_time)

        # time controller — central archive clock.
        self._time_ctrl = TimeController(start_time=self._archive_time, parent=self)
        # data age in archive mode is measured against playback time
        self._layer_status.set_clock(lambda: self._time_ctrl.current_time, archive=True)
        self._time_ctrl.time_changed.connect(lambda _t: self._layer_status.refresh())
        if hasattr(self, "btn_track"):
            self._time_ctrl.time_changed.connect(self._on_track_time_changed)
            self._open_session_track()
        if hasattr(self, "trail_controls"):
            self._time_ctrl.time_changed.connect(self._schedule_trails)

        # archive controls bar at bottom of screen.
        self._archive_controls = ArchiveControls(self._time_ctrl, self._map_container)
        self._archive_controls.setObjectName("archiveControls")
        self._archive_controls.set_radar_status("Radar: waiting")
        self._archive_controls.set_satellite_status("Sat: off")
        self._archive_controls.set_obs_status("OBS: probing")
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
        self._archive_vehicle_obs_loaded = False
        self._archive_vehicle_obs_from_catalog = False
        # auto-pick waits for one-second positions when MQTT has none
        self._radar_station_awaiting_dense_obs = False
        self._radar_station_provisional = False
        self._radar_station_user_picked = False

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
        self._archive_satellite.unavailable.connect(self._on_archive_satellite_unavailable)

        # sounding dialog (built on first use) and archive fetcher (on-demand).
        self._sounding_dialog_obj = None
        self._archive_sounding = ArchiveSoundingFetcher(parent=self)
        self._archive_sounding.sounding_ready.connect(self._on_sounding_ready)
        self._archive_sounding.fetch_error.connect(self._on_archive_sounding_error)
        # also initialise the sounding-station layer so the map shows clickable sites.
        self._sounding_stations_geojson = build_stations_geojson()

        # ASOS replay and damage paths, both from a box drawn on the map.
        self._init_archive_surface()

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

        # once the clock has been still for 500 ms, the frame on screen is
        # rendered again sharper, cropped around the radar, on its own pool
        # so it never holds up the normal render above
        from ui.app.radar_superres_cache import RadarSuperresCache
        from ui.map.radar_overlay import ARCHIVE_SUPERRES_GRID_SIZE
        self._archive_superres_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="archive-radar-superres"
        )
        self._archive_superres_cache = RadarSuperresCache(capacity=12)
        self._archive_superres_size = ARCHIVE_SUPERRES_GRID_SIZE   # steps down on a slow computer
        self._archive_superres_pending = None    # (generation, station, scan) on screen
        # the scan whose sharp render is on screen, or was skipped for good
        # (low memory, a failed render): a Video Studio frame waits for it
        self._radar_settled_scan = None
        self._archive_superres_busy = False
        self._archive_superres_wall_s = None     # how long the last sharp render took
        self._archive_superres_timer = QTimer(self)
        self._archive_superres_timer.setSingleShot(True)
        self._archive_superres_timer.setInterval(500)
        self._archive_superres_timer.timeout.connect(self._on_archive_superres_timer_fired)
        self._archive_superres_ready.connect(self._on_archive_superres_ready)
        self._time_ctrl.playing_changed.connect(self._on_archive_playing_changed)

        # wire time controller to archive fetchers.
        self._time_ctrl.time_changed.connect(self._archive_mqtt.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_hazard.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_satellite.on_time_changed)
        self._time_ctrl.time_changed.connect(self._archive_sounding.on_time_changed)

        # a small line of text over the map while initial data loads; the
        # session is usable meanwhile. Listed in the order they tend to
        # finish: radar can only start once vehicles have picked a station.
        # Satellite isn't one: it indexes once SATELLITE is turned on.
        loading_tasks = ["hazards", "vehicle tracks", "radar"]
        self._archive_loading = ArchiveLoadingIndicator(
            tasks=loading_tasks,
            parent=self._map_container,
        )

        # start background fetches; mark tasks done via callbacks.
        self._archive_mqtt.load()
        self._archive_hazard.load_day_data()

        # track completion of loading tasks.
        def _check_mqtt_loaded():
            if self._archive_mqtt._loaded:
                self._archive_loading.set_task_done("vehicle tracks")
                self._start_archive_vehicle_obs()
                self._try_auto_select_radar_station()
                self._update_archive_session_end()
                if hasattr(self, "trail_controls"):
                    self._schedule_trails()
            else:
                self._later(500, _check_mqtt_loaded)

        def _check_hazard_loaded():
            if self._archive_hazard._watches_loaded:
                self._archive_loading.set_task_done("hazards")
            else:
                self._later(500, _check_hazard_loaded)

        self._archive_satellite.error.connect(self._on_archive_satellite_error)

        self._later(400, _check_mqtt_loaded)
        self._later(400, _check_hazard_loaded)

        # once startup has finished, nudge every fetcher with the clock again,
        # for any that was wired up after the first time_changed
        self._archive_loading.all_done.connect(self._renudge_archive_clock)

        self.hazard_controls.spc_day_changed.connect(self._on_archive_spc_day_changed)
        self.hazard_controls.spc_mode_changed.connect(self._on_archive_spc_mode_changed)
        self.hazard_controls.spc_watches_toggled.connect(self._on_archive_watches_toggled)
        self.hazard_controls.spc_mds_toggled.connect(self._on_archive_mds_toggled)
        self.hazard_controls.nws_warnings_toggled.connect(self._on_archive_nws_toggled)
        self.hazard_controls.fetch_requested.connect(self._archive_hazard.refresh_now)
        self.map_widget.feature_clicked.connect(self._on_spc_feature_clicked)

        self.satellite_controls.configure_for_archive(True)
        self.satellite_controls.mode_changed.connect(self._on_satellite_mode_changed)
        self.satellite_controls.band_changed.connect(self._on_archive_satellite_band_changed)
        self._archive_satellite.set_band(self.satellite_controls.current_band())
        if self._archive_satellite.unavailable_reason:
            self._on_archive_satellite_unavailable(self._archive_satellite.unavailable_reason)
        elif self.btn_satellite.isChecked():
            self._archive_satellite.set_active(True)
        self.satellite_controls.opacity_changed.connect(self.map_widget.set_satellite_opacity)
        self.satellite_controls.meso_preview.connect(self._on_meso_preview)

        self.map_widget.obs_sounding_station_clicked.connect(self._on_archive_obs_station_click)
        # mode_changed is normally connected in _init_soundings (not called in archive).
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)
        self.sounding_controls.set_hrrr_available(False)
        # CLAMPS: lidar winds (VAD), trailer surface obs, sonde / TROPoe / CopterSonde
        self._init_archive_clamps()
        # Doppler lidar scans (LIDAR)
        self._init_archive_lidar()

        self.radar_controls.configure_for_archive(True)
        self.radar_controls.product_changed.connect(self._on_archive_product_changed)
        self.radar_controls.tilt_changed.connect(self._on_archive_tilt_changed)
        self.radar_controls.dealias_toggled.connect(self._on_archive_dealias_toggled)
        self.radar_controls.archive_data_toggled.connect(self._on_archive_radar_data_toggled)

        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)

        self._later(200, lambda: self._time_ctrl.set_time(self._archive_time))
        if self._case_package_at_start:
            path = Path(self._case_package_at_start)
            self._later(0, lambda: self._load_case_package(path, at_start=True))

        # lay out the archive controls bar at the bottom of the screen.
        self._later(0, self._layout_overlays)

    def _start_archive_vehicle_obs(self) -> None:
        """Start the one-second observation source (FOFS mesonet files)."""
        if (
            self._archive_vehicle_obs_started
            or not hasattr(self, "_archive_mqtt")
        ):
            return
        self._archive_vehicle_obs_started = True

        # Recorded MQTT vehicles carry icon types; the fetcher adds every
        # vehicle THREDDS has files for this day (archive/fofs_index.py), so
        # dates with no MQTT history still get one-second data.
        vehicles = self._archive_mqtt.vehicle_metadata()
        self._archive_vehicle_obs_from_catalog = not vehicles

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
        self._archive_vehicle_obs.error.connect(self._on_archive_vehicle_obs_error)
        # MQTT is connected first so its rewind clear/fallback pass happens before
        # dense observations overlay the vehicles they cover.
        self._time_ctrl.time_changed.connect(
            self._archive_vehicle_obs.on_time_changed
        )
        self._archive_vehicle_obs.load(vehicles)

    def _update_archive_session_end(self) -> None:
        """Size the timeline to the day's activity (archive/session.py) once
        the evidence is in: the recorded MQTT history, plus the one-second
        vehicle tracks whenever those are being loaded -- waiting for both
        so the timeline doesn't shrink and then grow again. With no
        evidence at all the session keeps its full UTC day."""
        from archive.session import activity_end, session_bounds
        if not getattr(self._archive_mqtt, "_loaded", False):
            return
        if self._archive_vehicle_obs_started and not self._archive_vehicle_obs_loaded:
            return
        times = self._archive_mqtt.activity_times()
        if self._archive_vehicle_obs is not None and self._archive_vehicle_obs_loaded:
            times += self._archive_vehicle_obs.activity_times()
        end = activity_end(self._archive_time, times)
        if end is None:
            start, _ = session_bounds(self._archive_time)
            end = start + timedelta(days=1)
        log.info("Archive session runs to %s (%d activity markers)", end.isoformat(), len(times))
        self._time_ctrl.set_window_end(end)

    def _on_archive_vehicle_obs_loaded(self, vehicle_ids: set[str]) -> None:
        self._archive_vehicle_obs_loaded = True
        self._update_archive_session_end()
        if hasattr(self, "trail_controls"):
            self._schedule_trails()
        if self._radar_station_awaiting_dense_obs:
            self._radar_station_awaiting_dense_obs = False
            self._try_auto_select_radar_station()

        total = self._archive_vehicle_obs.roster_size
        if not vehicle_ids:
            self._archive_controls.set_obs_status(
                "OBS: no data for this date"
                if self._archive_vehicle_obs_from_catalog
                else "OBS: MQTT fallback"
            )
            return

        self._archive_controls.set_precision_mode(True)
        source = "catalog" if self._archive_vehicle_obs_from_catalog else "1-second"
        if len(vehicle_ids) == total:
            status = f"OBS: {source} ({len(vehicle_ids)})"
        else:
            status = f"OBS: {source} partial {len(vehicle_ids)}/{total}"
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
        self._archive_superres_timer.stop()
        self._archive_superres_pending = None

        if self._archive_radar is not None:
            for sig in (
                self._archive_radar.scan_ready,
                self._archive_radar.index_loaded,
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
            # frees its parsed volume (~0.5 GB) and stops its workers
            self._archive_radar.shutdown()

        self._archive_radar = ArchiveRadarFetcher(
            station=station,
            session_date=self._archive_time,
            parent=self,
        )
        self._archive_radar.scan_ready.connect(self._on_archive_radar_scan)
        self._archive_radar.index_loaded.connect(self._archive_controls.set_available_scan_times)
        self._archive_controls.set_available_scan_times([])
        self._archive_radar.loading_changed.connect(self._on_archive_radar_loading_changed)
        self._archive_radar.error.connect(self._on_archive_radar_error)
        self._time_ctrl.time_changed.connect(self._archive_radar.on_time_changed)
        self._archive_radar.set_active(self._archive_radar_on())
        self._apply_archive_velocity_options()

        loading = getattr(self, "_archive_loading", None)
        if loading is not None and loading.startup_pending():
            self._archive_radar.index_loaded.connect(self._on_archive_radar_index_loading_status)
            self._archive_radar.scan_ready.connect(self._on_archive_radar_loading_done)
            self._archive_radar.error.connect(self._on_archive_radar_loading_error)
            self._later(45_000, self._on_archive_radar_loading_done)

        if hasattr(self, "radar_controls"):
            self.radar_controls.set_selected_site(station, emit=False)

        self._archive_radar.load_index()
        self._archive_radar.on_time_changed(self._time_ctrl.current_time)

    def _archive_radar_on(self) -> bool:
        return not hasattr(self, "radar_controls") or self.radar_controls.is_data_enabled()

    def _on_archive_radar_data_toggled(self, on: bool) -> None:
        """RADAR's "show on map": off stops the radar fetching and decoding
        and clears it from the map (the station stays picked); on catches up
        with the clock."""
        # drop renders under way, or one would draw after this
        self._archive_render_generation += 1
        self._archive_pending_render_scan = None
        self._archive_superres_timer.stop()
        self._archive_superres_pending = None
        if not on:
            self._radar_overlay.hide(transient=False)
            self._current_radar_scan = None
            self._radar_settled_scan = None
            self.status_msg_label.setText("")
            if hasattr(self, "_archive_controls"):
                self._archive_controls.set_radar_status("Radar: off")
        elif hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status("Radar: loading" if self._archive_radar else "Radar: waiting")
        if self._archive_radar is not None:
            self._archive_radar.set_active(on)

    def _on_archive_radar_scan(self, scan) -> None:
        """Handle a newly decoded Level-2 scan from the archive fetcher.

        Updates tilt/product selectors immediately (cheap, UI-thread) and
        hands the PNG render (numpy/scipy work) off to a background thread
        so archive playback doesn't stall the UI on every frame.
        """
        if not self._archive_radar_on():
            return
        self._current_radar_scan = scan
        if getattr(self, "_package_radar_view", None):
            self._later(0, self._apply_package_radar_view)

        # add tilt/product selectors to archive controls the first time;
        if hasattr(scan, "available_products"):
            from core.level2_radar_scan import L2_PRODUCTS
            products = [
                (f, L2_PRODUCTS[f]["label"]) for f in scan.available_products
                if f in L2_PRODUCTS
            ]
            self.radar_controls.set_archive_products(products)
            current_tilt_idx = getattr(
                scan, "tilt_index", getattr(self._archive_radar, "_tilt_idx", 0)
            )
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
                        f"Radar: {scan.pyart_field} {scan.tilt_deg:.1f}deg "
                        f"{scan.scan_time.strftime('%H:%MZ')}{_velocity_status_tag(scan)}"
                    )
                # (re)start the wait before this frame's sharp render
                self._archive_superres_pending = (
                    self._archive_render_generation, self._archive_radar.station, scan,
                )
                self._archive_superres_timer.start()
        if self._archive_pending_render_scan is not None:
            self._submit_pending_archive_render()

    def _on_archive_superres_timer_fired(self) -> None:
        """The clock has been still for a moment: show the frame on screen
        sharp, from the cache or from a background render."""
        pending = self._archive_superres_pending
        if pending is None or self._archive_radar is None:
            return
        gen, station, scan = pending
        if gen != self._archive_render_generation or station != self._archive_radar.station:
            return
        key = self._archive_superres_cache.make_key(
            station, scan.pyart_field, scan.tilt_deg, scan.scan_time,
            getattr(scan, "velocity_processing", "") or "",
        )
        cached = self._archive_superres_cache.get(key)
        if cached is not None:
            self._radar_overlay.inject(*cached)
            self._radar_settled_scan = scan
            return
        if self._sharp_render_would_be_stale(scan):
            return   # rendered on pause (_on_archive_playing_changed)
        from ui.map.radar_overlay import sharp_render_memory_ok, superres_crop_radius_m
        if not sharp_render_memory_ok():
            log.info("Skipping the sharp radar render: system memory is low")
            self._radar_settled_scan = scan
            return
        self._archive_superres_busy = True
        self._archive_superres_executor.submit(
            self._bg_render_archive_superres, gen, station, scan, key,
            self._archive_superres_size, superres_crop_radius_m(scan),
        )

    def _sharp_render_would_be_stale(self, scan) -> bool:
        """While playing: would the next radar scan reach the clock before a
        sharp render of this one finishes (judged by the last one's time)?
        Its result would be thrown away, and the work only slows the decoding
        playback needs."""
        if not self._time_ctrl.is_playing:
            return False
        if self._archive_superres_busy:
            return True            # don't queue behind one still rendering
        if self._archive_superres_wall_s is None:
            return False
        upcoming = self._archive_radar.next_scan_after(scan.scan_time)
        if upcoming is None:
            return False
        wall_left = (upcoming - self._time_ctrl.current_time).total_seconds() / max(self._time_ctrl.speed, 1)
        return wall_left < self._archive_superres_wall_s

    def _on_archive_playing_changed(self, playing: bool) -> None:
        """Paused: give the frame on screen its sharp render if playback skipped it."""
        if not playing and self._archive_superres_pending is not None:
            self._archive_superres_timer.start()

    def _bg_render_archive_superres(
        self, gen: int, station: str, scan, key: tuple, grid_size: int, crop_radius_m: float,
    ) -> None:
        """Runs in the sharp-render thread pool — NOT on the main thread."""
        import time
        from ui.map.radar_overlay import ensure_pixel_index, next_superres_size, render_scan_to_png
        try:
            # once per station and size (~5 s at 4096 px on an M2), so it's
            # left out of the timing that steps the size down
            ensure_pixel_index(scan, grid_size, crop_radius_m)
            # this thread's CPU time, not wall time: while scans still
            # download and decode, a render also waits on them
            started, started_wall = time.thread_time(), time.perf_counter()
            png, bounds, _ = render_scan_to_png(scan, grid_size, crop_radius_m=crop_radius_m)
        except Exception as exc:
            log.error("Archive radar sharp render failed: %s", exc)
            self._archive_superres_ready.emit({"gen": gen, "station": station, "scan": scan, "error": str(exc)})
            return
        finally:
            self._archive_superres_busy = False
        took = time.thread_time() - started
        self._archive_superres_wall_s = time.perf_counter() - started_wall
        smaller = next_superres_size(grid_size, took)
        if smaller < self._archive_superres_size:
            self._archive_superres_size = smaller
            log.info("Archive radar sharp render took %.1f s of CPU at %d px; using %d px from now on",
                     took, grid_size, smaller)
        self._archive_superres_ready.emit({
            "gen": gen, "station": station, "scan": scan, "key": key,
            "png": png, "bounds": bounds,
        })

    def _on_archive_superres_ready(self, result: dict) -> None:
        """Runs on the main thread — caches the sharp PNG and shows it, unless
        the clock or station has moved on since it was submitted."""
        if result["gen"] != self._archive_render_generation:
            return
        if self._archive_radar is None or result["station"] != self._archive_radar.station:
            return
        if "error" in result:
            self._radar_settled_scan = result["scan"]     # the normal render stays on screen
            return
        self._archive_superres_cache.put(result["key"], result["png"], result["bounds"])
        pending = self._archive_superres_pending
        if pending is None or pending[2] is not result["scan"]:
            return
        self._radar_overlay.inject(result["png"], result["bounds"])
        self._radar_settled_scan = result["scan"]

    def _on_archive_satellite_frame(self, frame) -> None:
        """Update the archive satellite frame; only show if the user has toggled it on."""
        w, s, e, n = frame.bbox
        self.map_widget.set_satellite_frame(frame.mode, frame.frame_id, frame.png, w, s, e, n)
        self._archive_sat_has_data = True
        self._layer_status["satellite"].mark_success(frame.timestamp)
        self.satellite_controls.set_scan_time(frame.time_str, frame.timestamp)
        if hasattr(self, "_archive_controls"):
            band = "IR" if getattr(frame, "band", "vis") == "ir" else "VIS"
            self._archive_controls.set_satellite_status(
                f"Sat: AWS {frame.mode.upper()} {band} {frame.time_str}"
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

    def _apply_archive_velocity_options(self) -> None:
        from archive.fetchers.radar_archive_fetcher import VelocityOptions
        if self._archive_radar is not None and hasattr(self, "radar_controls"):
            self._archive_radar.set_velocity_options(
                VelocityOptions(dealias=self.radar_controls.is_dealias())
            )

    def _on_archive_dealias_toggled(self, enabled: bool) -> None:
        self._apply_archive_velocity_options()
        if self.radar_controls.current_product() == "velocity" and hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status(
                "Radar: dealiasing…" if enabled else "Radar: loading raw velocity"
            )

    def _on_archive_radar_error(self, msg: str) -> None:
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status("Radar: error", error=True)
        self._layer_status["radar"].mark_error(msg)

    def _renudge_archive_clock(self) -> None:
        self._time_ctrl.set_time(self._time_ctrl.current_time)

    def _on_archive_radar_index_loading_status(self, _index=None) -> None:
        self._archive_loading.set_status("Fetching first radar scan…")

    def _on_archive_radar_loading_done(self, _scan=None) -> None:
        if self._archive_loading.startup_pending():
            self._archive_loading.set_task_done("radar")

    def _on_archive_radar_loading_error(self, _msg=None) -> None:
        if self._archive_loading.startup_pending():
            self._archive_loading.set_task_error("radar")

    def _on_archive_sounding_error(self, msg: str) -> None:
        self.status_msg_label.setText(f"Sounding: {msg}")

    def _on_archive_vehicle_obs_error(self, msg: str) -> None:
        log.warning("One-second archive unavailable: %s", msg)

    def _on_archive_radar_loading_changed(self, loading: bool) -> None:
        station = self._archive_radar.station if self._archive_radar is not None else ""
        self.status_msg_label.setText(f"Radar: loading {station}…" if loading else "")
        if loading and hasattr(self, "_archive_controls"):
            self._archive_controls.set_radar_status(f"Radar: loading {station}…")

    # Worker signals go to bound methods, not lambdas capturing the window: the
    # window is then the receiver, and Qt drops deliveries once it's gone.
    # Delayed lambdas go through _later, in every mode.
    def _later(self, msec: int, callback) -> None:
        """QTimer.singleShot tied to this window: the timer is its child, so it
        dies with it, and the callback is skipped once closing has begun. A bare
        singleShot outlives the window and can run against deleted widgets."""
        timer = QTimer(self)
        timer.setSingleShot(True)

        def fire():
            timer.deleteLater()
            if not getattr(self, "_closing", False):
                callback()
        timer.timeout.connect(fire)
        timer.start(msec)

    def _silence_archive_workers(self) -> None:
        """No more signals from any archive worker once the window closes;
        their threads may still be finishing downloads."""
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget
        for name, value in list(vars(self).items()):
            if name.startswith("_archive_") and isinstance(value, QObject) and not isinstance(value, QWidget):
                try:
                    value.blockSignals(True)
                except RuntimeError:
                    pass   # already deleted

    def _on_archive_satellite_band_changed(self, band: str) -> None:
        """VIS / IR: drop the other band's frame and show this one's."""
        self._archive_sat_has_data = False
        self.map_widget.clear_satellite_frame()
        self.map_widget.set_satellite_visible(False)
        self._archive_satellite.set_band(band)
        mode = self.satellite_controls.current_mode()
        if mode:
            self.status_msg_label.setText(f"Fetching GOES {mode.upper()} {band.upper()}…")

    def _on_archive_satellite_unavailable(self, reason: str) -> None:
        """A day with no GOES imagery: a quiet "none", not an error."""
        self.satellite_controls.set_unavailable(reason)
        self._layer_status["satellite"].set_source_active("mode", False)
        self.map_widget.clear_satellite_frame()
        self.map_widget.set_satellite_visible(False)
        if hasattr(self, "_archive_controls"):
            self._archive_controls.set_satellite_status("Sat: none", tooltip=reason)

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

    def _on_archive_obs_station_click(
        self, station_id: str, name: str, lat: float, lon: float, elev: float
    ) -> None:
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._archive_sounding.fetch_obs_sounding(station_id, lat, lon, elev)
