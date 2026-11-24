"""ARCHIVE's instruments from a provider (NSSL's lidar truck and CLAMPS
trailers): lidar wind profiles in the VAD window, fixed instruments'
surface observations on the map, placing the provider's soundings on a
vehicle's track, and its UAS ascents (settings/code_providers.py:
archive_wind_profiles, archive_surface_playback, archive_sounding_fetcher).
Each object names its own instruments; STORM's text is generic.
"""

import logging

from settings import code_providers
from ui.app.main_window_util import described as _described

log = logging.getLogger(__name__)


class MainWindowInstrumentsMixin:

    def _init_archive_instruments(self):
        """Called from _begin_archive_startup, once the archive clock exists:
        whichever of these the providers offer."""
        # wind profiles: loaded the first time VAD or WINDS is asked for,
        # then kept. Every provider's, as one (archive/combined.py); the
        # radar's VAD only when a team provider has some, the lidar drawer's
        # WINDS for a provider drawer's
        import settings
        from archive import combined
        shared = [found for _provider, found in code_providers.every_hook(
            settings.SETTINGS, "archive_wind_profiles", archive_time=self._archive_time, parent=self)]
        in_drawers = [e.drawer.wind_profiles for e in getattr(self, "_archive_drawers", ())
                      if getattr(e.drawer, "wind_profiles", None) is not None]
        self._archive_wind_profiles = combined.wind_profiles(shared + in_drawers, parent=self)
        if self._archive_wind_profiles is not None:
            self._archive_wind_profiles.sets_ready.connect(self._on_archive_wind_profiles_ready)
            self._archive_wind_profiles.error.connect(self._on_archive_wind_profiles_error)
        if shared:
            self.radar_controls.set_vad_available(
                True, _described("Lidar wind profiles", self._archive_wind_profiles))
        else:
            self.radar_controls.set_vad_available(False)

        # fixed instruments run whether or not they were on MQTT: loaded by date
        self._archive_surface_playback = code_providers.provided("archive_surface_playback", parent=self)
        if self._archive_surface_playback is None:
            # the team's own provider drawer's (NSSL's profile); a guest's
            # load with its VEHICLES (main_window_provider_drawers.py)
            self._archive_surface_playback = next(
                (e.drawer.surface for e in getattr(self, "_archive_drawers", ())
                 if e.team and getattr(e.drawer, "surface", None)),
                None)
        if self._archive_surface_playback is not None:
            self._archive_surface_playback.loaded.connect(self._on_surface_playback_loaded)
            self._archive_surface_playback.error.connect(self._on_surface_playback_error)
            self._time_ctrl.time_changed.connect(self._update_surface_playback)
            self._archive_surface_playback.load(self._archive_time)

        # what a provider's sounding fetcher can do besides OBS
        sounding = self._archive_sounding
        vehicle = getattr(sounding, "track_vehicle", None)
        if vehicle and hasattr(sounding, "set_truck_position"):
            sounding.set_truck_position(lambda when: self._archive_position(vehicle, when))
        if hasattr(sounding, "uas_ready"):
            sounding.uas_ready.connect(self._on_archive_uas_ready)
            self.sounding_controls.uas_selected.connect(self._on_sounding_ready)
            self.sounding_controls.set_uas_available(
                _uas_name(sounding).upper(), getattr(sounding, "uas_tooltip", ""))
        from ui.sounding import theme
        theme.set_source_labels(getattr(sounding, "pill_labels", None) or {})

    # -- lidar wind profiles (VAD) ----------------------------------------------

    def _request_archive_wind_profiles(self):
        if self._archive_wind_profiles is None:
            self.status_msg_label.setText("Lidar wind profiles: none offered")
            return
        if self._archive_wind_profiles.sets is None:
            self.status_msg_label.setText(
                f"Fetching {_described('lidar wind profiles', self._archive_wind_profiles)}…")
        self._archive_wind_profiles.fetch()

    def _on_archive_wind_profiles_ready(self, sets: dict):
        wind_source_name = self._archive_wind_profiles.source_name
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

    def _on_archive_wind_profiles_error(self, msg: str):
        log.warning("Lidar wind profiles unavailable: %s", msg)
        self.status_msg_label.setText(f"Lidar wind profiles unavailable: {msg}")

    # -- fixed instruments' surface observations ---------------------------------

    def _on_surface_playback_loaded(self, rows):
        self._archive_surface_playback.install(rows)
        if rows:
            log.info("Instrument surface obs: %d observations from %s", len(rows),
                     ", ".join(sorted(self._archive_surface_playback.rows)))
        self._update_surface_playback(self._time_ctrl.current_time)
        if hasattr(self, "trail_controls"):
            self._schedule_trails()

    def _on_surface_playback_error(self, msg: str):
        log.warning("Instrument surface observations unavailable: %s", msg)

    def _update_surface_playback(self, when):
        """Each instrument's report at the clock, or off the map once it's stale."""
        surface = self._archive_surface_playback
        if surface is None:
            return
        for key in surface.rows:
            obs = surface.at(key, when)
            if obs is None:
                self._hide_vehicle(key)
                continue
            vehicle = self._vehicles.get(key)
            if vehicle is None or vehicle.latest_obs is not obs:
                self.update_vehicle_obs(obs)

    # -- sounding sources ---------------------------------------------------------------

    def _archive_position(self, vehicle_id: str, when):
        """(lat, lon, fix time) of a vehicle (the provider's lidar truck)
        within a minute of `when`, from its one-second vehicle-obs track,
        else its recorded MQTT track. Runs on a sounding worker thread; the
        tracks aren't changed once loaded."""
        from archive.vehicle_obs import nearest_fix
        tracks = []
        dense = getattr(self, "_archive_vehicle_obs", None)
        if dense is not None:
            tracks.append(dense.observations_by_vehicle().get(vehicle_id, []))
        mqtt = getattr(self, "_archive_mqtt", None)
        if mqtt is not None:
            tracks.append(mqtt.vehicle_histories().get(vehicle_id, []))
        if hasattr(self, "_guest_track"):
            tracks.append(self._guest_track(vehicle_id))      # a provider drawer's, for a guest
        for track in tracks:
            fix = nearest_fix(track, when) if track else None
            if fix is not None:
                return fix.lat, fix.lon, fix.timestamp
        return None

    def _on_archive_uas_ready(self, sets: dict):
        if self.sounding_controls.active_mode != "uas" or not self.btn_sounding.isChecked():
            return
        self.sounding_controls.set_uas_sets(sets)
        when = self._time_ctrl.current_time
        name = _uas_name(self._archive_sounding)
        self.status_msg_label.setText(
            f"{name}: {len(sets)} site(s)" if sets
            else f"{name}: no ascents by {when:%H:%MZ} on {when:%Y-%m-%d}")


def _uas_name(sounding) -> str:
    """The provider's name for its UAS (its sounding fetcher's uas_name:
    NSSL's "CopterSonde")."""
    return str(getattr(sounding, "uas_name", "") or "UAS")
