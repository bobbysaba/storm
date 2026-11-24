"""Satellite, the contours source (NSSL: SFCOA), surface obs and the optional base layers."""

import logging
import threading
import runtime_flags
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from ui.layer_status import parse_utc
from data.fetchers.satellite_fetcher import SatelliteFetcher
from data.fetchers.surface_fetcher import SurfaceFetcher
from ui.layers.surface_plot_layer import SurfacePlotLayer, set_fresh_minutes
from ui.app.main_window_sources import source_layer_key
from ui.app.main_window_util import _loop_interval_ms, _overlay_time_label

log = logging.getLogger("ui.app.main_window")


class MainWindowOverlaysMixin:

    def _init_satellite(self):
        self._satellite_fetcher  = SatelliteFetcher(parent=self)
        self._satellite_cache: dict[str, list] = {"conus": [], "meso1": [], "meso2": []}
        self._satellite_loop_timer = QTimer(self)
        self._satellite_loop_timer.setInterval(_loop_interval_ms(600))   # ms per frame during loop
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

        self._satellite_fetcher.start()

        # a sector picked before this deferred init had no handler connected yet
        mode = self.satellite_controls.current_mode()
        if mode:
            self._on_satellite_mode_changed(mode)

    def _init_contour(self):
        if self._contour_fetcher is not None:
            return
        from data.fetchers.contour_fetcher import ContourFetcher  # noqa: PLC0415
        self._contour_fetcher = ContourFetcher(self._contour_source, parent=self)

        self.contour_controls.product_toggled.connect(self._on_contour_product_toggled)
        self.contour_controls.frame_requested.connect(self._on_contour_frame_requested)
        self.contour_controls.refresh_requested.connect(self._on_contour_refresh_requested)
        self.contour_controls.visible_toggled.connect(self._on_contour_visible_toggled)

        self._contour_fetcher.times_ready.connect(self._on_contour_times_ready)
        self._contour_fetcher.variables_ready.connect(self._on_contour_variables_ready)
        self._contour_fetcher.overlay_ready.connect(self._on_contour_overlay_ready)
        self._contour_fetcher.fetch_error.connect(self._on_contour_error)

        self._contour_fetcher.refresh_catalog()
        self.contour_controls.set_status(f"{self._contour_label()}: times")

    def _contour_label(self) -> str:
        source = getattr(self, "_contour_source", None)
        return source.short if source is not None else "Contours"

    def _init_surface_obs(self):
        # ASOS, plus the station_obs [[sources]] in storm.toml (live only)
        self._surface_fetcher = SurfaceFetcher(parent=self, sources=self._mode_sources("station_obs"))
        for source in self._surface_fetcher.sources.values():
            set_fresh_minutes(source.id, source.fresh_min)
        self._surface_layer = SurfacePlotLayer(self.map_widget)
        self._surface_station_ids: set[str] = set()
        self._surface_render_generation = 0

        self.surface_controls.source_toggled.connect(self._on_surface_source_toggled)
        for key in self._surface_fetcher.sources:
            btn = self._source_btns.get(key)   # where = "toolbar"
            if btn is not None:
                btn.toggled.connect(lambda on, k=key: self._on_surface_source_toggled(k, on))
        self.surface_controls.asos_toggled.connect(self._on_asos_toggled)
        self.surface_controls.asos_bbox_requested.connect(self._start_new_asos_bbox)
        self.surface_controls.plots_toggled.connect(self._surface_layer.set_visible)

        self.map_widget.asos_bbox_selected.connect(self._on_asos_bbox_selected)
        self.map_widget.asos_box_cancelled.connect(self._on_asos_box_cancelled)
        self._surface_fetcher.observations_updated.connect(self._on_surface_observations_updated)
        self._surface_fetcher.status_updated.connect(self.surface_controls.set_status)
        self._surface_fetcher.diagnostics_updated.connect(self.surface_controls.set_diagnostics)
        self._surface_fetcher.status_updated.connect(self.status_msg_label.setText)
        self._surface_fetcher.error.connect(self._on_surface_error)

        self._surface_fetcher.start()
        self._later(1200, lambda: self._surface_layer.set_visible(
            self.surface_controls.plots_visible()
        ))

    def _on_surface_observations_updated(self, items: list[dict]):
        self._layer_status["surface"].mark_success()
        items = [
            item for item in items
            if self._surface_source_enabled(item.get("source", ""))
        ]
        if runtime_flags.FLAGS.lite_mode:
            from ui.layers.surface_plot_layer import thin_stations  # noqa: PLC0415
            items = thin_stations(items, runtime_flags.LITE_STATION_CELL_DEG)
        self._surface_render_generation += 1
        render_generation = self._surface_render_generation
        incoming: set[str] = {item["id"] for item in items}
        gone = self._surface_station_ids - incoming
        self._surface_station_ids = incoming

        layer      = self._surface_layer
        map_widget = self.map_widget

        class RenderSignals(QObject):
            ready = pyqtSignal(list, set)   # (rendered_rows, ids_to_remove)
            status = pyqtSignal(str)

        signals = RenderSignals()

        def _push(rendered_data, ids_to_remove):
            if render_generation != self._surface_render_generation:
                return
            # remove stale stations in one JS call
            if ids_to_remove:
                for sid in ids_to_remove:
                    layer._cache.pop(sid, None)
                map_widget.remove_surface_station_plots_batch(ids_to_remove)

            if not rendered_data:
                return

            # store PNG bytes in scheme handler + send tiny metadata-only JS payload
            batch = []
            for sid, lat, lon, fp, png, name in rendered_data:
                layer._cache[sid] = (fp, png)
                batch.append((sid, lat, lon, png, name))
            map_widget.add_surface_station_plots_batch(batch)

        # critical: Ensure the signal is routed to the main thread
        signals.ready.connect(_push, Qt.ConnectionType.QueuedConnection)
        signals.status.connect(self.status_msg_label.setText, Qt.ConnectionType.QueuedConnection)

        def _render_worker():
            from ui.layers.surface_plot_layer import _surface_obs_fingerprint, render_surface
            import time

            _gone_to_emit = gone
            has_asos = any(item.get("source") == "asos" for item in items)

            # if there's nothing to render but stations to remove, clear them out
            if not items:
                if _gone_to_emit and render_generation == self._surface_render_generation:
                    signals.ready.emit([], _gone_to_emit)
                return

            # snapshot the cache once to avoid live dict mutation from the main thread
            cache_snapshot = dict(layer._cache)

            to_render = []
            for item in items:
                if render_generation != self._surface_render_generation:
                    return
                obs  = item["obs"]
                sid  = item["id"]
                name = item.get("name", sid)
                fp   = _surface_obs_fingerprint(obs, sid)
                cached = cache_snapshot.get(sid)
                if cached and cached[0] == fp:
                    continue
                to_render.append((item, obs, sid, name, fp))

            if has_asos and to_render:
                signals.status.emit(f"ASOS: rendering {len(to_render)} station plots…")

            rendered_chunk = []
            chunk_size = min(24, max(1, len(to_render))) if has_asos else max(1, len(to_render))

            for i, (item, obs, sid, name, fp) in enumerate(to_render):
                if render_generation != self._surface_render_generation:
                    return
                try:
                    png   = render_surface(fp)
                    rendered_chunk.append((sid, obs.lat, obs.lon, fp, png, name))
                except Exception as exc:
                    log.error("surface render failed for %s: %s", sid, exc)
                
                if len(rendered_chunk) >= chunk_size or i == len(to_render) - 1:
                    if render_generation != self._surface_render_generation:
                        return
                    signals.ready.emit(rendered_chunk, _gone_to_emit)
                    rendered_chunk = []
                    _gone_to_emit = set()  # Only emit the 'gone' list on the very first chunk
                    
                    if has_asos:
                        time.sleep(0.015)

            if has_asos and render_generation == self._surface_render_generation:
                signals.status.emit(f"ASOS: rendered {len(to_render)} station plots")

        threading.Thread(target=_render_worker, daemon=True).start()

    def _on_surface_source_toggled(self, source: str, enabled: bool) -> None:
        if source not in self._surface_fetcher.sources:
            return
        self._surface_fetcher.set_network_enabled(source, enabled)
        self._set_layer_active(source_layer_key(source), enabled)

        if not enabled:
            self._remove_surface_source_plots(source)

    def _surface_source_enabled(self, source: str) -> bool:
        if source in self._surface_fetcher.sources:
            return self._surface_fetcher.network_enabled(source)
        if source == "asos":
            return bool(
                getattr(self._surface_fetcher, "_asos_enabled", False)
                and getattr(self._surface_fetcher, "_asos_bbox", None) is not None
            )
        return False

    def _remove_surface_source_plots(self, source: str) -> None:
        self._surface_render_generation += 1
        prefix = f"surface:{source}:"
        ids = [
            sid for sid in self._surface_station_ids
            if sid.startswith(prefix)
        ]
        if not ids:
            return

        self._surface_station_ids = {
            sid for sid in self._surface_station_ids
            if not sid.startswith(prefix)
        }
        for sid in ids:
            self._surface_layer._cache.pop(sid, None)
        self.map_widget.remove_surface_station_plots_batch(ids)

    def _on_surface_error(self, msg: str):
        self._layer_status["surface"].mark_error(msg)
        if str(msg).startswith("ASOS:") and not self._click_modes.exit("asos_bbox"):
            self.map_widget.set_asos_bbox_mode(False)
            self.map_widget.run_js(
                "if(window.stormRestoreAsosMapInteractions) stormRestoreAsosMapInteractions();"
            )
        self._layout_overlays()

    def _on_satellite_toggled(self, checked: bool):
        self._set_layer_active("satellite", checked)
        if self._archive:
            # archive mode: show/hide the overlay; the fetcher lists and
            # downloads only while the layer is on.
            has_data = getattr(self, "_archive_sat_has_data", False)
            self.map_widget.set_satellite_visible(checked and has_data)
            if hasattr(self, "_archive_satellite"):
                self._archive_satellite.set_active(checked)
            return
        if not hasattr(self, "_satellite_fetcher"):
            # drawer used before _init_satellite ran (it waits for the startup phases)
            return
        if not checked:
            # closing the drawer stops playback but leaves the overlay visible.
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

    def _on_nlcd_toggled(self, checked: bool):
        self._set_layer_active("nlcd", checked)
        if hasattr(self, "landcover_controls"):
            self.map_widget.set_nlcd_opacity(self.landcover_controls.opacity())
        self.map_widget.set_nlcd_visible(checked)

    def _on_nlcd_opacity_changed(self, opacity: float):
        self.map_widget.set_nlcd_opacity(opacity)

    def _on_satellite_basemap_toggled(self, checked: bool):
        self._set_layer_active("satellite_basemap", checked)
        self.map_widget.set_satellite_basemap_visible(checked)

    def _on_contour_drawer_toggled(self, checked: bool):
        if checked and self._contour_fetcher is None:
            self._init_contour()
            return
        if checked and not self._contour_times and self._contour_fetcher:
            self._contour_fetcher.refresh_catalog()
        if checked:
            self._contour_visible = bool(self._contour_active_products)
            self._set_layer_active("contours", self._contour_visible)
            if self._contour_current_metadata and self._contour_visible:
                self._display_contour_metadata(self._contour_current_metadata)
            self.map_widget.set_contour_visible(self._contour_visible)

    def _contour_display_bounds(self, source_bounds: list[float]) -> list[float]:
        mbtiles_bounds = self._load_mbtiles_bounds()
        if not mbtiles_bounds or len(source_bounds) != 4:
            return source_bounds
        west = max(source_bounds[0], mbtiles_bounds[0])
        south = max(source_bounds[1], mbtiles_bounds[1])
        east = min(source_bounds[2], mbtiles_bounds[2])
        north = min(source_bounds[3], mbtiles_bounds[3])
        if west >= east or south >= north:
            return source_bounds
        return [west, south, east, north]

    def _on_contour_times_ready(self, times):
        self._contour_times = list(times)
        self.contour_controls.set_times(self._contour_times)
        self.contour_controls.set_status(f"{self._contour_label()}: {len(self._contour_times)} times")
        if self._contour_times and self._contour_fetcher:
            self._contour_fetcher.fetch_variables(self.contour_controls.current_time())

    def _on_contour_variables_ready(self, time_id: str, variables):
        self._contour_variables_by_time[time_id] = list(variables)
        if time_id != self.contour_controls.current_time():
            return
        self.contour_controls.set_products(variables)
        self.contour_controls.set_status(f"{self._run_label(time_id)}: {len(variables)} vars")
        if self._contour_active_products:
            self._fetch_current_contour_frame()

    def _on_contour_product_toggled(self, product_id: str, enabled: bool):
        if not product_id or self._contour_fetcher is None:
            return
        time_id = self.contour_controls.current_time()
        if enabled:
            self._contour_active_products.add(product_id)
            self._contour_visible = True
            self._set_layer_active("contours", True)
            self.map_widget.set_contour_opacity(1.0)
            if time_id:
                self.contour_controls.set_status(f"loading {product_id}")
                self._fetch_contour_product_time(product_id, time_id)
        else:
            self._contour_active_products.discard(product_id)
            self.map_widget.clear_contour_overlay(product_id)
            if not self._contour_active_products:
                self._contour_visible = False
                self._set_layer_active("contours", False)
                self.map_widget.set_contour_visible(False)

    def _on_contour_frame_requested(self, idx: int):
        self.contour_controls.set_frame(idx)
        time_id = self.contour_controls.current_time()
        if not time_id:
            return
        if time_id not in self._contour_variables_by_time and self._contour_fetcher:
            self.contour_controls.set_status(f"loading {self._run_label(time_id)}")
            self._contour_fetcher.fetch_variables(time_id)
            return
        variables = self._contour_variables_by_time.get(time_id, [])
        self.contour_controls.set_products(variables)
        self._fetch_current_contour_frame()

    def _fetch_current_contour_frame(self):
        time_id = self.contour_controls.current_time()
        if not time_id:
            return
        available = {
            getattr(var, "variable_id", "")
            for var in self._contour_variables_by_time.get(time_id, [])
        }
        for product_id in sorted(self._contour_active_products):
            if available and product_id not in available:
                continue
            self._fetch_contour_product_time(product_id, time_id)

    def _fetch_contour_product_time(self, product_id: str, time_id: str):
        cached = self._contour_overlay_cache.get((product_id, time_id))
        if cached is not None:
            self._on_contour_overlay_ready(cached)
            return
        self.contour_controls.set_status(f"loading {product_id} {self._run_label(time_id)}")
        self._contour_fetcher.fetch_overlay(product_id, time_id)

    def _on_contour_refresh_requested(self):
        if self._contour_fetcher is None:
            return
        self.contour_controls.set_status("refreshing")
        self._contour_variables_by_time.clear()
        self._contour_fetcher.refresh_catalog()

    def _on_contour_visible_toggled(self, visible: bool):
        self._contour_visible = bool(visible)
        self._set_layer_active("contours", self._contour_visible)
        if self._contour_visible:
            self.map_widget.set_contour_opacity(1.0)
        self.map_widget.set_contour_visible(
            self._contour_visible and bool(self._contour_active_products)
        )

    def _on_contour_overlay_ready(self, metadata: dict):
        self._contour_current_metadata = metadata
        self._layer_status["contours"].mark_success(parse_utc(metadata.get("valid_time")))
        product_id = str(metadata.get("product") or "")
        time_id = str(metadata.get("time") or self.contour_controls.current_time())
        if product_id and time_id:
            self._contour_overlay_cache[(product_id, time_id)] = metadata
        summary = self._contour_status_summary(metadata)
        self._contour_status_text = summary
        self.contour_controls.set_status(summary.replace(f"{self._contour_label()} ", "", 1))
        self.status_msg_label.setText(summary)
        if self._contour_visible:
            self._display_contour_metadata(metadata)

    def _contour_status_summary(self, metadata: dict) -> str:
        name = self._contour_label()
        label = str(metadata.get("label") or metadata.get("product") or "")
        valid = _overlay_time_label(metadata.get("valid_time"))
        if valid:
            return f"{name} {label} valid {valid}"
        time_id = str(metadata.get("time") or "")
        if time_id:
            return f"{name} {label} {self._run_label(time_id)}"
        return f"{name} {label}"

    def _run_label(self, time_id: str) -> str:
        """A run's label in the drawer's times (e.g. 15:00Z), else its id."""
        for run in self._contour_times:
            if run.time_id == time_id:
                return run.label
        return time_id

    def _display_contour_metadata(self, metadata: dict):
        bounds = metadata.get("bounds") or metadata.get("bbox") or []
        if len(bounds) != 4:
            self._on_contour_error("metadata missing bounds")
            return
        tile_url = str(metadata.get("tile_url") or "")
        source_layer = str(metadata.get("source_layer") or "")
        if not tile_url or not source_layer:
            self._on_contour_error("metadata missing tile source")
            return
        tile_key = str(metadata.get("tile_key") or "")
        tile_path = str(metadata.get("tile_path") or "")
        if tile_key and tile_path and hasattr(self.map_widget, "register_contour_tiles"):
            self.map_widget.register_contour_tiles(tile_key, tile_path)
        west, south, east, north = self._contour_display_bounds(
            [float(v) for v in bounds]
        )
        self.map_widget.set_contour_overlay(
            str(metadata.get("product") or ""),
            tile_url,
            source_layer,
            west,
            south,
            east,
            north,
            int(metadata.get("minzoom") or 0),
            int(metadata.get("maxzoom") or 8),
            str(metadata.get("units") or ""),
        )
        self.map_widget.set_contour_visible(True)

    def _on_contour_error(self, msg: str):
        text = f"{self._contour_label()}: {msg}"
        self.contour_controls.set_status(text)
        self._layer_status["contours"].mark_error(str(msg))

    def _on_satellite_mode_changed(self, mode: str):
        self._layer_status["satellite"].set_source_active("mode", bool(mode))
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
            if self._archive_satellite.is_active():
                self.status_msg_label.setText(f"Fetching GOES {mode.upper()}…")
            self._layout_overlays()
            return

        self._satellite_loop_timer.stop()
        self.satellite_controls.stop_loop()
        self.satellite_controls.reset_cache_ui()
        self._satellite_fetcher.set_active_mode(mode)
        # each mode has its own data age
        self._layer_status["satellite"].restart()

        if not mode:
            self.map_widget.set_satellite_visible(False)
            return

        self.map_widget.set_satellite_mode(mode)
        frames = self._satellite_cache.get(mode, [])
        if frames:
            self._layer_status["satellite"].mark_success(frames[-1].timestamp)
            self.satellite_controls.set_cache_size(len(frames))
            self._render_satellite_frame(frames[-1])
            self.satellite_controls.set_scan_time(frames[-1].time_str, frames[-1].timestamp)
            self.map_widget.set_satellite_visible(True)
            self.status_msg_label.setText(f"GOES {mode.upper()} {frames[-1].time_str}")
            self._layout_overlays()
        else:
            # clear the previous mode's frame so CONUS doesn't linger
            self.map_widget.clear_satellite_frame()
            self.map_widget.set_satellite_visible(False)
            # backfill recent frames on first select so loop playback works immediately.
            self._satellite_fetcher.fetch_history(mode, 10)
            self.status_msg_label.setText(f"Fetching GOES {mode.upper()}…")
            self._layout_overlays()

    def _on_satellite_frames_updated(self, mode: str, frames: list):
        self._satellite_cache[mode] = frames
        active_mode = self.satellite_controls.current_mode()
        if mode != active_mode:
            return
        if frames:
            self._layer_status["satellite"].mark_success(frames[-1].timestamp)
        was_live = self.satellite_controls.is_at_latest_frame()
        self.satellite_controls.set_cache_size(len(frames))
        if was_live:
            self._render_satellite_frame(frames[-1])
            self.satellite_controls.set_scan_time(frames[-1].time_str, frames[-1].timestamp)
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
        self.satellite_controls.set_scan_time(frame.time_str, frame.timestamp)

    def _on_satellite_speed_changed(self, ms: int):
        self._satellite_loop_timer.setInterval(_loop_interval_ms(ms))

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
        self.satellite_controls.set_scan_time(frames[nxt].time_str, frames[nxt].timestamp)

    def _render_satellite_frame(self, frame):
        if frame.png:
            w, s, e, n = frame.bbox
            mode = self.satellite_controls.current_mode()
            self.map_widget.set_satellite_frame(mode, frame.frame_id, frame.png, w, s, e, n)
        else:
            self.map_widget.set_satellite_time(frame.time_iso)

    def _on_meso_sectors_updated(self, sectors: dict):
        for idx in (1, 2):
            bbox = sectors.get(idx)
            # in archive mode buttons stay enabled even before bbox is known;
            available = bbox is not None or self._archive
            self.satellite_controls.set_meso_available(idx, available, bbox)
        self.map_widget.set_meso_sectors(sectors)

    def _on_meso_preview(self, idx: int, active: bool):
        self.map_widget.preview_meso_sector(idx if active else None)

    def _on_satellite_error(self, msg: str):
        self._layer_status["satellite"].mark_error(msg)
