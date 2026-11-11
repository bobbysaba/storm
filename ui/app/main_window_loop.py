"""Live loop sync: satellite and vehicles follow the radar loop (FEATURE_PLAN.md step 2)."""

import logging

from ui.loop_sync import RADAR, SATELLITE, SATELLITE_TOLERANCE_S, LoopSync, frame_at

log = logging.getLogger("ui.app.main_window")

# satellite opacity, as a fraction of the user's, while the loop time has no frame
_SATELLITE_NO_FRAME_DIM = 0.3


class MainWindowLoopMixin:

    def _init_loop_sync(self):
        """Live modes only; runs after radar and satellite init (post-startup)."""
        self._loop_sync = LoopSync(parent=self)
        self._loop_satellite_following = False
        self._loop_satellite_frame = None
        self._loop_satellite_dimmed = False
        self._loop_vehicles_shown = False
        self._loop_hidden_vehicles: set[str] = set()

        self._loop_sync.time_changed.connect(self._on_loop_time_changed)
        self._loop_sync.vehicles_changed.connect(lambda _on: self._apply_vehicle_loop_time())
        self.map_widget.satellite_idle.connect(self._loop_sync.mark_tiles_idle)
        self.loop_chip.clicked.connect(self._return_loop_to_live)

        rc = self.radar_controls
        self._loop_sync.set_enabled(rc.is_sync_enabled())
        self._loop_sync.set_vehicles(rc.is_sync_vehicles())
        rc.sync_toggled.connect(self._on_loop_sync_toggled)
        rc.sync_vehicles_toggled.connect(self._loop_sync.set_vehicles)
        # these run after the radar/satellite handlers connected in their own init,
        # so the frame they publish is the one now on screen
        rc.radar_toggled.connect(self._on_loop_radar_toggled)
        for sig in (rc.frame_requested, rc.loop_toggled, rc.site_changed, rc.product_changed):
            sig.connect(lambda *_: self._publish_radar_loop_time())
        self._loop_timer.timeout.connect(self._publish_radar_loop_time)

        sc = self.satellite_controls
        for sig in (sc.frame_requested, sc.loop_toggled):
            sig.connect(lambda *_: self._publish_satellite_loop_time())
        self._satellite_loop_timer.timeout.connect(self._publish_satellite_loop_time)
        sc.mode_changed.connect(self._on_loop_satellite_changed)
        self._satellite_fetcher.frames_updated.connect(
            lambda mode, _frames: self._on_loop_satellite_changed(mode))
        self._update_satellite_sync_state()

    # drivers

    def _publish_radar_loop_time(self):
        """Publish the radar frame on screen, or live; radar drives while its data is on."""
        sync = getattr(self, "_loop_sync", None)
        if sync is None:
            return
        if not self._radar_data_enabled():
            if sync.source == RADAR:
                sync.set_time(None, RADAR)
            return
        rc = self.radar_controls
        key = f"{rc.current_site()}/{rc.current_product()}"
        cache = self._scan_cache.get(key, [])
        t = None
        if cache and (rc.is_looping() or not rc.is_at_latest_frame()):
            t = cache[min(rc.current_frame(), len(cache) - 1)].scan_time
        sync.set_time(t, RADAR)

    def _publish_satellite_loop_time(self):
        """With radar off the satellite loop drives (vehicles follow it)."""
        sync = getattr(self, "_loop_sync", None)
        if sync is None or self._radar_data_enabled():
            return
        sc = self.satellite_controls
        frames = self._satellite_cache.get(sc.current_mode(), [])
        t = None
        if frames and (sc.is_looping() or not sc.is_at_latest_frame()):
            t = frames[min(sc.current_frame(), len(frames) - 1)].timestamp
        sync.set_time(t, SATELLITE)

    def _loop_step_ready(self) -> bool:
        """False while the radar loop should wait for CONUS satellite tiles."""
        sync = getattr(self, "_loop_sync", None)
        return sync is None or sync.ready_to_step()

    def _on_loop_radar_toggled(self, _enabled: bool):
        self._publish_radar_loop_time()
        self._update_satellite_sync_state()

    def _on_loop_sync_toggled(self, enabled: bool):
        self._loop_sync.set_enabled(enabled)
        self._update_satellite_sync_state()
        if self._radar_data_enabled():
            self._publish_radar_loop_time()
        else:
            self._publish_satellite_loop_time()

    def _update_satellite_sync_state(self):
        """Satellite follows (own playback off) while sync is on and radar drives."""
        sync = getattr(self, "_loop_sync", None)
        if sync is None:
            return
        synced = sync.enabled and self._radar_data_enabled()
        was_synced = self.satellite_controls.is_synced()
        self.satellite_controls.set_synced(synced)
        if synced and not was_synced:
            self._apply_satellite_loop_time(sync.loop_time if sync.source == RADAR else None)
        elif was_synced and not synced:
            self._apply_satellite_loop_time(None)
            self._publish_satellite_loop_time()

    def _return_loop_to_live(self):
        sync = getattr(self, "_loop_sync", None)
        if sync is None:
            return
        if sync.source == RADAR:
            self.radar_controls.jump_to_latest()
        else:
            self.satellite_controls.jump_to_latest()

    # followers

    def _on_loop_time_changed(self, t, source: str):
        if source == RADAR:
            self._apply_satellite_loop_time(t)
        self._apply_vehicle_loop_time()
        if t is None:
            self.loop_chip.setVisible(False)
        else:
            self.loop_chip.setText(f"◷ LOOP {t.strftime('%H:%MZ')}")
            self.loop_chip.setVisible(True)
        self._layout_overlays()

    def _on_loop_satellite_changed(self, mode: str):
        """New frames or a new sector: re-pick the frame for the loop time."""
        sync = getattr(self, "_loop_sync", None)
        if sync is None or mode != self.satellite_controls.current_mode():
            return
        if sync.source != RADAR or (sync.loop_time is None and not self._loop_satellite_following):
            return
        self._loop_satellite_frame = None
        self._apply_satellite_loop_time(sync.loop_time)

    def _apply_satellite_loop_time(self, t):
        """Show the satellite frame at or before ``t``; None restores the live frame."""
        sc = self.satellite_controls
        mode = sc.current_mode()
        frames = self._satellite_cache.get(mode, []) if mode else []
        if t is None or not sc.is_synced() or not mode:
            if self._loop_satellite_following:
                self._loop_satellite_following = False
                self._loop_satellite_frame = None
                self._set_loop_satellite_dimmed(False)
                if frames:
                    sc.set_frame(len(frames) - 1)
                    self._render_satellite_frame(frames[-1])
                    sc.set_scan_time(frames[-1].time_str, frames[-1].timestamp)
            return

        self._loop_satellite_following = True
        idx = frame_at([f.timestamp for f in frames], t,
                       SATELLITE_TOLERANCE_S.get(mode, 600)) if frames else None
        if idx is None:
            self._set_loop_satellite_dimmed(True)
            sc.set_scan_time("no frame")
            return
        self._set_loop_satellite_dimmed(False)
        frame = frames[idx]
        sc.set_frame(idx)
        if frame is not self._loop_satellite_frame:
            self._loop_satellite_frame = frame
            self._render_satellite_frame(frame)
            if not frame.png:
                # CONUS: WMS tiles for the new time load asynchronously
                self._loop_sync.mark_tiles_pending()
        sc.set_scan_time(frame.time_str, frame.timestamp)

    def _set_loop_satellite_dimmed(self, dimmed: bool):
        if dimmed == self._loop_satellite_dimmed:
            return
        self._loop_satellite_dimmed = dimmed
        opacity = self.satellite_controls.opacity()
        self.map_widget.set_satellite_opacity(
            opacity * _SATELLITE_NO_FRAME_DIM if dimmed else opacity)

    def _vehicle_loop_active(self) -> bool:
        """True while vehicle markers show loop positions instead of live ones."""
        return getattr(self, "_loop_vehicles_shown", False)

    def _apply_vehicle_loop_time(self):
        """Move vehicle markers to where they were at the loop time, or back to live."""
        sync = getattr(self, "_loop_sync", None)
        t = sync.loop_time if sync is not None and sync.vehicles else None
        if t is None:
            if self._loop_vehicles_shown:
                self._restore_live_vehicles()
            return

        if not self._loop_vehicles_shown:
            self._loop_vehicles_shown = True
            # follow would chase live fixes; plots show the latest obs, not then
            self.map_widget.set_follow(False)
            self.map_widget.set_station_plots_visible(False)
        ts = t.timestamp()
        label = t.strftime("%H:%MZ")
        for v in list(self._vehicles.values()):
            if v.latest_obs is None or not self._should_display_vehicle_obs(v.latest_obs):
                continue
            pos = v.track.position_at(ts)
            if pos is None:
                # no fix yet at that time
                if v.id not in self._loop_hidden_vehicles:
                    self._loop_hidden_vehicles.add(v.id)
                    self.map_widget.remove_vehicle(v.id)
                continue
            self._loop_hidden_vehicles.discard(v.id)
            color = self._vehicle_age_display_state.get(v.id, (self._obs_age_color(v.latest_obs),))[0]
            self.map_widget.add_vehicle(v.id, pos[0], pos[1], color, v.icon_type,
                                        hover_text=f"{v.id} · {label}", past=True)

    def _restore_live_vehicles(self):
        self._loop_vehicles_shown = False
        self._loop_hidden_vehicles.clear()
        for v in list(self._vehicles.values()):
            obs = v.latest_obs
            if obs is None or not self._should_display_vehicle_obs(obs):
                continue
            color = self._obs_age_color(obs)
            self.map_widget.add_vehicle(v.id, v.lat, v.lon, color, v.icon_type,
                                        hover_text=self._archive_vehicle_hover_text(v.id))
        self.map_widget.set_station_plots_visible(self._chk_station_plots.isChecked())
        self.map_widget.set_follow(self._follow_mode)
        target = self._vehicles.get(self._follow_target_id() or "")
        if self._follow_mode and target:
            self.map_widget.follow_move(target.lat, target.lon)
