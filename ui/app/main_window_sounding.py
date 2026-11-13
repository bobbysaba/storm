"""Point soundings, the ASOS box, comparison sources and VAD."""

import logging
from data.stations.sounding_utils import nearest_obs_station, nssl_within_radius_km
from ui.app.main_window_util import _SOUNDING_PROMPTS

log = logging.getLogger("ui.app.main_window")


class MainWindowSoundingMixin:

    def _on_sounding_mode_toggled(self, active: bool):
        if not active:
            self._click_modes.exit("sounding")
            # deactivate both sub-modes and clear station layer
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            if hasattr(self, "sounding_controls"):
                self.sounding_controls.reset_mode()
            return
        self._click_modes.enter("sounding")
        # activate the currently selected sub-mode
        mode = self.sounding_controls.active_mode if hasattr(self, "sounding_controls") else "hrrr"
        self._on_sounding_mode_changed(mode)

    def _on_sounding_mode_changed(self, mode: str):
        """Called when the user switches between HRRR, OBS, and NSSL in the sub-bar."""
        if not self.btn_sounding.isChecked():
            return
        self._click_modes.set_prompt(_SOUNDING_PROMPTS.get(mode, ""))
        if self._archive:
            # archive mode: OBS → radiosonde, NSSL → lidar truck sondes (then
            # CLAMPS TROPoe), COPTERSONDE → PERiLS ascents; no HRRR source.
            if mode == "obs":
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_sounding_stations(self._sounding_stations_geojson)
                self.map_widget.set_obs_sounding_mode(True)
            elif mode == "coptersonde":
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_obs_sounding_mode(False)
                self.map_widget.clear_sounding_stations()
                self.status_msg_label.setText("Fetching CopterSonde profiles…")
                self._archive_sounding.fetch_coptersondes()
            else:  # nssl
                self.map_widget.set_sounding_mode(False)
                self.map_widget.set_obs_sounding_mode(False)
                self.map_widget.clear_sounding_stations()
                self.status_msg_label.setText("Fetching NSSL soundings…")
                self._archive_sounding.fetch_nssl_sounding()
            return
        if not hasattr(self, "_sounding_fetcher"):
            return
        if mode == "hrrr":
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            self.map_widget.set_sounding_mode(True)
        elif mode == "obs":
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_sounding_stations(self._sounding_stations_geojson)
            self.map_widget.set_obs_sounding_mode(True)
        else:  # nssl
            self.map_widget.set_sounding_mode(False)
            self.map_widget.set_obs_sounding_mode(False)
            self.map_widget.clear_sounding_stations()
            self.status_msg_label.setText("Fetching NSSL soundings…")
            self._clamps_sounding_fetcher.fetch()

    def _on_sounding_map_click(self, lat: float, lon: float):
        self.status_msg_label.setText("Fetching HRRR sounding…")
        self._sounding_fetcher.fetch(lat, lon)

    def _on_asos_toggled(self, enabled: bool):
        """Enable/disable ASOS; only enter draw mode if no bbox has been set yet."""
        self._surface_fetcher.set_asos_enabled(enabled)
        self._set_layer_active("asos", enabled)
        if enabled and self._surface_fetcher._asos_bbox is None:
            self._enter_asos_bbox()
        elif not enabled:
            self._click_modes.exit("asos_bbox")
            self._remove_surface_source_plots("asos")

    def _enter_asos_bbox(self):
        self._click_modes.enter("asos_bbox", "ASOS — drag a box on the map")
        self.map_widget.set_asos_bbox_mode(True)

    def _exit_asos_bbox(self, _mode: str, _next: str):
        self.map_widget.set_asos_bbox_mode(False)
        self.map_widget.run_js(
            "if(window.stormRestoreAsosMapInteractions) stormRestoreAsosMapInteractions();"
        )

    def _on_asos_box_cancelled(self):
        """Esc / right-click in the map while drawing the ASOS box: leave the
        mode, and with no box yet ASOS has nothing to show, so turn it off."""
        self._click_modes.exit("asos_bbox")
        if self._surface_fetcher._asos_bbox is None and self.surface_controls.asos_enabled():
            self.surface_controls.set_asos_enabled(False)
            self.status_msg_label.setText("ASOS: box canceled")

    def _start_new_asos_bbox(self):
        """Let the user replace the saved ASOS bbox with a freshly drawn one."""
        self._surface_render_generation += 1
        self._surface_fetcher.clear_asos_bbox()
        asos_ids = [
            sid for sid in self._surface_station_ids
            if sid.startswith("surface:asos:")
        ]
        self._surface_station_ids = {
            sid for sid in self._surface_station_ids
            if not sid.startswith("surface:asos:")
        }
        if asos_ids:
            for sid in asos_ids:
                self._surface_layer._cache.pop(sid, None)
            self.map_widget.remove_surface_station_plots_batch(asos_ids)
        if not self.surface_controls.asos_enabled():
            self.surface_controls.set_asos_enabled(True)
        else:
            self._surface_fetcher.set_asos_enabled(True)
            self._set_layer_active("asos", True)
        self._enter_asos_bbox()

    def _on_asos_bbox_selected(self, west: float, south: float, east: float, north: float):
        """Triggered when user finishes an ASOS bbox selection on the map."""
        self.status_msg_label.setText("Fetching ASOS observations…")
        # exit drawing mode but keep the ASOS button checked (auto-refresh stays active)
        self._click_modes.exit("asos_bbox")

        def _start_asos_fetch():
            try:
                self._surface_fetcher.fetch_asos_bbox(west, south, east, north)
            except Exception as exc:
                log.error("ASOS fetch failed to start: %s", exc, exc_info=True)
                self.status_msg_label.setText(f"ASOS fetch error: {exc}")

        self._later(50, _start_asos_fetch)

    def _on_obs_station_click(self, station_id: str, name: str, lat: float, lon: float, elev: float):
        self.status_msg_label.setText(f"Fetching OBS sounding {station_id}…")
        self._obs_sounding_fetcher.fetch(station_id, name, lat, lon, elev)

    def _on_sounding_ready(self, sset):
        self.status_msg_label.setText("")
        if sset.is_nssl:
            self._last_nssl_sset = sset
        if hasattr(self, "_track"):
            # the archive storm track's motion: OBS on the hodograph, SRH obs
            self._sounding_dialog.set_observed_storm_motion(self.track_storm_motion())
        self._sounding_dialog.load(sset)

        available = self._compute_available_sources(sset)
        self._sounding_dialog.set_available_sources(available)

        if sset.is_nssl and not self._archive:
            self._nssl_sounding_refresh_timer.start()
        elif hasattr(self, "_nssl_sounding_refresh_timer"):
            self._nssl_sounding_refresh_timer.stop()

    def _compute_available_sources(self, sset) -> dict:
        """Determine which comparison source pills to show for a given primary SoundingSet."""
        available = {}
        nssl = getattr(self, "_last_nssl_sset", None)

        if sset.source == "hrrr":
            obs = nearest_obs_station(sset.lat, sset.lon)
            if obs:
                available["obs"] = obs
            if nssl and nssl_within_radius_km(nssl.lat, nssl.lon, sset.lat, sset.lon):
                available["nssl"] = {}

        elif sset.source == "obs":
            available["hrrr"] = {"lat": sset.lat, "lon": sset.lon}
            if nssl and nssl_within_radius_km(nssl.lat, nssl.lon, sset.lat, sset.lon):
                available["nssl"] = {}

        elif sset.source in ("nssl", "clamps_tropoe", "coptersonde"):
            if sset.lat != 0.0 or sset.lon != 0.0:
                available["hrrr"] = {"lat": sset.lat, "lon": sset.lon}
                obs = nearest_obs_station(sset.lat, sset.lon)
                if obs:
                    available["obs"] = obs

        if self._archive:
            # the comparison fetchers read "now", and archive has no HRRR source
            available.pop("hrrr", None)
        return available

    def _on_comp_sounding_ready(self, sset):
        """Called when a comparison (secondary) sounding finishes fetching."""
        self.status_msg_label.setText("")
        if sset.is_nssl:
            self._last_nssl_sset = sset
        self._sounding_dialog.add_source(sset)

    def _on_comparison_source_requested(self, source: str, meta: dict):
        """Triggered when the user clicks an inactive pill in the sounding dialog."""
        if source == "hrrr":
            lat = meta.get("lat", 0.0)
            lon = meta.get("lon", 0.0)
            if lat == 0.0 and lon == 0.0:
                return
            self.status_msg_label.setText(f"Fetching HRRR comparison sounding…")
            self._comp_hrrr_fetcher.fetch(lat, lon)
        elif source == "obs":
            sid  = meta.get("station_id", "")
            name = meta.get("name", "")
            lat  = meta.get("lat", 0.0)
            lon  = meta.get("lon", 0.0)
            elev = meta.get("elev", 0.0)
            if not sid:
                return
            self.status_msg_label.setText(f"Fetching OBS comparison sounding {sid}…")
            self._comp_obs_fetcher.fetch(sid, name, lat, lon, elev)
        elif source == "nssl":
            self.status_msg_label.setText("Fetching NSSL comparison sounding…")
            self._comp_nssl_fetcher.fetch()

    def _on_sounding_error(self, msg: str):
        self.status_msg_label.setText(f"Sounding error: {msg}")

    @property
    def _sounding_dialog(self):
        """Sounding dialog, built on first use so metpy loads only when needed."""
        if self._sounding_dialog_obj is None:
            from ui.sounding.dialog import SoundingDialog  # noqa: PLC0415
            self._sounding_dialog_obj = SoundingDialog(self)
            if not self._archive:
                self._sounding_dialog_obj.source_requested.connect(
                    self._on_comparison_source_requested
                )
        return self._sounding_dialog_obj

    def _refresh_nssl_sounding_if_visible(self):
        if self._archive:
            self._nssl_sounding_refresh_timer.stop()
            return
        dlg = self._sounding_dialog_obj
        sset = getattr(dlg, "_sset", None)
        if (
            dlg is None
            or not dlg.isVisible()
            or sset is None
            or not sset.is_nssl
        ):
            self._nssl_sounding_refresh_timer.stop()
            return
        self._clamps_sounding_fetcher.fetch()

    def _on_vad_requested(self):
        """Open the VAD wind-profile window: live NEXRAD VAD for the current
        radar site, or in ARCHIVE the session's lidar wind profiles (the
        lidar truck and the CLAMPS trailers)."""
        if self._archive:
            self._request_archive_clamps_wind()
            return
        site = self.radar_controls.current_site()
        from ui.dialogs.vad_dialog import VADDialog
        dlg = VADDialog(site, parent=self)
        dlg.exec()
