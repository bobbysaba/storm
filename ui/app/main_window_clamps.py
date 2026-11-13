"""ARCHIVE CLAMPS sources, ported from the storm_ty_edits fork: lidar wind
profiles in the VAD window, the trailers' surface observations on the map,
and the profile sources of the sounding viewer (lidar truck sondes placed
on the truck's track, TROPoe, PERiLS CopterSonde).

All of it comes from NSSL THREDDS (FRDD/CLAMPS, archive/fetchers/clamps_*).
"""

import logging

log = logging.getLogger(__name__)

_LIDAR_TRUCK = "lid1"     # the lidar truck's MQTT id (FOFS "dltruck")


class MainWindowClampsMixin:

    def _init_archive_clamps(self):
        """Called from _begin_archive_startup, once the archive clock exists."""
        from archive.fetchers.clamps_surface_playback import ClampsSurfacePlayback
        from archive.fetchers.clamps_wind_archive_fetcher import ArchiveClampsWindFetcher

        # wind profiles: loaded the first time VAD is asked for, then kept
        self._archive_clamps_wind = ArchiveClampsWindFetcher(self._archive_time, parent=self)
        self._archive_clamps_wind.sets_ready.connect(self._on_archive_clamps_wind_ready)
        self._archive_clamps_wind.error.connect(self._on_archive_clamps_wind_error)

        # the trailers run whether or not they were on MQTT: loaded by date
        self._archive_clamps_surface = ClampsSurfacePlayback(self)
        self._archive_clamps_surface.loaded.connect(self._on_clamps_surface_loaded)
        self._archive_clamps_surface.error.connect(self._on_clamps_surface_error)
        self._time_ctrl.time_changed.connect(self._update_clamps_surface)
        self._archive_clamps_surface.load(self._archive_time)

        self._archive_sounding.set_truck_position(self._lidar_truck_position)
        self._archive_sounding.coptersondes_ready.connect(self._on_archive_coptersondes_ready)
        self.sounding_controls.coptersonde_selected.connect(self._on_sounding_ready)
        self.sounding_controls.set_coptersonde_available(True)

    # -- lidar wind profiles (VAD) ----------------------------------------------

    def _request_archive_clamps_wind(self):
        if self._archive_clamps_wind.sets is None:
            self.status_msg_label.setText(
                "Fetching lidar wind profiles (lidar truck and CLAMPS trailers)…")
        self._archive_clamps_wind.fetch()

    def _on_archive_clamps_wind_ready(self, sets: dict):
        from archive.fetchers.clamps_wind_archive_fetcher import wind_source_name
        if not sets:
            self.status_msg_label.setText("Lidar wind profiles: none for this date")
            return
        platform_id = sorted(sets)[0]
        if len(sets) > 1:
            from PyQt6.QtWidgets import QInputDialog
            names = {f"{wind_source_name(pid)}  ({len(sets[pid])} profiles)": pid for pid in sorted(sets)}
            name, accepted = QInputDialog.getItem(
                self, "Lidar wind profiles", "Instrument", list(names), 0, False)
            if not accepted:
                self.status_msg_label.setText("")
                return
            platform_id = names[name]
        self.status_msg_label.setText(f"Lidar wind profiles: {wind_source_name(platform_id)}")
        from ui.dialogs.vad_dialog import VADDialog
        dlg = VADDialog(wind_source_name(platform_id), parent=self, preloaded_set=sets[platform_id],
                        start_time=self._time_ctrl.current_time)
        dlg.exec()

    def _on_archive_clamps_wind_error(self, msg: str):
        log.warning("Lidar wind profiles unavailable: %s", msg)
        self.status_msg_label.setText(f"Lidar wind profiles unavailable: {msg}")

    # -- trailer surface observations ----------------------------------------------

    def _on_clamps_surface_loaded(self, rows):
        self._archive_clamps_surface.install(rows)
        if rows:
            log.info("CLAMPS surface: %d observations from %s", len(rows),
                     ", ".join(sorted(self._archive_clamps_surface.rows)))
        self._update_clamps_surface(self._time_ctrl.current_time)
        if hasattr(self, "trail_controls"):
            self._schedule_trails()

    def _on_clamps_surface_error(self, msg: str):
        log.warning("CLAMPS surface observations unavailable: %s", msg)

    def _update_clamps_surface(self, when):
        """Each trailer's report at the clock, or off the map once it's stale."""
        surface = self._archive_clamps_surface
        for key in surface.rows:
            obs = surface.at(key, when)
            if obs is None:
                self._hide_vehicle(key)
                continue
            vehicle = self._vehicles.get(key)
            if vehicle is None or vehicle.latest_obs is not obs:
                self.update_vehicle_obs(obs)

    # -- sounding sources ---------------------------------------------------------------

    def _lidar_truck_position(self, when):
        """(lat, lon, fix time) of the lidar truck within a minute of `when`,
        from its one-second FOFS track, else its recorded MQTT track. Runs
        on a sounding worker thread; the tracks aren't changed once loaded."""
        from archive.fetchers.vehicle_obs_archive_fetcher import nearest_fix
        tracks = []
        dense = getattr(self, "_archive_vehicle_obs", None)
        if dense is not None:
            tracks.append(dense.observations_by_vehicle().get(_LIDAR_TRUCK, []))
        mqtt = getattr(self, "_archive_mqtt", None)
        if mqtt is not None:
            tracks.append(mqtt.vehicle_histories().get(_LIDAR_TRUCK, []))
        for track in tracks:
            fix = nearest_fix(track, when) if track else None
            if fix is not None:
                return fix.lat, fix.lon, fix.timestamp
        return None

    def _on_archive_coptersondes_ready(self, sets: dict):
        if self.sounding_controls.active_mode != "coptersonde" or not self.btn_sounding.isChecked():
            return
        self.sounding_controls.set_coptersondes(sets)
        when = self._time_ctrl.current_time
        self.status_msg_label.setText(
            f"CopterSonde: {len(sets)} site(s)" if sets
            else f"CopterSonde: no ascents by {when:%H:%MZ} on {when:%Y-%m-%d}")
