"""Observation trails (archive): each vehicle's recent path on the map,
colored by a measured or derived quantity, ported from the storm_ty_edits fork.

The controls are the bottom of the VEHICLES panel (``TrailControls``); the
trails are built by ``core.trails.TrailBuilder`` from each vehicle's whole
day of observations and windowed to the archive clock. Storm-relative
quantities, barbs and time-to-space use the storm track's mean motion
(TRACK). Rebuilds are coalesced to at most one per 250 ms while playing.
"""

from datetime import timedelta

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFrame

from core.derived import QUANTITIES
from core.trails import TrailBuilder, color_stops
from ui import palette as P
from ui.controls.trail_controls import TrailControls

TRAILS_REBUILD_MS = 250


class MainWindowTrailsMixin:

    def _init_trail_controls(self, layout):
        """The trail row at the bottom of the vehicle panel (archive only);
        called from _init_vehicle_panel."""
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {P.DIVIDER}; border: none;")
        layout.addSpacing(2)
        layout.addWidget(divider)
        self.trail_controls = TrailControls(self.vehicle_panel)
        layout.addWidget(self.trail_controls)
        self.trail_controls.settings_changed.connect(self._schedule_trails)
        self.trail_controls.settings_changed.connect(self._on_trail_settings_changed)
        self.trail_controls.track_requested.connect(self._open_track_for_trails)
        self._trail_builder = TrailBuilder()
        self._trails_timer = QTimer(self)
        self._trails_timer.setSingleShot(True)
        self._trails_timer.setInterval(TRAILS_REBUILD_MS)
        self._trails_timer.timeout.connect(self._refresh_trails)

    def _on_trail_settings_changed(self):
        # the trail rows show and hide with the switch: let the panel shrink back
        self._vehicle_panel_size_watermark = (0, 0)
        self._request_layout()

    def _open_track_for_trails(self):
        if hasattr(self, "btn_track"):
            self.btn_track.setChecked(True)

    def _schedule_trails(self, *_args):
        """Throttled: at most one rebuild per TRAILS_REBUILD_MS while the clock runs."""
        timer = getattr(self, "_trails_timer", None)
        if timer is not None and not timer.isActive():
            timer.start()

    def _on_trail_track_changed(self, motion):
        """The storm motion changed (or appeared, or went away)."""
        self.trail_controls.set_track_available(motion is not None)
        self._schedule_trails()

    def _trail_observations(self) -> dict:
        """Each vehicle's whole day of observations (time-ordered), the
        one-second FOFS data where there is some, then the CLAMPS trailers'
        surface met, else the recorded MQTT history."""
        found = {}
        dense = getattr(self, "_archive_vehicle_obs", None)
        if dense is not None:
            found.update(dense.observations_by_vehicle())
        surface = getattr(self, "_archive_clamps_surface", None)
        if surface is not None:
            for trailer, rows in surface.rows.items():
                found.setdefault(trailer, rows)
        mqtt = getattr(self, "_archive_mqtt", None)
        if mqtt is not None:
            for vehicle_id, history in mqtt.vehicle_histories().items():
                if history:
                    found.setdefault(vehicle_id, history)
        return found

    def _refresh_trails(self):
        if not hasattr(self, "trail_controls") or not hasattr(self, "_time_ctrl"):
            return
        if not self.trail_controls.trails_on():
            self.map_widget.set_trails(None)
            return
        quantity, minutes, time_to_space, wind_barbs, sr_barbs = self.trail_controls.settings()
        motion = self.track_storm_motion() if hasattr(self, "_track") else None
        track = list(self._track.points) if hasattr(self, "_track") else []
        end = self._time_ctrl.current_time
        observations = self._trail_observations()
        asos_ids = frozenset()
        if time_to_space and motion is not None:
            # ASOS stations join in time-to-space; their pressure is sea-level
            asos = self._archive_asos_trail_observations()
            observations.update(asos)
            asos_ids = frozenset(asos)
        fc, (vmin, vmax), n = self._trail_builder.build(
            observations, quantity, end - timedelta(minutes=minutes), end,
            track_points=track, motion=motion, time_to_space=time_to_space and motion is not None,
            no_station_pressure=asos_ids,
            wind_barbs=wind_barbs, storm_relative_barbs=sr_barbs and motion is not None,
        )
        info = QUANTITIES[quantity]
        stops = color_stops(quantity, vmin, vmax) if n else []
        self.map_widget.set_trails(fc, stops, info.units, info.label)
        self.trail_controls.set_scale(stops, info.units)
        if n:
            self.trail_controls.set_status("")      # the trails on the map say enough
        elif time_to_space and motion is not None:
            self.trail_controls.set_status("No observations within the storm track's time span")
        else:
            self.trail_controls.set_status(f"No {info.label.lower()} in the last {minutes} min")
