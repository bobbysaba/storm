"""ARCHIVE surface stations and damage paths, both picked by drawing a box
on the map (the live ASOS box, ui/map/map_template.html), ported from the
storm_ty_edits fork.

SURFACE > ASOS replays IEM's ASOS history on the archive clock
(archive/fetchers/asos_archive_fetcher.py); its plots are the live station
plots, colored by report age at the clock. HAZARDS > DAMAGE PATHS draws the
NWS damage-survey tornado paths for the session's date
(archive/fetchers/damage_paths_archive_fetcher.py). Each has its own click
mode, so the box goes to whichever button asked for it; backing out of the
box with nothing loaded turns that button off again.
"""

import logging
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.qt_signals import emit_alive

log = logging.getLogger(__name__)

ASOS_BOX_PROMPT = "ASOS — drag a box on the map"
DAMAGE_BOX_PROMPT = "Damage paths — drag a box on the map"
_ASOS_PUSH_CHUNK = 24          # plots per map update while rendering


class _AsosRenderSignals(QObject):
    ready = pyqtSignal(int, list)       # generation, [(plot id, lat, lon, fingerprint, png, name)]


def _plot_id(station_id: str) -> str:
    # the "surface:asos:" prefix gives the plots ASOS's hourly age colors
    return f"surface:asos:{station_id}"


