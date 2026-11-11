"""Satellite, mesoanalysis, SFCOA, surface obs and the optional base layers."""

import logging
import threading
import runtime_flags
import config
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from ui.layer_status import parse_utc
from data.fetchers.satellite_fetcher import SatelliteFetcher
from data.fetchers.mesoanalysis_fetcher import MesoanalysisFetcher
from data.fetchers.sfcoa_overlay_fetcher import SfcoaOverlayFetcher
from data.fetchers.surface_fetcher import SurfaceFetcher
from ui.layers.surface_plot_layer import SurfacePlotLayer
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

    def _init_mesoanalysis(self):
        if self._mesoanalysis_fetcher is not None:
            return
        self._mesoanalysis_fetcher = MesoanalysisFetcher(parent=self)

        self.mesoanalysis_controls.product_toggled.connect(self._on_mesoanalysis_product_toggled)
        self.mesoanalysis_controls.frame_requested.connect(self._on_mesoanalysis_frame_requested)
        self.mesoanalysis_controls.refresh_requested.connect(self._on_mesoanalysis_refresh_requested)
        self.mesoanalysis_controls.visible_toggled.connect(self._on_mesoanalysis_visible_toggled)

        self._mesoanalysis_fetcher.products_ready.connect(self._on_mesoanalysis_products_ready)
        self._mesoanalysis_fetcher.times_ready.connect(self._on_mesoanalysis_times_ready)
        self._mesoanalysis_fetcher.overlay_ready.connect(self._on_mesoanalysis_overlay_ready)
        self._mesoanalysis_fetcher.fetch_error.connect(self._on_mesoanalysis_error)

        self._mesoanalysis_fetcher.refresh_products()
        self.mesoanalysis_controls.set_status("Meso: products")

    def _init_sfcoa(self):
        if self._sfcoa_fetcher is not None:
            return
        self._sfcoa_fetcher = SfcoaOverlayFetcher(config.SFCOA_BASE_URL, parent=self)

        self.sfcoa_controls.product_toggled.connect(self._on_sfcoa_product_toggled)
        self.sfcoa_controls.frame_requested.connect(self._on_sfcoa_frame_requested)
        self.sfcoa_controls.refresh_requested.connect(self._on_sfcoa_refresh_requested)
        self.sfcoa_controls.visible_toggled.connect(self._on_sfcoa_visible_toggled)

        self._sfcoa_fetcher.times_ready.connect(self._on_sfcoa_times_ready)
        self._sfcoa_fetcher.variables_ready.connect(self._on_sfcoa_variables_ready)
        self._sfcoa_fetcher.overlay_ready.connect(self._on_sfcoa_overlay_ready)
        self._sfcoa_fetcher.fetch_error.connect(self._on_sfcoa_error)

        self._sfcoa_fetcher.refresh_catalog()
        self.sfcoa_controls.set_status("SFCOA: times")

    def _init_surface_obs(self):
        self._surface_fetcher = SurfaceFetcher(parent=self)
        self._surface_layer = SurfacePlotLayer(self.map_widget)
        self._surface_station_ids: set[str] = set()
        self._surface_render_generation = 0

        self.surface_controls.ok_toggled.connect(
            lambda v: self._on_surface_source_toggled("ok", v)
        )
        self.surface_controls.wtm_toggled.connect(
            lambda v: self._on_surface_source_toggled("wtm", v)
        )
        self.surface_controls.ks_toggled.connect(
            lambda v: self._on_surface_source_toggled("ks", v)
        )
        self.surface_controls.co_toggled.connect(
            lambda v: self._on_surface_source_toggled("co", v)
        )
        self.surface_controls.ne_toggled.connect(
            lambda v: self._on_surface_source_toggled("ne", v)
        )
        self.surface_controls.sd_toggled.connect(
            lambda v: self._on_surface_source_toggled("sd", v)
        )
        self.surface_controls.asos_toggled.connect(self._on_asos_toggled)
        self.surface_controls.asos_bbox_requested.connect(self._start_new_asos_bbox)
        self.surface_controls.plots_toggled.connect(self._surface_layer.set_visible)

        self.map_widget.asos_bbox_selected.connect(self._on_asos_bbox_selected)
        self._surface_fetcher.observations_updated.connect(self._on_surface_observations_updated)
        self._surface_fetcher.status_updated.connect(self.surface_controls.set_status)
        self._surface_fetcher.diagnostics_updated.connect(self.surface_controls.set_diagnostics)
        self._surface_fetcher.status_updated.connect(self.status_msg_label.setText)
        self._surface_fetcher.error.connect(self._on_surface_error)

        self._surface_fetcher.start()
        QTimer.singleShot(1200, lambda: self._surface_layer.set_visible(
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
        if source == "ok":
            self._surface_fetcher.set_ok_enabled(enabled)
            self._set_layer_active("ok_mesonet", enabled)
        elif source == "wtm":
            self._surface_fetcher.set_wtm_enabled(enabled)
            self._set_layer_active("wtm", enabled)
        elif source == "ks":
            self._surface_fetcher.set_ks_enabled(enabled)
            self._set_layer_active("ks_mesonet", enabled)
        elif source == "co":
            self._surface_fetcher.set_co_enabled(enabled)
            self._set_layer_active("co_mesonet", enabled)
        elif source == "ne":
            self._surface_fetcher.set_ne_enabled(enabled)
            self._set_layer_active("ne_mesonet", enabled)
        elif source == "sd":
            self._surface_fetcher.set_sd_enabled(enabled)
            self._set_layer_active("sd_mesonet", enabled)
        else:
            return

        if not enabled:
            self._remove_surface_source_plots(source)

    def _surface_source_enabled(self, source: str) -> bool:
        if source == "ok":
            return bool(getattr(self._surface_fetcher, "_ok_enabled", False))
        if source == "wtm":
            return bool(getattr(self._surface_fetcher, "_wtm_enabled", False))
        if source == "ks":
            return bool(getattr(self._surface_fetcher, "_ks_enabled", False))
        if source == "co":
            return bool(getattr(self._surface_fetcher, "_co_enabled", False))
        if source == "ne":
            return bool(getattr(self._surface_fetcher, "_ne_enabled", False))
        if source == "sd":
            return bool(getattr(self._surface_fetcher, "_sd_enabled", False))
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
            # archive mode: simply show/hide the overlay the archive fetcher is updating.
            has_data = getattr(self, "_archive_sat_has_data", False)
            self.map_widget.set_satellite_visible(checked and has_data)
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

    def _on_mesoanalysis_drawer_toggled(self, checked: bool):
        if checked and self._mesoanalysis_fetcher is None:
            self._init_mesoanalysis()
            return
        if checked and not self._mesoanalysis_current_metadata:
            self._mesoanalysis_fetcher.refresh_products()

    def _on_sfcoa_drawer_toggled(self, checked: bool):
        if checked and self._sfcoa_fetcher is None:
            self._init_sfcoa()
            return
        if checked and not self._sfcoa_times and self._sfcoa_fetcher:
            self._sfcoa_fetcher.refresh_catalog()
        if checked:
            self._sfcoa_visible = bool(self._sfcoa_active_products)
            self._set_layer_active("sfcoa", self._sfcoa_visible)
            if self._sfcoa_current_metadata and self._sfcoa_visible:
                self._display_sfcoa_metadata(self._sfcoa_current_metadata)
            self.map_widget.set_sfcoa_visible(self._sfcoa_visible)

    def _on_mesoanalysis_products_ready(self, products):
        self.mesoanalysis_controls.set_products(products)
        self.mesoanalysis_controls.set_status(f"Meso: {len(products)} products")

    def _on_mesoanalysis_product_toggled(self, product_id: str, enabled: bool):
        if not product_id or self._mesoanalysis_fetcher is None:
            return
        self.mesoanalysis_controls.stop_loop()
        if enabled:
            self._mesoanalysis_active_products.add(product_id)
            self._mesoanalysis_visible = True
            self._set_layer_active("mesoanalysis", True)
            self.map_widget.set_mesoanalysis_opacity(1.0)
            self.mesoanalysis_controls.set_status(f"Meso: loading {product_id}")
            self._mesoanalysis_fetcher.fetch_times(product_id)
        else:
            self._mesoanalysis_active_products.discard(product_id)
            self.map_widget.clear_mesoanalysis_overlay(product_id)
            if not self._mesoanalysis_active_products:
                self._mesoanalysis_visible = False
                self._set_layer_active("mesoanalysis", False)
                self.map_widget.set_mesoanalysis_visible(False)

    def _on_mesoanalysis_times_ready(self, product_id: str, times):
        if product_id not in self._mesoanalysis_active_products:
            return
        self._mesoanalysis_times_by_product[product_id] = list(times)
        if len(times) > len(self._mesoanalysis_times):
            self._mesoanalysis_times = list(times)
        self.mesoanalysis_controls.set_times(self._mesoanalysis_times or times)
        self.mesoanalysis_controls.set_status(f"Meso: {len(self._mesoanalysis_active_products)} products")
        self._fetch_mesoanalysis_product_frame(product_id, self.mesoanalysis_controls.current_frame())

    def _on_mesoanalysis_frame_requested(self, idx: int):
        self.mesoanalysis_controls.set_frame(idx)
        self._fetch_current_mesoanalysis_frame()

    def _fetch_current_mesoanalysis_frame(self):
        for product_id in sorted(self._mesoanalysis_active_products):
            self._fetch_mesoanalysis_product_frame(product_id, self.mesoanalysis_controls.current_frame())

    def _fetch_mesoanalysis_product_frame(self, product_id: str, idx: int):
        if not product_id or self._mesoanalysis_fetcher is None:
            return
        times = self._mesoanalysis_times_by_product.get(product_id) or self._mesoanalysis_times
        if not times:
            return
        idx = max(0, min(len(times) - 1, int(idx)))
        time_id = times[idx].time_id
        cached = self._mesoanalysis_overlay_cache.get((product_id, time_id))
        if cached is not None:
            self._on_mesoanalysis_overlay_ready(cached)
            return
        self.mesoanalysis_controls.set_status(f"Meso: loading {product_id} {time_id[-6:-4]}Z")
        self._mesoanalysis_fetcher.fetch_overlay(product_id, time_id)

    def _on_mesoanalysis_refresh_requested(self):
        if self._mesoanalysis_fetcher is None:
            return
        self.mesoanalysis_controls.set_status("Meso: refreshing")
        if self._mesoanalysis_active_products:
            for product_id in sorted(self._mesoanalysis_active_products):
                self._mesoanalysis_fetcher.fetch_times(product_id)
        else:
            self._mesoanalysis_fetcher.refresh_products()

    def _on_mesoanalysis_visible_toggled(self, visible: bool):
        self._mesoanalysis_visible = bool(visible)
        self._set_layer_active("mesoanalysis", self._mesoanalysis_visible)
        if self._mesoanalysis_visible:
            self.map_widget.set_mesoanalysis_opacity(1.0)
        self.map_widget.set_mesoanalysis_visible(
            self._mesoanalysis_visible and bool(self._mesoanalysis_active_products)
        )

    def _on_mesoanalysis_overlay_ready(self, metadata: dict):
        self._mesoanalysis_current_metadata = metadata
        self._layer_status["mesoanalysis"].mark_success(parse_utc(metadata.get("valid_time")))
        product_id = str(metadata.get("product") or self.mesoanalysis_controls.current_product())
        time_id = str(metadata.get("time") or self.mesoanalysis_controls.current_time())
        if product_id and time_id:
            self._mesoanalysis_overlay_cache[(product_id, time_id)] = metadata
        self.mesoanalysis_controls.set_status(f"Meso: {len(self._mesoanalysis_active_products)} products")
        if self._mesoanalysis_visible:
            self._display_mesoanalysis_metadata(metadata)

    def _display_mesoanalysis_metadata(self, metadata: dict):
        bounds = metadata.get("bounds") or []
        if len(bounds) != 4:
            self._on_mesoanalysis_error("Mesoanalysis metadata missing bounds")
            return
        tile_url = metadata.get("tile_url") or ""
        source_layer = metadata.get("source_layer") or ""
        if not tile_url or not source_layer:
            self._on_mesoanalysis_error("Mesoanalysis metadata missing tile source")
            return
        west, south, east, north = self._mesoanalysis_display_bounds(
            [float(v) for v in bounds]
        )
        self.map_widget.set_mesoanalysis_overlay(
            str(metadata.get("product") or ""),
            tile_url,
            source_layer,
            west,
            south,
            east,
            north,
            int(metadata.get("minzoom") or 0),
            int(metadata.get("maxzoom") or 8),
            str(metadata.get("label_units") or ""),
        )
        self.map_widget.set_mesoanalysis_visible(True)

    def _mesoanalysis_display_bounds(self, source_bounds: list[float]) -> list[float]:
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

    def _on_mesoanalysis_error(self, msg: str):
        self.mesoanalysis_controls.set_status(str(msg))
        self._layer_status["mesoanalysis"].mark_error(str(msg))

    def _on_sfcoa_times_ready(self, times):
        self._sfcoa_times = list(times)
        self.sfcoa_controls.set_times(self._sfcoa_times)
        self.sfcoa_controls.set_status(f"SFCOA: {len(self._sfcoa_times)} times")
        if self._sfcoa_times and self._sfcoa_fetcher:
            self._sfcoa_fetcher.fetch_variables(self.sfcoa_controls.current_time())

    def _on_sfcoa_variables_ready(self, time_id: str, variables):
        self._sfcoa_variables_by_time[time_id] = list(variables)
        if time_id != self.sfcoa_controls.current_time():
            return
        self.sfcoa_controls.set_products(variables)
        self.sfcoa_controls.set_status(f"SFCOA {time_id[-2:]}Z: {len(variables)} vars")
        if self._sfcoa_active_products:
            self._fetch_current_sfcoa_frame()

    def _on_sfcoa_product_toggled(self, product_id: str, enabled: bool):
        if not product_id or self._sfcoa_fetcher is None:
            return
        time_id = self.sfcoa_controls.current_time()
        if enabled:
            self._sfcoa_active_products.add(product_id)
            self._sfcoa_visible = True
            self._set_layer_active("sfcoa", True)
            self.map_widget.set_sfcoa_opacity(1.0)
            if time_id:
                self.sfcoa_controls.set_status(f"SFCOA: loading {product_id}")
                self._fetch_sfcoa_product_time(product_id, time_id)
        else:
            self._sfcoa_active_products.discard(product_id)
            self.map_widget.clear_sfcoa_overlay(product_id)
            if not self._sfcoa_active_products:
                self._sfcoa_visible = False
                self._set_layer_active("sfcoa", False)
                self.map_widget.set_sfcoa_visible(False)

    def _on_sfcoa_frame_requested(self, idx: int):
        self.sfcoa_controls.set_frame(idx)
        time_id = self.sfcoa_controls.current_time()
        if not time_id:
            return
        if time_id not in self._sfcoa_variables_by_time and self._sfcoa_fetcher:
            self.sfcoa_controls.set_status(f"SFCOA: loading {time_id[-2:]}Z")
            self._sfcoa_fetcher.fetch_variables(time_id)
            return
        variables = self._sfcoa_variables_by_time.get(time_id, [])
        self.sfcoa_controls.set_products(variables)
        self._fetch_current_sfcoa_frame()

    def _fetch_current_sfcoa_frame(self):
        time_id = self.sfcoa_controls.current_time()
        if not time_id:
            return
        available = {
            getattr(var, "variable_id", "")
            for var in self._sfcoa_variables_by_time.get(time_id, [])
        }
        for product_id in sorted(self._sfcoa_active_products):
            if available and product_id not in available:
                continue
            self._fetch_sfcoa_product_time(product_id, time_id)

    def _fetch_sfcoa_product_time(self, product_id: str, time_id: str):
        cached = self._sfcoa_overlay_cache.get((product_id, time_id))
        if cached is not None:
            self._on_sfcoa_overlay_ready(cached)
            return
        self.sfcoa_controls.set_status(f"SFCOA: loading {product_id} {time_id[-2:]}Z")
        self._sfcoa_fetcher.fetch_overlay(product_id, time_id)

    def _on_sfcoa_refresh_requested(self):
        if self._sfcoa_fetcher is None:
            return
        self.sfcoa_controls.set_status("SFCOA: refreshing")
        self._sfcoa_variables_by_time.clear()
        self._sfcoa_fetcher.refresh_catalog()

    def _on_sfcoa_visible_toggled(self, visible: bool):
        self._sfcoa_visible = bool(visible)
        self._set_layer_active("sfcoa", self._sfcoa_visible)
        if self._sfcoa_visible:
            self.map_widget.set_sfcoa_opacity(1.0)
        self.map_widget.set_sfcoa_visible(
            self._sfcoa_visible and bool(self._sfcoa_active_products)
        )

    def _on_sfcoa_overlay_ready(self, metadata: dict):
        self._sfcoa_current_metadata = metadata
        self._layer_status["sfcoa"].mark_success(parse_utc(metadata.get("valid_time")))
        product_id = str(metadata.get("product") or metadata.get("variable") or "")
        time_id = str(metadata.get("time") or self.sfcoa_controls.current_time())
        if product_id and time_id:
            self._sfcoa_overlay_cache[(product_id, time_id)] = metadata
        summary = self._sfcoa_status_summary(metadata)
        self._sfcoa_status_text = summary
        self.sfcoa_controls.set_status(summary.replace("SFCOA ", "", 1))
        self.status_msg_label.setText(summary)
        if self._sfcoa_visible:
            self._display_sfcoa_metadata(metadata)

    def _sfcoa_status_summary(self, metadata: dict) -> str:
        label = str(metadata.get("label") or metadata.get("product") or "SFCOA")
        valid = _overlay_time_label(metadata.get("valid_time"))
        if valid:
            return f"SFCOA {label} valid {valid}"
        time_id = str(metadata.get("time") or "")
        if time_id:
            return f"SFCOA {label} {time_id[-2:]}Z"
        return f"SFCOA {label}"

    def _display_sfcoa_metadata(self, metadata: dict):
        bounds = metadata.get("bounds") or metadata.get("bbox") or []
        if len(bounds) != 4:
            self._on_sfcoa_error("SFCOA metadata missing bounds")
            return
        tile_url = str(metadata.get("tile_url") or "")
        source_layer = str(metadata.get("source_layer") or "")
        if not tile_url or not source_layer:
            self._on_sfcoa_error("SFCOA metadata missing tile source")
            return
        tile_key = str(metadata.get("tile_key") or "")
        mbtiles_path = str(metadata.get("mbtiles_path") or "")
        if tile_key and mbtiles_path and hasattr(self.map_widget, "register_sfcoa_mbtiles"):
            self.map_widget.register_sfcoa_mbtiles(tile_key, mbtiles_path)
        west, south, east, north = self._mesoanalysis_display_bounds(
            [float(v) for v in bounds]
        )
        self.map_widget.set_sfcoa_overlay(
            str(metadata.get("product") or metadata.get("variable") or ""),
            tile_url,
            source_layer,
            west,
            south,
            east,
            north,
            int(metadata.get("minzoom") or 0),
            int(metadata.get("maxzoom") or 8),
            str(metadata.get("label_units") or metadata.get("units") or ""),
        )
        self.map_widget.set_sfcoa_visible(True)

    def _on_sfcoa_error(self, msg: str):
        text = f"SFCOA: {msg}"
        self.sfcoa_controls.set_status(text)
        self._layer_status["sfcoa"].mark_error(str(msg))

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
