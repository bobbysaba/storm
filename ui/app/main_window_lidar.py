"""Doppler lidar (archive LIDAR), ported from the storm_ty_edits fork: the
providers' lidar scans on the map (a team's archive_lidar and the provider
drawers' lidars, NSSL's lidar truck and CLAMPS trailers: PPI and CSM sector
scans; several providers' are one fetcher, archive/combined.py). The provider's sources name
each lidar (name, marker); STORM's text is generic.

A lidar is picked like a radar station: while LIDAR is open, every place a
lidar scanned from this session is marked (a fixed lidar's site, or each of
a mobile one's stops; core/lidar_scans.locations). The lidars' files are surveyed
in the background once the session's startup has settled, so the places are
ready when LIDAR opens; a guest provider drawer's lidars (NSSL's without its
profile) only once LIDAR is first opened, after the provider's vehicle
tracks have loaded. Picking one moves the clock to its first scan; its
sweeps are drawn as the clock reaches them (each held until the next, up to
10 min), and its scanning periods show under the time slider. STARES opens
the lidars' vertical stares (the fetcher's optional `stares`), whether or
not a lidar is picked.

Files: the provider's (NSSL: providers/nssl/archive/raw_lidar_*); the sweep
image: ui/map/lidar_overlay.py.
"""

import logging
import math
import threading
import time

from ui.app.main_window_util import described
from ui.controls.raw_lidar_controls import RawLidarControls

log = logging.getLogger(__name__)

LIDAR_COLOR = "#9B8FFF"            # the location dots and the slider's scanning strip
_SURVEY_WAIT_MAX_S = 180           # the survey waits this long at most for startup to settle


