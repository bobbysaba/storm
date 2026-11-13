"""Video Studio on the main window (ui/studio/, core/studio.py): opening
and closing the panel in the archive bar's place, is the map finished
drawing the archive clock's moment, the radar a keyframe shows, the
captions' data line, Save Frame, and running an export with progress."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

log = logging.getLogger(__name__)

# the table's keys the studio panel uses itself while open (ui/studio/panel.py)
STUDIO_SUSPENDS = ("play", "step_back", "step_forward", "skip_start", "skip_end", "track_delete", "sounding")


class MainWindowStudioMixin:

    # ---- the panel ---------------------------------------------------------------
    def _studio_open(self) -> bool:
        studio = getattr(self, "_studio", None)
        return studio is not None and studio.isVisible()

    def _toggle_studio(self) -> None:
        self._close_studio() if self._studio_open() else self._open_studio()

    def _open_studio(self) -> None:
        """The studio takes the archive bar's place, and its keys (Space,
        arrows, [ ], K, Home / End, Delete, , .) while open."""
        if not hasattr(self, "_archive_controls") or self._studio_open():
            self._sync_studio_action()
            return
        if getattr(self, "_studio", None) is None:
            from ui.studio.panel import StudioPanel
            self._studio = StudioPanel(self, self._map_container)
            self._studio.closed.connect(self._close_studio)
            self._studio.export_requested.connect(self._export_studio)
        self._shortcuts.suspend(STUDIO_SUSPENDS)
        self._archive_controls.hide()
        self._studio.show()
        self._studio.raise_()
        self._set_edit_menu_texts("Studio Edit")
        self._sync_studio_action()
        self._layout_overlays()

    def _close_studio(self) -> None:
        studio = getattr(self, "_studio", None)
        if studio is None or studio.isHidden():
            self._sync_studio_action()
            return
        studio.hide()
        self._archive_controls.show()
        self._shortcuts.resume(STUDIO_SUSPENDS)
        self._set_edit_menu_texts("Track Edit")
        self._sync_studio_action()
        self._layout_overlays()

    def _sync_studio_action(self) -> None:
        action = getattr(self, "_menu_actions", {}).get("studio")
        if action is not None:
            action.setChecked(self._studio_open())

    def _set_edit_menu_texts(self, what: str) -> None:
        menu = getattr(self, "_menu_actions", {})
        for name, verb in (("track_undo", "Undo"), ("track_redo", "Redo")):
            if name in menu:
                menu[name].setText(f"{verb} {what}")

    def _edit_undo(self, redo: bool) -> None:
        """Edit > Undo / Redo (Cmd+Z): the studio's edits while it's open, else the track's."""
        if self._studio_open():
            self._studio.redo() if redo else self._studio.undo()
        elif redo:
            self._redo_track_edit()
        else:
            self._undo_track_edit()

    # ---- is the map finished drawing the clock's moment? --------------------
    def studio_frame_pending(self) -> str | None:
        """What the map is still drawing for the archive clock's current time
        (None when everything is in), so a movie frame is only captured once
        complete: the radar scan for this time in its sharp render, the
        observation trails, satellite frames."""
        t = self._time_ctrl.current_time
        radar = getattr(self, "_archive_radar", None)
        if radar is not None:
            if radar.index_loading:
                return "radar index"                      # e.g. the station was just (re)chosen
            if getattr(self, "_package_radar_view", None):
                return "radar product"                    # a station switch, product/tilt still to apply
            wanted = radar._nearest_scan_before(t)
            scan = self._current_radar_scan
            if wanted is not None:
                if scan is None or scan.scan_time != wanted:
                    return "radar scan"
                if not self._scan_matches_radar(scan, radar):
                    return "radar product"
                if self._archive_render_in_flight or self._archive_pending_render_scan is not None:
                    return "radar image"
                if self._radar_settled_scan is not scan:
                    return "radar sharp render"
        if hasattr(self, "_trails_timer") and self._trails_timer.isActive():
            return "trails"
        sat = getattr(self, "_archive_satellite", None)
        if sat is not None and getattr(sat, "_pending", None):
            return "satellite"
        return None

    @staticmethod
    def _scan_matches_radar(scan, radar) -> bool:
        """The scan on screen is the station and product the radar was last
        asked for. (Its tilt index counts within the product's own tilts, so
        it isn't compared here; a new tilt arrives as a new scan anyway.)"""
        return (getattr(scan, "site", radar.station) == radar.station
                and getattr(scan, "pyart_field", radar._product) == radar._product)

    def studio_scan_times(self) -> list[datetime]:
        """The radar's scan times, for the "radar scans" clock step."""
        radar = getattr(self, "_archive_radar", None)
        return radar.scan_times() if radar is not None else []

    # ---- the radar a keyframe shows ------------------------------------------
    def studio_radar_state(self) -> dict | None:
        """The radar on screen: station, product and tilt."""
        radar = getattr(self, "_archive_radar", None)
        if radar is None:
            return None
        return {"station": radar.station, "product": self.radar_controls.current_product(),
                "tilt": self.radar_controls.current_tilt_index()}

    def studio_apply_radar(self, state: dict | None) -> None:
        """Show the radar a keyframe recorded, if it isn't already showing."""
        if not state or self.studio_radar_state() == state:
            return
        station, product, tilt = state.get("station"), state.get("product"), state.get("tilt")
        current = self.studio_radar_state() or {}
        if station and station != current.get("station"):
            # the product and tilt are applied when the new station's first scan arrives
            self._package_radar_view = {"product": product, "tilt_index": tilt}
            self._on_radar_station_clicked(station)
            return
        if product and product != current.get("product"):
            self.radar_controls.select_product(product)
        if tilt is not None and tilt >= 0 and tilt != self.radar_controls.current_tilt_index():
            self.radar_controls.select_tilt(tilt)

    # ---- captions -------------------------------------------------------------
    def studio_data_line(self) -> str:
        """The captions' second line: the radar scan on screen."""
        scan = self._current_radar_scan
        if scan is None or getattr(self, "_archive_radar", None) is None:
            return ""
        from core.level2_radar_scan import L2_PRODUCTS
        from ui.app.main_window_archive import _velocity_status_tag
        label = L2_PRODUCTS.get(scan.pyart_field, {}).get("label", scan.pyart_field)
        return (f"{scan.site} · {label} {scan.tilt_deg:.1f}° · scan {scan.scan_time:%H:%M:%S}Z"
                f"{_velocity_status_tag(scan)}")

    # ---- Save Frame and export ------------------------------------------------------
    def _studio_flash(self, text: str, seconds: float = 5.0) -> None:
        if self._studio_open():
            self._studio.flash(text, seconds)

    def _studio_save_frame(self) -> None:
        """Save Frame: a PNG of the map as it is now, once it has finished
        drawing, with the studio's captions."""
        from ui.studio.capture import MapCapture
        studio = self._studio
        capture = MapCapture(self, self)
        t = self._time_ctrl.current_time
        folder = studio.folder()

        def got(image):
            capture.end()
            capture.deleteLater()
            folder.mkdir(parents=True, exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, "Save Frame", str(folder / f"storm_{t:%Y%m%d_%H%M%S}Z.png"),
                                                  "PNG image (*.png)")
            if not path:
                self._studio_flash("")
                return
            path = Path(path if path.lower().endswith(".png") else path + ".png")
            if image.save(str(path), "PNG"):
                self.status_msg_label.setText(f"Frame saved: {path}")
                self._studio_flash(f"Frame saved: {path.name}")
            else:
                log.warning("Save Frame: couldn't write %s", path)
                self._studio_flash(f"Couldn't write {path.name}")
        capture.frame_ready.connect(got)
        self._studio_flash("Saving frame: waiting for the map to finish drawing…", 30)
        capture.begin(None, studio.project.overlays, capture.capture_when_ready)

    def _export_studio(self) -> None:
        """Export Movie…: choose where, then render (_start_studio_export)."""
        project = self._studio.project
        folder = self._studio.folder()
        folder.mkdir(parents=True, exist_ok=True)
        if project.format == "png":
            chosen = QFileDialog.getExistingDirectory(self, "Folder for the PNG frames", str(folder))
            if not chosen:
                return
            path = Path(chosen) / project.file_stem()
        else:
            ext = f".{project.format}"
            chosen, _ = QFileDialog.getSaveFileName(self, "Export Movie", str(folder / f"{project.file_stem()}{ext}"),
                                                    f"{project.format.upper()} (*{ext})")
            if not chosen:
                return
            path = Path(chosen if chosen.lower().endswith(ext) else chosen + ext)
        self._start_studio_export(project, path)

    def _start_studio_export(self, project, path: Path) -> None:
        """Render a project to `path` frame by frame, with progress and Cancel."""
        if getattr(self, "_studio_exporter", None) is not None:
            self.status_msg_label.setText("A video export is already running")
            return
        from ui.dialogs.task_progress_dialog import TaskProgressDialog
        from ui.studio.exporter import StudioExporter
        path = Path(path)
        exporter = StudioExporter(self, project, path, self)
        bar = TaskProgressDialog("Export Video", f"Rendering {path.name}", self)
        bar.canceled.connect(exporter.cancel)
        exporter.progress.connect(bar.set_progress)

        def finished(ok: bool, message: str) -> None:
            bar.finish()
            self._studio_exporter = None
            if ok:
                self.status_msg_label.setText(f"Video saved: {message}")
                self._studio_flash(f"Saved {message}", 10)
            elif message == "Export canceled":
                self.status_msg_label.setText("Video export canceled")
                self._studio_flash("Export canceled")
            elif not getattr(self, "_closing", False):
                log.error("Video export to %s failed: %s", path, message)
                QMessageBox.warning(self, "Export Video", message)
        exporter.finished.connect(finished)
        self._studio_exporter = exporter
        bar.show()
        exporter.start()

    def _cancel_studio_export(self) -> None:
        exporter = getattr(self, "_studio_exporter", None)
        if exporter is not None:
            exporter.cancel()
