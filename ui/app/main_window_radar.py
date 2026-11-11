"""Live radar: site selection, fetch, decode, render, inject and the frame loop."""

import logging
import config
from datetime import datetime, timezone, timedelta
from PyQt6.QtCore import QTimer
from ui.map.radar_overlay import (
    RadarOverlay,
    cached_scan_png as _cached_scan_png,
    render_scan_to_png as _render_scan_to_png,
)
from data.fetchers.radar_fetcher import RadarFetcher
from data.fetchers.sounding_fetcher import SoundingFetcher
from data.fetchers.obs_sounding_fetcher import ObsSoundingFetcher
from data.fetchers.clamps_sounding_fetcher import ClampsSoundingFetcher
from data.stations.sounding_stations import build_stations_geojson
from data.radar.radar_decoder import decode_nexrad_l3
from core.observation import Observation
from ui.app.main_window_util import LIVE_RADAR_MAX_AGE_MINUTES, _loop_interval_ms

log = logging.getLogger("ui.app.main_window")


class MainWindowRadarMixin:

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

    def _init_radar(self):
        self._radar_overlay = RadarOverlay(self.map_widget)
        self._radar_fetcher = RadarFetcher()
        self._scan_cache: dict[str, list] = {}   # key: "site/product" → list of RadarScan

        # background thread pool for NEXRAD decode+render — keeps heavy MetPy/numpy/scipy
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
        self._loop_timer.setInterval(_loop_interval_ms(500))
        self._loop_timer.timeout.connect(self._advance_loop_frame)

        # pending render scan — latest scan waiting to be rendered.
        self._pending_render_scan = None

        # guard: only one background render in flight at a time.
        self._render_in_flight = False

        self._last_inject_time = 0.0
        self._deferred_inject_result = None
        self._inject_throttle_timer = QTimer()
        self._inject_throttle_timer.setSingleShot(True)
        self._inject_throttle_timer.timeout.connect(self._flush_deferred_inject)

        self.radar_controls.radar_toggled.connect(self._on_radar_toggled)
        self.radar_controls.site_changed.connect(self._on_radar_site_changed)
        self.radar_controls.stations_requested.connect(self._toggle_radar_station_picker)
        self.radar_controls.product_changed.connect(self._on_radar_product_changed)
        self.radar_controls.products_available_changed.connect(
            self._on_radar_products_available
        )
        self.radar_controls.product_availability_changed.connect(
            self._on_radar_product_availability_changed
        )
        self._radar_product_availability: dict[str, bool] = {
            "N0B": True, "N0U": True, "N0C": True, "N0K": True
        }
        self.radar_controls.fetch_requested.connect(self._on_radar_fetch_requested)
        self.radar_controls.frame_requested.connect(self._display_cached_frame)
        self.radar_controls.loop_toggled.connect(self._on_loop_toggled)
        self.radar_controls.speed_changed.connect(self._on_radar_speed_changed)
        self.radar_controls.vad_requested.connect(self._on_vad_requested)
        self.map_widget.radar_station_clicked.connect(self._on_radar_station_clicked)

        self._radar_fetcher.new_data.connect(self._on_radar_data)
        self._radar_fetcher.fetch_error.connect(self._on_radar_error)

        self.map_widget.set_radar_stations(self._radar_station_sites)
        self.radar_controls.set_selected_site("KTLX")

        initial_site = self.radar_controls.current_site()
        self._radar_fetcher.set_site(initial_site)
        self._on_radar_products_available(["N0B", "N0U", "N0C", "N0K"])
        
        # track initial backfill completion
        self._radar_initial_backfill_complete = False
        self._radar_backfill_products_received = set()

        self._sounding_fetcher        = SoundingFetcher(self)
        self._obs_sounding_fetcher    = ObsSoundingFetcher(self)
        self._clamps_sounding_fetcher = ClampsSoundingFetcher(self)
        self._sounding_dialog_obj     = None   # built on first use
        self._nssl_sounding_refresh_timer = QTimer(self)
        self._nssl_sounding_refresh_timer.setInterval(5 * 60 * 1000)
        self._nssl_sounding_refresh_timer.timeout.connect(
            self._refresh_nssl_sounding_if_visible
        )

        # dedicated comparison fetchers — their sounding_ready goes to add_source
        self._comp_hrrr_fetcher = SoundingFetcher(self)
        self._comp_obs_fetcher  = ObsSoundingFetcher(self)
        self._comp_nssl_fetcher = ClampsSoundingFetcher(self)

        self._last_nssl_sset = None   # cached for NSSL proximity checks

        self._sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._obs_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._obs_sounding_fetcher.fetch_error.connect(self._on_sounding_error)
        self._clamps_sounding_fetcher.sounding_ready.connect(self._on_sounding_ready)
        self._clamps_sounding_fetcher.fetch_error.connect(self._on_sounding_error)

        self._comp_hrrr_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_hrrr_fetcher.fetch_error.connect(self._on_sounding_error)
        self._comp_obs_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_obs_fetcher.fetch_error.connect(self._on_sounding_error)
        self._comp_nssl_fetcher.sounding_ready.connect(self._on_comp_sounding_ready)
        self._comp_nssl_fetcher.fetch_error.connect(self._on_sounding_error)

        self.map_widget.sounding_clicked.connect(self._on_sounding_map_click)
        self.map_widget.obs_sounding_station_clicked.connect(self._on_obs_station_click)
        self.sounding_controls.mode_changed.connect(self._on_sounding_mode_changed)

        # pre-build stations GeoJSON once (static asset, ~15 KB)
        self._sounding_stations_geojson = build_stations_geojson()

    def _auto_start_radar(self):
        self._radar_fetcher.start()
        self._radar_fetcher.fetch_now()
        # show fetching status
        site = self.radar_controls.current_site()
        self.status_msg_label.setText(f"Fetching {site} radar data…")
        self._layout_overlays()

    def _radar_data_enabled(self) -> bool:
        return (
            hasattr(self, "radar_controls")
            and self.radar_controls.is_data_enabled()
        )

    def _on_radar_fetch_requested(self):
        if not self._radar_data_enabled():
            return
        self._radar_fetcher.fetch_now()

    def _on_radar_error(self, msg: str):
        if not self._radar_data_enabled():
            return
        self._layer_status["radar"].mark_error(msg)

    def _on_radar_toggled(self, enabled: bool):
        self._set_layer_active("radar", enabled)
        if enabled:
            # bump generation so any renders queued before this toggle-on are
            self._render_generation += 1
            self._radar_fetcher.start()
            self._radar_fetcher.fetch_now()
            site = self.radar_controls.current_site()
            self.status_msg_label.setText(f"Fetching {site} radar data…")
            self._layout_overlays()
        else:
            # stop everything and clear all state when disabled
            self._set_radar_station_picker_visible(False)
            self._loop_timer.stop()
            self.radar_controls.reset_cache_ui()
            self._scan_cache.clear()
            self._current_radar_scan = None
            # bump render generation so any in-flight decode/render and any
            self._render_generation += 1
            self._pending_render_scan = None
            self._render_in_flight = False
            self._deferred_inject_result = None
            self._inject_throttle_timer.stop()
            self._last_inject_time = 0.0
            self._radar_fetcher.reset_history()
            self._radar_fetcher.stop()
            self._radar_overlay.hide(transient=False)
            self.status_msg_label.setText("")
            self._layout_overlays()
            # defer the actual removeLayer + removeSource until the renderer has
            _guard_gen = self._render_generation
            QTimer.singleShot(400, lambda: self._deferred_radar_clear(_guard_gen))

    def _deferred_radar_clear(self, guard_gen: int):
        """Remove the MapLibre radar source/layer after the deferred delay.

        Skipped if the render generation changed since scheduling — meaning the
        user toggled radar back on (which bumps the generation) before the timer
        fired.
        """
        if self._render_generation == guard_gen:
            self._radar_overlay.clear()

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
        self._current_radar_scan = None
        if getattr(self, '_site_change_from_map_click', False):
            # js click handler already set raster-opacity to 0 — just sync
            self._radar_overlay._hidden = True
            self._radar_overlay._current_scan = None
        else:
            # non-click path (auto-site, etc.) — safe to send JS
            self._radar_overlay.hide()
        # reset inject throttle so the first frame for the new site appears immediately
        self._last_inject_time = 0.0
        self._deferred_inject_result = None
        self._inject_throttle_timer.stop()
        if not self._radar_data_enabled():
            self.status_msg_label.setText("")
            self._layout_overlays()
            return
        # show fetching status
        self._radar_initial_backfill_complete = False
        self._radar_backfill_products_received = set()
        self._layer_status["radar"].restart()
        self.status_msg_label.setText(f"Fetching {site} radar data…")
        self._layout_overlays()

    def _on_radar_products_available(self, products: list[str]):
        if not hasattr(self, "_radar_fetcher"):
            return
        # always fetch all 4 products regardless of THREDDS availability probe —
        self._radar_fetcher.set_products(["N0B", "N0U", "N0C", "N0K"])

    def _on_radar_product_availability_changed(self, availability: dict):
        self._radar_product_availability = dict(availability)
        self._update_radar_unavailable_status()

    def _update_radar_unavailable_status(self):
        """If the currently selected radar product is marked unavailable for
        the current site, surface that in the status pill."""
        if not hasattr(self, "radar_controls"):
            return
        product = self.radar_controls.current_product()
        site = self.radar_controls.current_site()
        avail = self._radar_product_availability.get(product, True)
        cur = self.status_msg_label.text()
        prefix = "Radar:"
        if not avail:
            name_map = {"N0C": "Correlation Coefficient", "N0K": "Specific Diff. Phase"}
            name = name_map.get(product, product)
            self.status_msg_label.setText(f"Radar: no {name} data at {site}")
            self._layout_overlays()
        elif cur.startswith("Radar: no "):
            self.status_msg_label.setText("")
            self._layout_overlays()

    def _toggle_radar_station_picker(self):
        self._set_radar_station_picker_visible(not self._radar_station_picker_visible)

    def _set_radar_station_picker_visible(self, visible: bool):
        self._radar_station_picker_visible = visible
        self.map_widget.set_radar_stations_visible(visible)

    def _on_radar_station_clicked(self, site: str):
        # js already called stormSetRadarStationsVisible(false) and set
        self._radar_station_picker_visible = False
        if self._archive:
            # in archive mode, start fetching from the selected station.
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
        if not self._radar_data_enabled():
            self._current_radar_scan = None
            self._radar_overlay.hide()
            self.status_msg_label.setText("")
            self._layout_overlays()
            return
        key = f"{self.radar_controls.current_site()}/{product}"
        cache = self._scan_cache.get(key, [])
        self.radar_controls.reset_cache_ui()
        if cache:
            self.radar_controls.set_cache_size(len(cache))
            self._show_scan(cache[-1])
        else:
            # hide instead of clear to avoid forcing MapLibre to teardown and rebuild
            self._current_radar_scan = None
            self._radar_overlay.hide()
            if not self._radar_product_availability.get(product, True):
                self._update_radar_unavailable_status()
            else:
                self.status_msg_label.setText(f"Fetching radar product {product}…")
                self._layout_overlays()
                self._radar_fetcher.fetch_now()

    def _on_radar_data(self, site: str, product: str, raw_bytes: bytes):
        """Called on the main thread by the fetcher signal.  Returns immediately —
        the actual decode is submitted to a background thread so the UI is never
        blocked by MetPy/numpy work."""
        if not self._radar_data_enabled():
            return
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
        if not self._radar_data_enabled():
            return

        scan_time = scan.scan_time
        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)
        else:
            scan_time = scan_time.astimezone(timezone.utc)
        if datetime.now(timezone.utc) - scan_time > timedelta(minutes=LIVE_RADAR_MAX_AGE_MINUTES):
            log.warning(
                "discarding stale live radar scan: %s/%s %s",
                site,
                product,
                scan_time.strftime("%Y-%m-%d %H:%M:%SZ"),
            )
            return
        scan.scan_time = scan_time
        self._layer_status["radar"].mark_success(scan_time)

        key = f"{site}/{product}"
        cache = self._scan_cache.setdefault(key, [])

        # skip duplicate scan times — THREDDS sometimes returns the same file twice
        if cache and cache[-1].scan_time == scan.scan_time:
            return

        cache.append(scan)

        # trim to 35-minute rolling window, hard cap at 6 scans per product (12 total)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
        while cache and cache[0].scan_time < cutoff:
            cache.pop(0)
        while len(cache) > 6:
            cache.pop(0)

        if scan not in cache:
            log.debug("discarding live radar scan trimmed from cache: %s/%s", site, product)
            return

        log.debug("cache updated: key=%s, n=%d frames", key, len(cache))
        
        # track initial backfill completion
        if not self._radar_initial_backfill_complete:
            self._radar_backfill_products_received.add(product)
            # consider backfill complete when we have at least one scan for each product
            expected_products = {"N0B", "N0U", "N0C", "N0K"}
            if expected_products.issubset(self._radar_backfill_products_received):
                self._radar_initial_backfill_complete = True
                # clear the fetching message
                if self.status_msg_label.text().startswith("Fetching"):
                    self.status_msg_label.setText("")
                    self._layout_overlays()

        # only update display for the currently visible product;
        if product != self.radar_controls.current_product():
            return

        was_live = self.radar_controls.is_at_latest_frame()
        self.radar_controls.set_cache_size(len(cache))

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
        if not self._radar_data_enabled():
            self._pending_render_scan = None
            return
        if self._render_in_flight:
            # don't queue a second render — _on_render_ready will re-check pending
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
            mask_scan = self._velocity_mask_scan(scan)
            from ui.map import radar_gl  # noqa: PLC0415
            if radar_gl.enabled() and radar_gl.supports(scan, mask_scan):
                gl_payload = radar_gl.build_payload(scan)
                if gen == self._render_generation:
                    self._render_ready.emit({"gen": gen, "scan": scan, "gl": gl_payload})
                return
            png_bytes, bounds, elapsed_ms = self._render_scan_to_png(
                scan, grid_size, mask_scan=mask_scan
            )
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

    def _on_render_ready(self, result: dict):
        """Runs on the main thread — injects the pre-rendered PNG into the map."""
        import time as _time
        self._render_in_flight = False
        if result["gen"] != self._render_generation:
            if self._pending_render_scan is not None:
                self._submit_pending_render()
            return
        if not self._radar_data_enabled():
            self._pending_render_scan = None
            return
        scan = result["scan"]
        if scan.product != self.radar_controls.current_product():
            if self._pending_render_scan is not None:
                self._submit_pending_render()
            return

        now = _time.monotonic()
        elapsed_since_inject = (now - self._last_inject_time) * 1000

        if elapsed_since_inject >= self._INJECT_COOLDOWN_MS:
            # enough time has passed — inject immediately
            self._do_inject(result)
        else:
            # too soon — defer this result; timer will flush it
            self._deferred_inject_result = result
            if not self._inject_throttle_timer.isActive():
                remaining = self._INJECT_COOLDOWN_MS - int(elapsed_since_inject)
                self._inject_throttle_timer.start(max(20, remaining))

        # keep the render pipeline moving regardless of inject throttle
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
        if not self._radar_data_enabled():
            return
        self._do_inject(result)

    def _do_inject(self, result: dict):
        """Actually inject the rendered PNG into the map."""
        if not self._radar_data_enabled():
            return
        import time as _time
        scan = result["scan"]
        self._current_radar_scan = scan
        if "gl" in result:
            self._radar_overlay.inject_gl(result["gl"])
        else:
            self._radar_overlay.inject(result["png_bytes"], result["bounds"])
            self._radar_overlay._maybe_adjust_grid(result["elapsed_ms"])
        self._last_inject_time = _time.monotonic()
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"), scan.scan_time)
        if not self._pending_cone_motion_fix:
            self.status_msg_label.setText(scan.label)
        self._layout_overlays()

    def _show_scan(self, scan):
        if not self._radar_data_enabled():
            return
        cached = _cached_scan_png(
            scan, self._radar_overlay._grid_size, self._velocity_mask_scan(scan)
        )
        if cached is None:
            # not drawn yet at this grid size — render off the main thread;
            # _do_inject updates the labels when it lands
            self._pending_render_scan = scan
            self._submit_pending_render()
            return
        self._current_radar_scan = scan
        self._radar_overlay.inject(*cached)
        self.radar_controls.set_scan_time(scan.scan_time.strftime("%H:%MZ"), scan.scan_time)
        if not self._pending_cone_motion_fix:
            self.status_msg_label.setText(scan.label)
        self._layout_overlays()

    def _velocity_mask_scan(self, scan):
        if getattr(scan, "colormap", "") not in ("nws_vel", "nws_cc", "nws_kdp"):
            return None
        site = getattr(scan, "site", "")
        ref_cache = self._scan_cache.get(f"{site}/N0B", [])
        if not ref_cache:
            return None
        return min(
            ref_cache,
            key=lambda ref: abs((ref.scan_time - scan.scan_time).total_seconds()),
        )

    def _display_cached_frame(self, idx: int):
        if not self._radar_data_enabled():
            return
        log.debug("displaying cached frame %d of %d",
                  idx, len(self._scan_cache.get(
                      f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}", []
                  )))
        key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
        cache = self._scan_cache.get(key, [])
        if 0 <= idx < len(cache):
            self._show_scan(cache[idx])

    def _on_radar_speed_changed(self, ms: int):
        self._loop_timer.setInterval(_loop_interval_ms(ms))

    def _on_loop_toggled(self, looping: bool):
        if looping and not self._radar_data_enabled():
            self._loop_timer.stop()
            return
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
        if not self._radar_data_enabled():
            self._loop_timer.stop()
            return
        if not self._loop_step_ready():
            return   # synced CONUS satellite tiles still loading
        key = f"{self.radar_controls.current_site()}/{self.radar_controls.current_product()}"
        cache = self._scan_cache.get(key, [])
        if not cache:
            return
        # wrap around so loop plays continuously
        next_frame = (self.radar_controls.current_frame() + 1) % len(cache)
        self.radar_controls.set_frame(next_frame)
        self._show_scan(cache[next_frame])

    def _current_displayed_radar_scan(self):
        return self._current_radar_scan

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