class MainWindowLidarMixin:

    def _init_lidar_toolbar(self, tb, button=None):
        """LIDAR button and drawer (archive only); called from _init_toolbar,
        or for a provider drawer's lidar with its LIDAR row as *button*.
        Enabled once the date is known to have lidar files."""
        self.btn_raw_lidar = button or tb.add_button("LIDAR", "Doppler lidar", "lidar", overflow=True)
        self._lidar_buttons = [self.btn_raw_lidar]   # and any provider drawer's LIDAR row (_add_lidar_button)
        self._set_lidar_button(False, "Looking for lidar data for this date…")
        self.raw_lidar_controls = RawLidarControls(self._map_container)
        self.raw_lidar_controls.setObjectName("floatingToolbar")
        self.btn_raw_lidar.toggled.connect(self.raw_lidar_controls.toggle_drawer)
        self.btn_raw_lidar.toggled.connect(self._show_lidar_locations)
        self.btn_raw_lidar.toggled.connect(self._on_lidar_drawer_toggled)
        self.raw_lidar_controls.scans_requested.connect(self._open_lidar_scan_window)
        self.raw_lidar_controls.stares_requested.connect(self._open_lidar_stare_window)
        self.raw_lidar_controls.location_requested.connect(self._center_on_lidar_location)
        self.raw_lidar_controls.winds_requested.connect(self._request_archive_wind_profiles)
        self.map_widget.lidar_location_clicked.connect(self._on_lidar_location_clicked)
        self._lidar_render_ready.connect(self._on_lidar_render_ready)

        self._lidar_assets = {}           # instrument -> its files found for the session
        self._lidar_sites = {}            # fixed lidar -> recorded site (its files' header)
        self._lidar_files_by = {}         # instrument -> loaded files (RawLidarRays with .scans)
        self._lidar_large_by = {}         # instrument -> files held back for size
        self._lidar_large_loading = set() # instruments whose held-back files are loading
        self._lidar_locations = []        # every place a lidar scanned from
        self._lidar_survey_done = False
        self._lidar_survey_pending = False # a guest's: waits for the drawer to open
        self._lidar_location = None       # the picked location
        self._lidar_scans = []            # its scans
        # set from the scan window (ui/dialogs/lidar_scan_dialog.py), which
        # also says whether the map draws the scan it shows (off by default)
        self._lidar_field = "velocity"
        self._lidar_min_intensity = None
        self._lidar_draw_on_map = False
        self._lidar_scan_window = None
        self._lidar_generation = 0        # bumped whatever makes a render in flight stale
        self._lidar_drawn_key = None      # (scan, field, min intensity) on the map now
        self._lidar_render_in_flight = False
        self._lidar_render_pending = False
        self._lidar_awaiting_first_render = False
        self._lidar_stare_assets = {}     # instrument -> its stare files for the session
        self._lidar_stare_window = None

    def _add_lidar_button(self, button):
        """Another button for the lidar drawer: a provider drawer's LIDAR row
        while the drawer is a team lidar's LIDAR button's. It follows that
        button (on/off, enabled, tooltip)."""
        self._lidar_buttons.append(button)
        button.setEnabled(self.btn_raw_lidar.isEnabled())
        button.setToolTip(self.btn_raw_lidar.toolTip())
        button.toggled.connect(self.btn_raw_lidar.setChecked)
        self.btn_raw_lidar.toggled.connect(button.setChecked)

    def _set_lidar_status(self, text: str):
        """On the lidar drawer's lidar button; the drawer is laid out again so
        the text isn't cut off."""
        self.raw_lidar_controls.set_status(text)
        self._layout_overlays()

    def _set_lidar_button(self, enabled: bool, tooltip: str):
        for button in self._lidar_buttons:
            button.setEnabled(enabled)
            button.setToolTip(tooltip)

    def _init_archive_lidar(self):
        """Called from _begin_archive_startup, once the archive clock exists."""
        if not hasattr(self, "btn_raw_lidar"):
            return
        self._archive_raw_lidar.set_truck_track(self._lidar_truck_track)
        self._archive_raw_lidar.assets_ready.connect(self._on_lidar_assets_ready)
        self._archive_raw_lidar.sites_ready.connect(self._on_lidar_sites_ready)
        self._archive_raw_lidar.file_ready.connect(self._on_lidar_file_ready)
        self._archive_raw_lidar.large_file.connect(self._on_lidar_large_file)
        self._archive_raw_lidar.instrument_loaded.connect(self._on_lidar_instrument_loaded)
        self._archive_raw_lidar.survey_done.connect(self._on_lidar_survey_done)
        self._archive_raw_lidar.error.connect(self._on_lidar_error)
        self._time_ctrl.time_changed.connect(self._on_lidar_time_changed)
        stares = getattr(self._archive_raw_lidar, "stares", None)
        if stares is not None:
            stares.found.connect(self._on_lidar_stares_found)
            stares.ready.connect(self._on_lidar_stare_ready)
            stares.failed.connect(self._on_lidar_stare_failed)
            if hasattr(stares, "progress"):
                stares.progress.connect(self._on_lidar_stare_progress)
            stares.fetch()
        else:
            self.raw_lidar_controls.set_stares_available(False, "This provider's lidars have no stares")
        self._archive_raw_lidar.fetch()

    def _lidar_truck_track(self, vehicle=None):
        """The observations for the session of the vehicle carrying a
        provider's lidar (*vehicle*, else the fetcher's track_vehicle: NSSL's
        lidar truck): its one-second track (with compass), else its recorded
        MQTT track. Runs on a lidar worker thread; tracks aren't changed once
        loaded."""
        vehicle = vehicle or getattr(self._archive_raw_lidar, "track_vehicle", None)
        if not vehicle:
            return []
        dense = getattr(self, "_archive_vehicle_obs", None)
        track = dense.observations_by_vehicle().get(vehicle, []) if dense is not None else []
        if not track and getattr(self, "_archive_mqtt", None) is not None:
            track = self._archive_mqtt.vehicle_histories().get(vehicle, [])
        if not track and hasattr(self, "_guest_track"):
            track = self._guest_track(vehicle)      # a provider drawer's, for a guest
        return list(track)

    # -- which lidars have files ---------------------------------------------------

    def _on_lidar_assets_ready(self, assets_by_source: dict):
        found, unresolved = {}, set()
        for source in self._archive_raw_lidar.sources:
            assets = assets_by_source.get(source.platform_id)
            if assets is None and source.platform_id in assets_by_source:
                unresolved.add(source.instrument)
            elif assets:
                found.setdefault(source.instrument, []).extend(assets)
        self._lidar_assets = found
        available = bool(found)
        stares_only = not available and bool(self._lidar_stare_assets)
        self._set_lidar_button(
            available or stares_only,
            described("Doppler lidar", self._archive_raw_lidar) if available
            else self._LIDAR_STARES_ONLY if stares_only
            else "Couldn't check for lidar data" if unresolved else "No lidar data for this date")
        if not available:
            self._set_lidar_status(
                "Couldn't check the lidars" if unresolved
                else "No lidar scans this date; see STARES" if stares_only else "No lidar data this date")
            if self.btn_raw_lidar.isChecked() and not stares_only:
                self.btn_raw_lidar.setChecked(False)
            return
        if self._guest_lidar_entries() and not self.btn_raw_lidar.isChecked():
            # a guest provider's lidars (NSSL's, without its profile): nothing
            # downloads until the drawer is opened, and the truck needs the
            # provider's vehicle track, which loads then
            self._lidar_survey_pending = True
            self._set_lidar_status("Finding lidar scans…")
            return
        self._begin_lidar_survey()

    def _begin_lidar_survey(self):
        self._lidar_survey_pending = False
        self._set_lidar_status("Finding lidar scans…")
        self._lidar_activity_begin("lidar-survey", "Loading lidar scans…")
        self._start_lidar_survey(time.monotonic())

    def _on_lidar_drawer_toggled(self, on: bool):
        if not on or not self._lidar_survey_pending:
            return
        for entry in self._guest_lidar_entries():
            self._load_guest_vehicles(entry)
        self._begin_lidar_survey()

    def _guest_lidar_entries(self) -> list:
        """The provider drawers whose lidars are in the lidar drawer and that
        aren't this setup's own team (main_window_provider_drawers.py)."""
        parts = getattr(self._archive_raw_lidar, "parts", [self._archive_raw_lidar])
        return [e for e in getattr(self, "_archive_drawers", ())
                if not e.team and any(getattr(e.drawer, "lidar", None) is p for p in parts)]

    _LIDAR_STARES_ONLY = "Doppler lidar: vertical stares only for this date"

    def _on_lidar_stares_found(self, found: dict):
        self._lidar_stare_assets = {k: v for k, v in found.items() if v}
        stares = self._archive_raw_lidar.stares
        names = [stares.name(k) for k in dict.fromkeys(s.instrument for s in stares.sources)
                 if k in self._lidar_stare_assets]
        unresolved = any(v is None for v in found.values())
        self.raw_lidar_controls.set_stares_available(
            bool(names),
            f"Vertical stares (time-height) from {', '.join(names)}" if names
            else "Couldn't check for lidar stares" if unresolved else "No lidar stares this date")
        if names and not self.btn_raw_lidar.isEnabled():
            # no scans found (yet): stares alone still open the drawer
            self._set_lidar_button(True, self._LIDAR_STARES_ONLY)

    def _start_lidar_survey(self, since: float):
        """Survey once startup has settled (the first radar image drawn, the
        vehicle tracks loaded: the truck's scans are placed from its track),
        so the big files don't load on top of them; a guest provider's
        vehicle tracks too (its lidar truck). After 3 min it goes anyway."""
        startup = hasattr(self, "_archive_loading") and self._archive_loading.startup_pending()
        tracks = (getattr(self, "_archive_vehicle_obs_started", False)
                  and not getattr(self, "_archive_vehicle_obs_loaded", False))
        tracks = tracks or any(e.guest is not None and not e.guest.tracks_loaded
                               for e in self._guest_lidar_entries())
        if (startup or tracks) and time.monotonic() - since < _SURVEY_WAIT_MAX_S:
            self._later(1000, lambda: self._start_lidar_survey(since))
            return
        self._archive_raw_lidar.survey(dict(self._lidar_assets))

    def _on_lidar_sites_ready(self, sites: dict):
        self._lidar_sites = dict(sites)
        for instrument in sites:
            self._rebuild_lidar_locations(instrument)

    def _lidar_session_span(self):
        """The scans that belong to this session: from 12Z on the session day
        (earlier ones are the previous evening's, as for every archive feed;
        archive/session.py) to the end of the timeline."""
        from archive.session import activity_from, session_bounds
        start, end = self._time_ctrl.window
        return max(start, session_bounds(self._archive_time)[0] + activity_from()), end

    # -- the survey: where and when each lidar scanned ---------------------------------

    def _on_lidar_file_ready(self, instrument: str, rays):
        self._lidar_files_by.setdefault(instrument, []).append(rays)
        self._rebuild_lidar_locations(instrument)

    def _on_lidar_large_file(self, instrument: str, asset, size: int):
        log.info("Lidar %s: %s (%.0f MB) loads once picked", instrument, asset.filename, size / 1e6)
        self._lidar_large_by.setdefault(instrument, []).append(asset)
        if self._lidar_location is not None and self._lidar_location.instrument == instrument:
            self._load_lidar_large(instrument)

    def _load_lidar_large(self, instrument: str):
        """The picked lidar's larger files, held back from the survey."""
        assets = self._lidar_large_by.pop(instrument, [])
        if assets:
            self._lidar_large_loading.add(instrument)
            self._archive_raw_lidar.load_files(instrument, assets)

    def _on_lidar_instrument_loaded(self, instrument: str, files: int):
        self._lidar_large_loading.discard(instrument)
        self._maybe_end_lidar_location_loading()
        self._show_lidar_status()

    def _on_lidar_survey_done(self):
        self._lidar_survey_done = True
        self._lidar_activity_end("lidar-survey")
        self._show_lidar_status()

    def _on_lidar_error(self, msg: str):
        log.warning("Raw lidar: %s", msg)
        loc = self._lidar_location
        if loc is not None and msg.startswith(f"{loc.instrument}: "):
            # e.g. a held-back file over the download cap: say why scans are missing
            self.status_msg_label.setText(f"Lidar: {msg[len(loc.instrument) + 2:]}")

    def _show_lidar_status(self):
        """On the lidar button while none is picked."""
        if not self._lidar_survey_done:
            return
        n = len(self._lidar_locations)
        self._set_lidar_status(
            f"Pick a lidar on the map ({n} location{'s' if n != 1 else ''})" if n
            else "No lidar PPI scans this session")

    def _rebuild_lidar_locations(self, instrument: str):
        from core import lidar_scans
        scans = lidar_scans.timeline(self._lidar_files_by.get(instrument, []), *self._lidar_session_span())
        found, unplaced = lidar_scans.locations(instrument, scans, self._lidar_sites.get(instrument))
        if unplaced:
            log.info("Lidar %s: %d scan(s) with no position", instrument, unplaced)
        self._lidar_locations = [loc for loc in self._lidar_locations if loc.instrument != instrument] + found
        chosen = self._lidar_location
        if chosen is not None and chosen.instrument == instrument:
            # the same place, now with whatever the new file added
            same = next((loc for loc in found if chosen.scans and chosen.scans[0] in loc.scans), None)
            if same is not None:
                self._lidar_location = same
                self._lidar_scans = same.scans
                self._show_lidar_location_details()
                self._render_lidar_sweep()
        self._show_lidar_locations(self.btn_raw_lidar.isChecked())
        self._show_lidar_status()

    # -- the location markers ------------------------------------------------------------

    def _lidar_place(self, loc) -> str:
        """'CLAMPS1 trailer · NWC Vehicle Bay', 'Lidar truck · stop 2 of 4'"""
        total = sum(1 for other in self._lidar_locations if other.instrument == loc.instrument)
        name = self._lidar_source_text(loc.instrument, "name")
        return f"{name} · {loc.place or f'stop {loc.number} of {total}'}"

    def _lidar_marker_label(self, loc) -> str:
        """The map marker's text: a mobile lidar's numbers its stops ('Truck 2')."""
        marker = self._lidar_source_text(loc.instrument, "marker")
        mobile = any(s.mobile for s in self._archive_raw_lidar.sources if s.instrument == loc.instrument)
        return f"{marker} {loc.number}" if mobile else marker

    def _lidar_source_text(self, instrument: str, field: str) -> str:
        """A lidar's name or marker from the provider's sources (NSSL's
        "Lidar truck" / "Truck"), else its instrument id."""
        for source in self._archive_raw_lidar.sources:
            if source.instrument == instrument and getattr(source, field, ""):
                return str(getattr(source, field))
        return instrument

    def _lidar_location_text(self, loc, with_distance: bool = True) -> str:
        text = f"{self._lidar_place(loc)} ({loc.lat:.3f}, {loc.lon:.3f})"
        return text + (self._distance_from_case_radar(loc.lat, loc.lon) if with_distance else "")

    def _distance_from_case_radar(self, lat: float, lon: float) -> str:
        station = getattr(getattr(self, "_archive_radar", None), "station", None)
        radar = next((s for s in (self._radar_station_sites or []) if s.get("site_id") == station), None)
        if radar is None:
            return ""
        dlat = math.radians(lat - radar["lat"])
        dlon = math.radians(lon - radar["lon"]) * math.cos(math.radians(radar["lat"]))
        return f", {6371.0 * math.hypot(dlat, dlon):.0f} km from {radar['site_id']}"

    def _show_lidar_locations(self, shown: bool):
        """Every lidar location on the map while LIDAR is open, the picked
        one ringed; hovering one tells when it scanned and how its scan
        directions were set."""
        from core import lidar_scans
        markers = []
        for loc in (self._lidar_locations if shown else []):
            chosen = loc is self._lidar_location
            n = len(loc.scans)
            hover = (f"{self._lidar_place(loc)}\n"
                     f"{lidar_scans.describe_periods(lidar_scans.periods(loc.scans))}"
                     f" · {n} PPI scan{'s' if n != 1 else ''}\n"
                     f"{self._lidar_heading_text(loc)}\n"
                     + ("Showing its scans" if chosen else "Click to show its scans"))
            markers.append({"key": loc.key, "lat": loc.lat, "lon": loc.lon,
                            "label": self._lidar_marker_label(loc),
                            "hover": hover, "selected": chosen})
        self.map_widget.set_lidar_locations(markers)

    @staticmethod
    def _lidar_heading_text(loc) -> str:
        """How a location's scan directions were set: a fixed lidar's are
        north-referenced; a mobile one's come from the heading in its files, or
        from its compass when a file has none."""
        sources = list({id(s.source): s.source for s in loc.scans}.values())
        if not any(getattr(getattr(r, "source", None), "mobile", False) for r in sources):
            return "Heading: fixed site (north-referenced)"
        prov = [r.provenance for r in sources]
        if any(p.get("north_referenced") is False for p in prov):
            return "Heading: unknown (no recorded heading or compass) — not mapped"
        estimated = [p["truck_heading_estimate_deg"] for p in prov
                     if p.get("heading_missing_in_file") and p.get("truck_heading_estimate_deg") is not None]
        if estimated:
            return f"Heading: estimated from the vehicle compass (~{estimated[0]:.0f}°) ⚠ not in the file"
        recorded = [p["truck_heading_deg"] for p in prov if p.get("truck_heading_deg") is not None]
        return f"Heading: recorded in the file ({recorded[0]:.0f}°)" if recorded else "Heading: recorded in the file"

    def _on_lidar_location_clicked(self, key: str):
        loc = next((l for l in self._lidar_locations if l.key == key), None)
        if loc is None:
            return
        self._lidar_location = loc
        self._lidar_scans = loc.scans
        self._lidar_generation += 1
        self._lidar_drawn_key = None
        self.map_widget.clear_lidar_image()
        self._lidar_activity_begin("lidar-location", f"Loading {self._lidar_place(loc)}…")
        self._lidar_awaiting_first_render = True
        self._load_lidar_large(loc.instrument)        # any larger files follow in the background
        # to its first scan, so what was happening then is on screen; the
        # clock moves before the drawer's field list (which redraws) is filled
        self._time_ctrl.pause()
        self._time_ctrl.set_time(loc.start)
        self._show_lidar_location_details()
        self._open_lidar_scan_window()                # picking a lidar opens its scans
        self._show_lidar_locations(self.btn_raw_lidar.isChecked())
        self._render_lidar_sweep()                    # even if the clock was already there
        self.status_msg_label.setText(f"Lidar: {self._lidar_location_text(loc, False)}, "
                                      f"first scan {loc.start:%H:%M:%S} UTC")

    def _show_lidar_location_details(self):
        from core import lidar_scans
        loc = self._lidar_location
        if loc is None:
            self.raw_lidar_controls.set_location(None)
            self._archive_controls.set_coverage([])
            self._layout_overlays()
            return
        spans = lidar_scans.periods(loc.scans)
        n = len(loc.scans)
        details = (f"{self._lidar_location_text(loc)}\n{lidar_scans.describe_periods(spans)} · "
                   f"{n} PPI scan{'s' if n != 1 else ''}\n"
                   f"{self._lidar_heading_text(loc)}\nClick to center the map on it")
        self.raw_lidar_controls.set_location(self._lidar_place(loc), details)
        self._layout_overlays()
        sources = list({id(s.source): s.source for s in loc.scans}.values())
        window = self._lidar_scan_window
        if window is not None and window.isVisible():
            window.set_location(self._lidar_place(loc), loc.scans, self._lidar_heading_text(loc),
                                self._time_ctrl.current_time)
        self.raw_lidar_controls.set_heading_notice(next(
            (r.provenance for r in sources
             if r.provenance.get("heading_missing_in_file") or r.provenance.get("north_referenced") is False),
            None))
        self._archive_controls.set_coverage(spans, LIDAR_COLOR, self._lidar_location_text(loc, False))

    def _center_on_lidar_location(self):
        loc = self._lidar_location
        if loc is not None:
            self.map_widget.fly_to(loc.lat, loc.lon)

    # -- the sweep on the map ----------------------------------------------------------------

    def _open_lidar_scan_window(self):
        """The picked lidar's scans in their own window (SCANS, or picking a
        lidar on the map); its field, threshold and scan are what the map
        draws when its "draw on map" is on."""
        loc = self._lidar_location
        if loc is None:
            return
        from ui.dialogs.lidar_scan_dialog import LidarScanDialog
        window = self._lidar_scan_window
        if window is None:
            from PyQt6.QtWidgets import QWidget
            window = self._lidar_scan_window = LidarScanDialog(self if isinstance(self, QWidget) else None)
            window.draw_on_map_toggled.connect(self._on_lidar_data_toggled)
            window.field_changed.connect(self._on_lidar_field_selected)
            window.min_intensity_changed.connect(self._on_lidar_min_intensity_changed)
            window.scan_changed.connect(lambda _scan: self._render_lidar_sweep())
        window.set_location(self._lidar_place(loc), loc.scans, self._lidar_heading_text(loc),
                            self._time_ctrl.current_time)
        window.show()
        window.raise_()

    def _open_lidar_stare_window(self):
        """STARES: the lidars' vertical stares in their own window, opening
        on the picked lidar when it has some."""
        stares = getattr(self._archive_raw_lidar, "stares", None)
        if stares is None or not self._lidar_stare_assets:
            return
        from ui.dialogs.lidar_stare_dialog import LidarStareDialog
        window = self._lidar_stare_window
        if window is None:
            from PyQt6.QtWidgets import QWidget
            window = self._lidar_stare_window = LidarStareDialog(self if isinstance(self, QWidget) else None)
            window.lidar_picked.connect(stares.load)
            window.set_span(*self._lidar_session_span())
        window.set_time(self._time_ctrl.current_time)
        lidars = [(k, stares.name(k)) for k in dict.fromkeys(s.instrument for s in stares.sources)
                  if k in self._lidar_stare_assets]
        picked = self._lidar_location.instrument if self._lidar_location is not None else None
        window.set_lidars(lidars, picked)
        window.show()
        window.raise_()

    def _on_lidar_stare_ready(self, instrument: str, stare):
        if self._lidar_stare_window is not None:
            self._lidar_stare_window.set_stare(instrument, stare)

    def _on_lidar_stare_progress(self, instrument: str, text: str):
        if self._lidar_stare_window is not None:
            self._lidar_stare_window.set_loading(instrument, text)

    def _on_lidar_stare_failed(self, instrument: str, reason: str):
        log.warning("Lidar stares %s: %s", instrument, reason)
        if self._lidar_stare_window is not None:
            self._lidar_stare_window.set_failed(instrument, reason)

    def _on_lidar_data_toggled(self, on: bool):
        """Draw the scan window's scan on the map, or stop (it stays picked)."""
        self._lidar_draw_on_map = on
        self._lidar_generation += 1
        self._lidar_drawn_key = None
        if not on:
            self.map_widget.clear_lidar_image()
            return
        self._render_lidar_sweep()

    def _on_lidar_field_selected(self, field: str):
        self._lidar_field = field
        self._render_lidar_sweep()

    def _on_lidar_min_intensity_changed(self, value):
        self._lidar_min_intensity = value
        self._render_lidar_sweep()

    def _on_lidar_time_changed(self, when):
        stare_window = self._lidar_stare_window
        if stare_window is not None and stare_window.isVisible():
            stare_window.set_time(when)
        window = self._lidar_scan_window
        if window is not None and window.isVisible():
            window.set_time(when)               # it re-renders the map when its scan changes
        elif self._lidar_scans:
            self._render_lidar_sweep()

    def _render_lidar_sweep(self):
        """Draw the sweep at the clock, but only when that's a different
        sweep (or field, or filter) from the one on the map: a sweep is held
        for minutes, and redrawing it on every clock tick only churns the map."""
        from core.lidar_scans import scan_at
        if not self._lidar_scans or not self._lidar_draw_on_map:
            self._lidar_awaiting_first_render = False
            self._maybe_end_lidar_location_loading()
            return
        window = self._lidar_scan_window
        scan = (window.current_scan() if window is not None and window.isVisible()
                else scan_at(self._lidar_scans, self._time_ctrl.current_time))
        key = (id(scan) if scan is not None else None, self._lidar_field, self._lidar_min_intensity)
        if key == self._lidar_drawn_key:
            return
        self._lidar_generation += 1
        if self._lidar_render_in_flight:
            self._lidar_render_pending = True
            return
        self._lidar_drawn_key = key
        self._lidar_render_in_flight = True
        threading.Thread(
            target=self._bg_render_lidar_sweep,
            args=(scan, self._lidar_field, self._lidar_min_intensity, self._lidar_generation),
            daemon=True, name="lidar-render",
        ).start()

    def _bg_render_lidar_sweep(self, scan, field: str, min_intensity, generation: int):
        """Runs on a background thread: one sweep's PNG, or why there's none."""
        from ui.map.lidar_overlay import render_lidar_to_png
        from ui.map.radar_overlay import RENDER_GRID_SIZE
        if scan is None:
            self._lidar_render_ready.emit({"generation": generation, "error": "no PPI scan now", "quiet": True})
            return
        rays = scan.source
        if field not in rays.fields:
            field = "velocity" if "velocity" in rays.fields else next(iter(rays.fields))
        try:
            png, bounds, metadata = render_lidar_to_png(rays, scan.start, field, RENDER_GRID_SIZE,
                                                        ray_indices=scan.indices, min_intensity=min_intensity)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, ValueError):
                log.error("Lidar sweep render failed: %s", exc, exc_info=True)
            self._lidar_render_ready.emit({"generation": generation, "error": str(exc)})
            return
        metadata["scan"] = scan.describe()
        self._lidar_render_ready.emit({"generation": generation, "png": png, "bounds": bounds,
                                       "metadata": metadata})

    def _on_lidar_render_ready(self, result: dict):
        self._lidar_render_in_flight = False
        current = result["generation"] == self._lidar_generation
        if current:
            self._lidar_awaiting_first_render = False
            self._maybe_end_lidar_location_loading()
        if current and self._lidar_location is not None and self._lidar_draw_on_map:
            if "error" in result:
                if not result.get("quiet"):
                    self.status_msg_label.setText(f"Lidar: {result['error']}")
                self.map_widget.clear_lidar_image()
            else:
                self.map_widget.set_lidar_image(result["png"], result["bounds"])
                meta = result["metadata"]
                self.status_msg_label.setText(
                    f"Lidar: {self._lidar_place(self._lidar_location)} · {meta['scan']} · "
                    f"{meta['field']} {meta['vmin']:g} … {meta['vmax']:g} {meta['units']}".rstrip())
        if self._lidar_render_pending:
            self._lidar_render_pending = False
            self._lidar_drawn_key = None              # what's drawn may be stale: check again
            self._render_lidar_sweep()

    # -- "Loading …" in the archive indicator ------------------------------------------------

    def _lidar_activity_begin(self, key: str, text: str):
        if hasattr(self, "_archive_loading"):
            self._archive_loading.begin(key, text)
            self._layout_overlays()

    def _lidar_activity_end(self, key: str):
        if hasattr(self, "_archive_loading"):
            self._archive_loading.end(key)

    def _maybe_end_lidar_location_loading(self):
        """The picked lidar is ready once its held-back files have loaded and
        its first sweep (or "no sweep now") has come back."""
        loc = self._lidar_location
        if loc is None or (not self._lidar_awaiting_first_render
                           and loc.instrument not in self._lidar_large_loading):
            self._lidar_activity_end("lidar-location")
