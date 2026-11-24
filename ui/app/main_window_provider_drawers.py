"""A provider's own ARCHIVE button and drawer (the archive_drawer hook;
https://bobbysaba.com/docs/storm/extend/providers/): NSSL's, for every team, with its public THREDDS data.

The drawer's objects are the same kinds the shared instrument hooks hand
over. Its lidars and wind profiles join any team provider's in ARCHIVE's
one lidar drawer and its WINDS (archive/combined.py); the provider's LIDAR
row opens that drawer, as a child when no team lidar has a LIDAR button of
its own. Its soundings are its own, beside the SOUNDING drawer's.

Vehicles: when the provider is the setup's own team (NSSL's profile, team =
true) its traffic comes through [archive] history as usual, VEHICLES starts
on, and turning it off hides the archive's vehicles. Otherwise VEHICLES
starts off; turning it on loads the provider's recorded traffic (vehicles
and scan sectors only) and one-second obs here, and its surface playback,
every vehicle tagged with the provider's tag ("NSSL probe1").
"""
from __future__ import annotations

import copy
import logging
from types import SimpleNamespace

import settings
from settings import code_providers
from ui.controls.provider_drawer_controls import ProviderDrawerControls

log = logging.getLogger(__name__)


class MainWindowProviderDrawersMixin:

    # -- set up -------------------------------------------------------------------------

    def _create_archive_drawers(self, archive_time):
        """Each provider's archive_drawer() object, before the toolbar is
        built (ARCHIVE only): [SimpleNamespace(provider, drawer, ...)]."""
        self._archive_drawers = []
        if archive_time is None:
            return
        for provider, drawer in code_providers.every_hook(
                settings.SETTINGS, "archive_drawer", archive_time=archive_time, parent=self):
            self._archive_drawers.append(SimpleNamespace(
                provider=provider, drawer=drawer, team=bool(getattr(drawer, "team", False)),
                tag=str(getattr(drawer, "tag", "") or code_providers.label(provider)),
                vehicles_on=bool(getattr(drawer, "team", False)), guest=None,
                controls=None, button=None))

    def _init_provider_drawer_toolbar(self, tb):
        """A button and drawer per provider drawer; called from _init_toolbar
        after RADAR (and LIDAR)."""
        for entry in self._archive_drawers:
            drawer = entry.drawer
            entry.button = tb.add_button(drawer.label, f"{drawer.label}'s archive data", "instruments")
            entry.controls = ProviderDrawerControls(drawer.items, self._map_container)
            entry.controls.setObjectName("floatingToolbar")
            entry.button.toggled.connect(entry.controls.toggle_drawer)
            entry.controls.item_toggled.connect(lambda key, on, e=entry: self._on_drawer_item_toggled(e, key, on))
            entry.controls.item_requested.connect(lambda key, e=entry: self._on_drawer_item_requested(e, key))
            entry.controls.uas_selected.connect(self._on_sounding_ready)
            vehicles = entry.controls.buttons.get("vehicles")
            if vehicles is not None:
                vehicles.blockSignals(True)
                vehicles.setChecked(entry.vehicles_on)
                vehicles.blockSignals(False)
            lidar = entry.controls.buttons.get("lidar")
            if lidar is not None:
                if getattr(self, "_lidar_from_drawer", None) is drawer:
                    self._init_lidar_toolbar(tb, button=lidar)
                elif hasattr(self, "btn_raw_lidar"):
                    # the lidar drawer is the team's LIDAR button's (or another
                    # drawer's): this row opens the same one
                    self._add_lidar_button(lidar)
                else:
                    lidar.setEnabled(False)
            drawer.availability_ready.connect(lambda found, e=entry: self._on_drawer_availability(e, found))

    def _init_archive_drawers(self):
        """Called from _begin_archive_startup, once the archive clock exists."""
        from ui.sounding import theme
        for entry in self._archive_drawers:
            soundings = getattr(entry.drawer, "soundings", None)
            if soundings is not None:
                soundings.sounding_ready.connect(self._on_sounding_ready)
                soundings.fetch_error.connect(self._on_archive_sounding_error)
                if hasattr(soundings, "uas_ready"):
                    soundings.uas_ready.connect(lambda sets, e=entry: self._on_drawer_uas_ready(e, sets))
                self._time_ctrl.time_changed.connect(soundings.on_time_changed)
                soundings.on_time_changed(self._time_ctrl.current_time)
                vehicle = getattr(soundings, "track_vehicle", None)
                if vehicle and hasattr(soundings, "set_truck_position"):
                    soundings.set_truck_position(lambda when, v=vehicle: self._archive_position(v, when))
                theme.set_source_labels(getattr(soundings, "pill_labels", None) or {})
                if not getattr(self, "_provider_sounding_label", None):
                    theme.set_provider_label(entry.drawer.label)
            entry.drawer.check_availability()

    # -- the drawer's items -----------------------------------------------------------------

    def _on_drawer_availability(self, entry, found: dict) -> None:
        # lidar is the lidar survey's to say (_on_lidar_assets_ready)
        found = {k: v for k, v in found.items() if k != "lidar"}
        entry.controls.set_availability(found)
        entry.button.setEnabled(entry.controls.any_available())

    def _on_drawer_item_requested(self, entry, key: str) -> None:
        soundings = entry.drawer.soundings
        if key == "sounding":
            self._load_guest_vehicles(entry)      # the radiosondes are placed on the truck's track
        label = entry.drawer.label
        if key == "sounding":
            self.status_msg_label.setText(f"Fetching {label} radiosondes…")
            soundings.fetch_provider_sounding()
        elif key == "retrievals":
            self.status_msg_label.setText(f"Fetching {label} retrievals…")
            soundings.fetch_retrievals()
        elif key == "uas":
            name = str(getattr(soundings, "uas_name", "") or "UAS")
            self.status_msg_label.setText(f"Fetching {name} profiles…")
            soundings.fetch_uas()

    def _on_drawer_uas_ready(self, entry, sets: dict) -> None:
        entry.controls.set_uas_sets(sets)
        when = self._time_ctrl.current_time
        name = str(getattr(entry.drawer.soundings, "uas_name", "") or "UAS")
        self.status_msg_label.setText(
            f"{name}: {len(sets)} site(s)" if sets
            else f"{name}: no ascents by {when:%H:%MZ} on {when:%Y-%m-%d}")

    def _on_drawer_item_toggled(self, entry, key: str, on: bool) -> None:
        if key == "vehicles":
            self._set_drawer_vehicles(entry, on)
        elif key == "lidar" and on:
            self._load_guest_vehicles(entry)      # the truck's scans are placed on its track

    # -- vehicles ---------------------------------------------------------------------------

    def _vehicle_hidden_by_drawer(self, vehicle_id: str) -> bool:
        """A vehicle a drawer's VEHICLES is off for: every archive vehicle
        when it's the team's own provider, else the ones it tagged."""
        for entry in getattr(self, "_archive_drawers", ()):
            if not entry.vehicles_on and (entry.team or vehicle_id.startswith(f"{entry.tag} ")):
                return True
        return False

    def _set_drawer_vehicles(self, entry, on: bool) -> None:
        entry.vehicles_on = on
        when = self._time_ctrl.current_time
        if not on:
            for vehicle_id in list(self._vehicles):
                if self._vehicle_hidden_by_drawer(vehicle_id):
                    self._hide_vehicle(vehicle_id)
            if not entry.team:
                for vehicle_id in [v for v in self._scan_sectors if v.startswith(f"{entry.tag} ")]:
                    self._scan_sectors.pop(vehicle_id, None)
                self._refresh_scan_sectors()
            return
        if entry.team:
            # the team's own vehicles: send them again
            for reader in (getattr(self, "_archive_mqtt", None), getattr(self, "_archive_vehicle_obs", None)):
                if reader is not None:
                    reader.resend(when)
            if getattr(self, "_archive_surface_playback", None) is not None:
                self._update_surface_playback(when)
        elif entry.guest is None:
            self._load_guest_vehicles(entry)
        else:
            entry.guest.mqtt.resend(when)
            entry.guest.dense.resend(when)
            entry.guest.shown.clear()
            self._update_guest_surface(entry, when)

    def _tagged(self, entry, obs):
        tagged = copy.copy(obs)
        tagged.vehicle_id = f"{entry.tag} {obs.vehicle_id}"
        return tagged

    def _load_guest_vehicles(self, entry) -> None:
        """A guest's provider vehicles, loaded the first time VEHICLES is
        turned on, or LIDAR or SOUNDING need the truck's track: the
        provider's recorded vehicles and scan sectors, its one-second obs and
        its surface playback. Shown (tagged) only while VEHICLES is on."""
        if entry.team or entry.guest is not None:
            return
        from archive.fetchers.mqtt_reader import ArchiveMQTTReader
        from archive.fetchers.vehicle_obs_archive_fetcher import ArchiveVehicleObsFetcher
        drawer = entry.drawer
        guest = entry.guest = SimpleNamespace(
            mqtt=ArchiveMQTTReader(self._archive_time, sources=list(drawer.history_sources), parent=self,
                                   renames=getattr(drawer, "vehicle_renames", None)),
            dense=ArchiveVehicleObsFetcher(self._archive_time, sources=list(drawer.vehicle_obs_sources),
                                           parent=self),
            surface=getattr(drawer, "surface", None), shown={}, tracks_loaded=False)
        guest.mqtt.vehicle_position.connect(lambda obs, e=entry: self._on_guest_position(e, obs))
        guest.mqtt.vehicles_cleared.connect(lambda e=entry: self._clear_guest_vehicles(e))
        guest.mqtt.scan_sector_received.connect(lambda scan, e=entry: self._on_guest_scan_sector(e, scan))
        guest.mqtt.loaded.connect(lambda e=entry: e.guest.dense.load(e.guest.mqtt.vehicle_metadata()))
        guest.dense.observation_ready.connect(lambda obs, e=entry: self._on_guest_dense_position(e, obs))
        guest.dense.load_finished.connect(lambda ids, e=entry: self._on_guest_tracks_loaded(e, ids))
        self._time_ctrl.time_changed.connect(guest.mqtt.on_time_changed)
        self._time_ctrl.time_changed.connect(guest.dense.on_time_changed)
        guest.mqtt.on_time_changed(self._time_ctrl.current_time)
        guest.mqtt.load()
        if guest.surface is not None:
            guest.surface.loaded.connect(lambda rows, e=entry: self._on_guest_surface_loaded(e, rows))
            self._time_ctrl.time_changed.connect(lambda when, e=entry: self._update_guest_surface(e, when))
            guest.surface.load(self._archive_time)
        if entry.vehicles_on:
            self.status_msg_label.setText(f"Loading {drawer.label} vehicles…")

    def _on_guest_tracks_loaded(self, entry, vehicle_ids) -> None:
        entry.guest.tracks_loaded = True
        label = entry.drawer.label
        if self.status_msg_label.text() == f"Loading {label} vehicles…":
            n = len(vehicle_ids or ())
            self.status_msg_label.setText(f"{label} vehicles: {n} with one-second obs" if n
                                          else f"{label} vehicles: no one-second obs this date")

    def _guest_track(self, vehicle_id: str) -> list:
        """A guest provider vehicle's observations for the session (its
        one-second track, else its recorded positions), by its own ID; [] when
        no drawer has loaded it. The lidar and the radiosondes are placed on
        the truck's."""
        for entry in getattr(self, "_archive_drawers", ()):
            if entry.guest is None:
                continue
            track = entry.guest.dense.observations_by_vehicle().get(vehicle_id, [])
            if not track:
                track = entry.guest.mqtt.vehicle_histories().get(vehicle_id, [])
            if track:
                return list(track)
        return []

    def _on_guest_position(self, entry, obs) -> None:
        if not entry.vehicles_on:
            return
        if entry.guest.dense.has_fresh_observation(obs.vehicle_id, self._time_ctrl.current_time):
            return
        self.update_vehicle_obs(self._tagged(entry, obs))

    def _on_guest_dense_position(self, entry, obs) -> None:
        if entry.vehicles_on and entry.guest.dense.has_fresh_observation(obs.vehicle_id,
                                                                         self._time_ctrl.current_time):
            self.update_vehicle_obs(self._tagged(entry, obs))

    def _on_guest_scan_sector(self, entry, scan) -> None:
        if entry.vehicles_on:
            tagged = copy.copy(scan)
            tagged.vehicle_id = f"{entry.tag} {scan.vehicle_id}"
            self._recv_remote_scan_sector(tagged)

    def _clear_guest_vehicles(self, entry) -> None:
        """A backward jump: the guest's vehicles are sent again from scratch."""
        for vehicle_id in [v for v in self._vehicles if v.startswith(f"{entry.tag} ")]:
            self._hide_vehicle(vehicle_id)
        for vehicle_id in [v for v in self._scan_sectors if v.startswith(f"{entry.tag} ")]:
            self._scan_sectors.pop(vehicle_id, None)
        self._refresh_scan_sectors()

    def _on_guest_surface_loaded(self, entry, rows) -> None:
        entry.guest.surface.install(rows)
        self._update_guest_surface(entry, self._time_ctrl.current_time)

    def _update_guest_surface(self, entry, when) -> None:
        surface = entry.guest.surface if entry.guest is not None else None
        if surface is None or not entry.vehicles_on:
            return
        for key in surface.rows:
            obs = surface.at(key, when)
            if obs is None:
                entry.guest.shown.pop(key, None)
                self._hide_vehicle(f"{entry.tag} {key}")
            elif entry.guest.shown.get(key) is not obs:
                entry.guest.shown[key] = obs
                self.update_vehicle_obs(self._tagged(entry, obs))