class MainWindowArchiveSurfaceMixin:

    def _init_archive_surface(self):
        """Called from _begin_archive_startup, once the archive clock exists."""
        from archive.fetchers.asos_archive_fetcher import ArchiveAsosFetcher
        from archive.fetchers.damage_paths_archive_fetcher import ArchiveDamagePathsFetcher

        cm = self._click_modes
        # replaces live ASOS's exit: here a box backed out of turns ASOS off
        cm.register("asos_bbox", self._exit_archive_asos_box)
        cm.register("damage_box", self._exit_damage_box)
        self.map_widget.asos_bbox_selected.connect(self._on_archive_box_drawn)
        self.map_widget.asos_box_cancelled.connect(self._on_archive_box_cancelled)

        self._archive_asos = ArchiveAsosFetcher(self._archive_time, parent=self)
        self._archive_asos_latest: dict[str, tuple] = {}      # plot id -> (name, obs) at the clock
        self._archive_asos_drawn: dict[str, tuple] = {}       # plot id -> fingerprint on the map
        self._archive_asos_generation = 0
        self._archive_asos_render_busy = False
        self._archive_asos_render_signals = _AsosRenderSignals(self)
        self._archive_asos_render_signals.ready.connect(self._on_archive_asos_rendered)
        self._archive_asos_flush_timer = QTimer(self)
        self._archive_asos_flush_timer.setSingleShot(True)
        self._archive_asos_flush_timer.setInterval(0)
        self._archive_asos_flush_timer.timeout.connect(self._flush_archive_asos_plots)
        self._archive_asos.station_updated.connect(self._on_archive_asos_station_updated)
        self._archive_asos.station_removed.connect(self._on_archive_asos_station_removed)
        self._archive_asos.stations_cleared.connect(self._clear_archive_asos_plots)
        self._archive_asos.load_started.connect(self._on_archive_asos_load_started)
        self._archive_asos.load_finished.connect(self._on_archive_asos_load_finished)
        self._archive_asos.error.connect(self._on_archive_asos_error)
        self._time_ctrl.time_changed.connect(self._archive_asos.on_time_changed)
        # after the fetcher: reports that didn't change still age with the clock
        self._time_ctrl.time_changed.connect(self._schedule_archive_asos_flush)

        self.surface_controls.asos_toggled.connect(self._on_archive_asos_toggled)
        self.surface_controls.asos_bbox_requested.connect(self._enter_archive_asos_box)
        self.surface_controls.plots_toggled.connect(self.map_widget.set_surface_station_plots_visible)

        self._archive_damage_paths = ArchiveDamagePathsFetcher(self._archive_time, parent=self)
        self._damage_paths_state = "none"         # none / loading / shown
        self._archive_damage_paths.paths_ready.connect(self._on_damage_paths_ready)
        self._archive_damage_paths.error.connect(self._on_damage_paths_error)
        self.btn_damage_paths.toggled.connect(self._on_damage_paths_toggled)

    # ── the box ──────────────────────────────────────────────
    def _enter_box_mode(self, kind: str, prompt: str) -> None:
        self._click_modes.enter(kind, prompt)
        self.map_widget.set_asos_bbox_mode(True)

    def _leave_box_mode(self) -> None:
        self.map_widget.set_asos_bbox_mode(False)
        self.map_widget.run_js(
            "if(window.stormRestoreAsosMapInteractions) stormRestoreAsosMapInteractions();"
        )

    def _on_archive_box_drawn(self, west: float, south: float, east: float, north: float):
        """The box goes to the button whose click mode asked for it."""
        kind = self._click_modes.kind
        if kind == "asos_bbox":
            self.status_msg_label.setText("ASOS: finding stations…")
            self.surface_controls.set_status("ASOS: finding stations in the box…")
            self._archive_asos.set_bbox(west, south, east, north)
        elif kind == "damage_box":
            self.status_msg_label.setText("Damage paths: searching…")
            self.map_widget.set_damage_paths(None)
            self._damage_paths_state = "loading"
            self._archive_damage_paths.set_bbox(west, south, east, north)
        else:
            return
        self._click_modes.exit(kind)

    def _on_archive_box_cancelled(self):
        """Esc / right-click in the map while drawing."""
        if self._click_modes.kind in ("asos_bbox", "damage_box"):
            self._click_modes.exit()

    # ── ASOS ─────────────────────────────────────────────────
    def _on_archive_asos_toggled(self, enabled: bool):
        self._set_layer_active("asos", enabled)
        if enabled:
            if not self._archive_asos.has_stations() and not self._archive_asos.is_loading():
                self._enter_archive_asos_box()
            return
        self._click_modes.exit("asos_bbox")
        self._archive_asos.clear()
        self._clear_archive_asos_plots()
        self.surface_controls.set_status("ASOS: turn on, then drag a box on the map")

    def _enter_archive_asos_box(self):
        """ASOS on, or "new ASOS bbox": the stations shown stay until a new box is drawn."""
        self._enter_box_mode("asos_bbox", ASOS_BOX_PROMPT)

    def _exit_archive_asos_box(self, _mode: str, _next: str):
        self._leave_box_mode()
        if not self._archive_asos.has_stations() and not self._archive_asos.is_loading() \
                and self.surface_controls.asos_enabled():
            self.surface_controls.set_asos_enabled(False)
            self.status_msg_label.setText("ASOS: box canceled")

    def _on_archive_asos_load_started(self, count: int):
        text = f"ASOS: fetching history for {count} station{'s' if count != 1 else ''}…"
        self.status_msg_label.setText(text)
        self.surface_controls.set_status(text)

    def _on_archive_asos_load_finished(self, count: int):
        if count:
            text = f"ASOS: {count} station{'s' if count != 1 else ''}"
            self._layer_status["surface"].mark_success()
        else:
            text = "ASOS: no reports in this box"
            if not self._click_modes.is_active("asos_bbox"):
                self.surface_controls.set_asos_enabled(False)   # (resets the status line)
        self.status_msg_label.setText(text)
        self.surface_controls.set_status(text)
        if hasattr(self, "trail_controls"):
            self._schedule_trails()

    def _on_archive_asos_error(self, msg: str):
        self._layer_status["surface"].mark_error(msg)
        if not self._archive_asos.has_stations() and not self._click_modes.is_active("asos_bbox"):
            self.surface_controls.set_asos_enabled(False)
        self.status_msg_label.setText(msg)
        self.surface_controls.set_status(msg)

    def _on_archive_asos_station_updated(self, station_id: str, name: str, obs):
        self._archive_asos_latest[_plot_id(station_id)] = (name, obs)
        self._schedule_archive_asos_flush()

    def _on_archive_asos_station_removed(self, station_id: str):
        self._archive_asos_latest.pop(_plot_id(station_id), None)
        self._schedule_archive_asos_flush()

    def _clear_archive_asos_plots(self):
        self._archive_asos_generation += 1
        self._archive_asos_latest = {}
        if self._archive_asos_drawn:
            self.map_widget.remove_surface_station_plots_batch(list(self._archive_asos_drawn))
        self._archive_asos_drawn = {}
        if hasattr(self, "trail_controls"):
            self._schedule_trails()

    def _schedule_archive_asos_flush(self, *_args):
        if not self._archive_asos_flush_timer.isActive():
            self._archive_asos_flush_timer.start()

    def _flush_archive_asos_plots(self):
        """Bring the map's ASOS plots in line with the reports at the clock:
        remove stations without one, and render (off the UI thread) those
        whose values or age color changed. One render runs at a time; a
        flush during it waits for it."""
        if self._archive_asos_render_busy:
            return
        gone = [pid for pid in self._archive_asos_drawn if pid not in self._archive_asos_latest]
        if gone:
            for pid in gone:
                del self._archive_asos_drawn[pid]
            self.map_widget.remove_surface_station_plots_batch(gone)
        if not self._archive_asos_latest:
            return

        from ui.layers.surface_plot_layer import _surface_obs_fingerprint
        clock = self._time_ctrl.current_time
        todo = []
        for pid, (name, obs) in self._archive_asos_latest.items():
            fp = _surface_obs_fingerprint(obs, pid, reference_time=clock)
            if self._archive_asos_drawn.get(pid) != fp:
                todo.append((pid, name, obs, fp))
        if not todo:
            return
        self._archive_asos_render_busy = True
        generation = self._archive_asos_generation
        signals = self._archive_asos_render_signals
        threading.Thread(target=self._render_archive_asos, args=(generation, todo, signals),
                         daemon=True, name="asos-plots").start()

    def _render_archive_asos(self, generation: int, todo: list, signals) -> None:
        from ui.layers.surface_plot_layer import render_surface
        rendered = []
        for i, (pid, name, obs, fp) in enumerate(todo):
            if generation != self._archive_asos_generation:
                break
            try:
                rendered.append((pid, obs.lat, obs.lon, fp, render_surface(fp), name))
            except Exception as exc:  # noqa: BLE001
                log.error("Archive ASOS: plot render failed for %s: %s", pid, exc)
            if len(rendered) >= _ASOS_PUSH_CHUNK and i < len(todo) - 1:
                emit_alive(signals, "ready", generation, rendered)
                rendered = []
        # the last batch (maybe empty) also says this render is finished
        emit_alive(signals, "ready", generation, rendered + [None])

    def _on_archive_asos_rendered(self, generation: int, rows: list):
        finished = bool(rows) and rows[-1] is None
        if finished:
            rows = rows[:-1]
            self._archive_asos_render_busy = False
        if generation == self._archive_asos_generation:
            batch = []
            for pid, lat, lon, fp, png, name in rows:
                if pid not in self._archive_asos_latest:
                    continue                    # gone since the render started
                self._archive_asos_drawn[pid] = fp
                batch.append((pid, lat, lon, png, name))
            if batch:
                self.map_widget.add_surface_station_plots_batch(batch)
        if finished:
            self._schedule_archive_asos_flush()      # anything that changed meanwhile

    def _archive_asos_trail_observations(self) -> dict:
        """ASOS stations as trail platforms, for time-to-space (a fixed
        station only makes a path relative to the storm)."""
        asos = getattr(self, "_archive_asos", None)
        if asos is None:
            return {}
        return {f"ASOS {sid}": rows for sid, rows in asos.observations_by_station().items() if rows}

    # ── damage paths ─────────────────────────────────────────
    def _on_damage_paths_toggled(self, enabled: bool):
        self._set_layer_active("damage_paths", enabled)
        if enabled:
            if self._damage_paths_state == "none":
                self._enter_box_mode("damage_box", DAMAGE_BOX_PROMPT)
            return
        self._click_modes.exit("damage_box")
        self._archive_damage_paths.cancel()
        self._damage_paths_state = "none"
        self.map_widget.set_damage_paths(None)

    def _exit_damage_box(self, _mode: str, _next: str):
        self._leave_box_mode()
        if self._damage_paths_state == "none" and self.btn_damage_paths.isChecked():
            self.btn_damage_paths.setChecked(False)
            self.status_msg_label.setText("Damage paths: box canceled")

    def _on_damage_paths_ready(self, fc: dict):
        if self._damage_paths_state != "loading":
            return                      # turned off meanwhile
        features = fc.get("features") or []
        if not features:
            self._damage_paths_state = "none"
            self.btn_damage_paths.setChecked(False)
            self.status_msg_label.setText(
                f"Damage paths: none in this box on {self._archive_time:%Y-%m-%d}")
            return
        self._damage_paths_state = "shown"
        source = (features[0].get("properties") or {}).get("damage_source", "")
        n = len(features)
        self.status_msg_label.setText(f"Damage paths: {n} path{'s' if n != 1 else ''} ({source})")
        self.map_widget.set_damage_paths(fc)

    def _on_damage_paths_error(self, msg: str):
        if self._damage_paths_state != "loading":
            return
        self._damage_paths_state = "none"
        self.btn_damage_paths.setChecked(False)
        self.status_msg_label.setText(msg)
