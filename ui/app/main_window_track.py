"""Storm track (archive TRACK): hand-placed storm centers, one per radar frame,
ported from the storm_ty_edits fork (itself from MESO-VIEW's track editor).

While the TRACK drawer is open the map is in the ``track`` click mode: a click
places the storm center at the current frame (or moves the point already at
that frame), a drag moves a point (Alt keeps its time), and right-click offers
the reference marker and the point under the cursor. The model is
``core.storm_track.StormTrack``; the map page draws what ``geojson`` gives it.

Every edit saves the track to the case date's CSV (``core.workspace``), and
the archive session opens that file at the start, so the storm motion is
there for the sounding viewer before TRACK is ever opened. The table
(``TrackTableDialog``) edits the same track and follows the map's selection.
"""

import json
import logging
from datetime import timezone
from pathlib import Path

from core.storm_motion import mean_motion, motion_at
from core.storm_track import StormTrack, read_track_csv, write_track_csv
from core.workspace import track_path, tracks_root, unused_path
from ui.controls.track_controls import TrackControls
from ui.widgets import map_context_menu
from ui.widgets.map_context_menu import MenuContext

log = logging.getLogger("ui.app.main_window")

_TRACK_PROMPT = ("storm track — click: point at this frame · drag: move (Alt keeps its time) · "
                 "right-click: marker · P: point at marker · Delete: remove selected")


class MainWindowTrackMixin:

    def _init_track_toolbar(self, tb):
        """TRACK button and drawer (archive only); called from _init_toolbar."""
        self.btn_track = tb.add_button(
            "TRACK", "Place storm-track points on the radar, one per frame", "track"
        )
        self.track_controls = TrackControls(self._map_container)
        self.track_controls.setObjectName("floatingToolbar")
        self.btn_track.toggled.connect(self.track_controls.toggle_drawer)
        self.btn_track.toggled.connect(self._on_track_toggled)
        self.track_controls.undo_requested.connect(self._undo_track_edit)
        self.track_controls.redo_requested.connect(self._redo_track_edit)
        self.track_controls.clear_requested.connect(self._clear_track)
        self.track_controls.table_requested.connect(self._open_track_table)
        self.track_controls.import_requested.connect(self._import_track_file)
        self.track_controls.export_requested.connect(self._export_track_file)
        self.track_controls.open_folder_requested.connect(self._open_track_folder)
        self.track_controls.layers_changed.connect(self._apply_track_visibility)
        self.map_widget.track_point_add_requested.connect(self._on_track_point_add)
        self.map_widget.track_point_selected.connect(self._on_track_point_select)
        self.map_widget.track_point_moved.connect(self._on_track_point_moved)
        self._track = StormTrack()
        self._track_marker = None        # {lat, lon, label}: one per session, never saved
        self._track_edit_active = False
        self._track_table = None         # TrackTableDialog, built on first open
        self._track_saved = False        # the date's file has been written or read
        self._track_save_path = None     # set when the date's file couldn't be read

    # ── mode ──────────────────────────────────────────────
    def _on_track_toggled(self, checked: bool):
        self._track_edit_active = checked
        self.map_widget.set_track_edit_mode(checked)
        if checked:
            self._click_modes.enter("track", _TRACK_PROMPT)
            self._push_track_geojson()
        else:
            self._click_modes.exit("track")
            if self._track_table is not None:
                self._track_table.hide()
        self._apply_track_visibility()

    def _exit_track_mode(self, _mode: str, _next: str):
        self.btn_track.setChecked(False)

    def _apply_track_visibility(self):
        """Drawn while TRACK is open (Line / Points as chosen), or always with Keep on map."""
        shown = self.btn_track.isChecked() or self.track_controls.keep_on_map()
        line, points = self.track_controls.layers()
        self.map_widget.set_track_layers_visible(line and shown, points and shown)

    def _track_now(self):
        return self._time_ctrl.current_time

    def _current_track_radar_context(self) -> dict:
        """The radar fields of a new point: whatever is on screen right now."""
        from core.level2_radar_scan import L2_PRODUCTS  # noqa: PLC0415
        product = self.radar_controls.current_product()
        radar = getattr(self, "_archive_radar", None)   # none until a station is picked
        return {
            "radar_site": radar.station if radar is not None else "",
            "product": product,
            "product_label": L2_PRODUCTS.get(product, {}).get("label", product),
            "tilt_deg": getattr(self._current_radar_scan, "tilt_deg", None),
        }

    # ── edits ─────────────────────────────────────────────
    def _on_track_point_add(self, lat: float, lon: float):
        if not self._track_edit_active:
            return
        point, moved = self._track.place(self._track_now(), lat, lon,
                                         **self._current_track_radar_context())
        if moved:
            self.status_msg_label.setText(
                f"Track: moved point {point.point_id} at {point.time:%H:%M:%S} UTC"
            )
        self._track_changed()

    def _on_track_point_moved(self, point_id: int, lat: float, lon: float, keep_time: bool):
        """A drag: here, now (MESO-VIEW's retime), or here at its own time with Alt."""
        if not self._track_edit_active:
            return
        self._track.move(point_id, lat, lon, None if keep_time else self._track_now())
        self._track_changed()

    def _on_track_point_select(self, point_id: int):
        self._track.selected_id = point_id if self._track.point(point_id) else None
        self._push_track_geojson()
        if self._track_table is not None:
            self._track_table.select_point(self._track.selected_id)

    def _delete_selected_track_point(self):
        if self._track_edit_active and self._track.selected_id is not None:
            self._delete_track_point(self._track.selected_id)

    def _delete_track_point(self, point_id: int):
        if self._track_edit_active and self._track.delete(point_id):
            self._track_changed()

    def _clear_track(self):
        # undoable, so no confirmation
        if len(self._track):
            self._track.clear()
            self.status_msg_label.setText("Track cleared (Ctrl+Z brings it back)")
            self._track_changed()

    def _undo_track_edit(self):
        if self._track_edit_active and self._track.undo():
            self._track_changed()

    def _redo_track_edit(self):
        if self._track_edit_active and self._track.redo():
            self._track_changed()

    def _add_track_point_at_marker(self):
        if not self._track_edit_active:
            return
        if not self._track_marker:
            self.status_msg_label.setText("Track: no marker — right-click the map to add one")
            return
        self._on_track_point_add(self._track_marker["lat"], self._track_marker["lon"])

    # ── reference marker ──────────────────────────────────
    def _on_track_marker_add(self, lat: float, lon: float):
        """Place the marker, replacing the old one: there is only ever one."""
        label = self._track_marker["label"] if self._track_marker else "M1"
        self._track_marker = {"lat": lat, "lon": lon, "label": label}
        self.map_widget.set_track_marker(self._track_marker)

    def _on_track_marker_rename(self):
        if not self._track_marker:
            return
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        text, ok = QInputDialog.getText(
            self, "Rename Marker", "New label for the marker:", text=self._track_marker["label"]
        )
        if ok:
            self._track_marker["label"] = text.strip() or "M1"
            self.map_widget.set_track_marker(self._track_marker)

    def _on_track_marker_remove(self):
        self._track_marker = None
        self.map_widget.set_track_marker(None)

    # ── right-click menu (main_window_context_menu runs the _ctx_ handlers) ──
    def _track_context_menu(self, lat: float, lon: float, feature_json: str):
        feature = None
        if feature_json:
            try:
                feature = json.loads(feature_json)
            except (json.JSONDecodeError, ValueError):
                log.warning("track menu: bad feature json %r", feature_json[:200])
        ctx = MenuContext(lat=lat, lon=lon, mode=self._session_mode(), feature=feature)
        label = self._track_marker["label"] if self._track_marker else None
        return map_context_menu.build_track_menu(ctx, label)

    def _ctx_track_point_select(self, point_id: int):
        self._on_track_point_select(point_id)

    def _ctx_track_point_delete(self, point_id: int):
        self._delete_track_point(point_id)

    def _ctx_track_marker_add(self, lat: float, lon: float):
        self._on_track_marker_add(lat, lon)

    def _ctx_track_marker_rename(self):
        self._on_track_marker_rename()

    def _ctx_track_marker_remove(self):
        self._on_track_marker_remove()

    # ── files ─────────────────────────────────────────────
    def _track_session_day(self):
        return self._archive_time.astimezone(timezone.utc).date()

    def _track_file(self) -> Path:
        return self._track_save_path or track_path(self._track_session_day())

    def _open_session_track(self):
        """Open the case date's saved track; called once the archive clock exists."""
        path = self._track_file()
        if path.exists():
            try:
                self._track.load(read_track_csv(path))
                self._track_saved = True
            except (OSError, ValueError) as exc:
                # keep the file as it is: edits save beside it (name_2.csv)
                self._track_save_path = unused_path(path)
                log.error("Track %s could not be read: %s", path, exc)
                self.status_msg_label.setText(
                    f"Track file could not be read ({exc}); edits save to {self._track_save_path.name}")
        self._refresh_track_controls()
        if len(self._track):
            mean = self.track_storm_motion()
            self.status_msg_label.setText(
                f"Storm track loaded ({len(self._track)} points"
                + (f", motion {mean.short()}" if mean else "") + ") — open TRACK to see it")

    def _autosave_track(self):
        path = self._track_file()
        try:
            write_track_csv(self._track.points, path)
        except OSError as exc:
            log.error("Track autosave to %s failed: %s", path, exc)
            self.status_msg_label.setText(f"Track autosave failed: {exc}")
            return
        if not self._track_saved:
            self.status_msg_label.setText(f"Track saved to {path}")
        self._track_saved = True

    def _open_track_folder(self):
        from PyQt6.QtCore import QUrl  # noqa: PLC0415
        from PyQt6.QtGui import QDesktopServices  # noqa: PLC0415
        folder = tracks_root()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _import_track_file(self):
        """Replace the track with a CSV (STORM's, MESO-VIEW's, any time / lat / lon table)."""
        from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Storm Track", str(tracks_root()), "CSV files (*.csv);;All files (*)")
        if path:
            self._import_track_path(Path(path))

    def _import_track_path(self, path: Path):
        try:
            points = read_track_csv(path)
        except (OSError, ValueError) as exc:
            self.status_msg_label.setText(f"Track import failed: {exc}")
            return
        if not points:
            self.status_msg_label.setText(f"Track import: {path.name} has no usable points")
            return
        self._track.set_points(points)
        self.status_msg_label.setText(
            f"Track: imported {len(points)} points from {path.name} (Ctrl+Z undoes it)")
        self._track_changed()

    def _export_track_file(self):
        if not len(self._track):
            return
        from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415
        default = Path.home() / self._track_file().name
        path, _ = QFileDialog.getSaveFileName(self, "Export Storm Track", str(default), "CSV files (*.csv)")
        if not path:
            return
        try:
            write_track_csv(self._track.points, Path(path))
        except OSError as exc:
            self.status_msg_label.setText(f"Track export failed: {exc}")
            return
        self.status_msg_label.setText(f"Track exported to {path}")

    # ── table ─────────────────────────────────────────────
    def _open_track_table(self):
        from ui.dialogs.track_table_dialog import TrackTableDialog  # noqa: PLC0415
        if self._track_table is None:
            self._track_table = TrackTableDialog(self._track_session_day(), self)
            self._track_table.apply_requested.connect(self._apply_track_table)
            self._track_table.point_selected.connect(self._on_track_point_select)
            self._track_table.jump_requested.connect(self._jump_to_track_point)
        self._track_table.set_default_time(self._track_now())
        self._track_table.load(self._track.points, force=True)
        self._track_table.select_point(self._track.selected_id)
        self._track_table.show()
        self._track_table.raise_()

    def _apply_track_table(self, points: list):
        if points == self._track.points:
            return
        self._track.set_points(points)
        self.status_msg_label.setText(f"Track: table edits applied ({len(points)} points)")
        self._track_changed()

    def _jump_to_track_point(self, point_id: int):
        point = self._track.point(point_id)
        if point is None:
            return
        self._on_track_point_select(point_id)
        self._time_ctrl.pause()
        self._time_ctrl.set_time(point.time)

    # ── redraw ────────────────────────────────────────────
    def _track_changed(self):
        """After every edit: redraw, save, refresh the drawer and the table."""
        self._push_track_geojson()
        self._autosave_track()
        self._refresh_track_controls()
        if self._track_table is not None:
            self._track_table.set_default_time(self._track_now())
            self._track_table.load(self._track.points)
            self._track_table.select_point(self._track.selected_id)

    def _push_track_geojson(self):
        self.map_widget.set_track_geojson(self._track.geojson(self._track_now()))

    def _refresh_track_controls(self):
        self.track_controls.set_point_count(len(self._track))
        self.track_controls.set_edit_state(can_undo=self._track.can_undo(),
                                           can_redo=self._track.can_redo())
        path = self._track_file()
        if self._track_saved:
            self.track_controls.set_file(f"Saved to {path.parent.name}/{path.name}", str(path))
        else:
            self.track_controls.set_file("New track — saves with the first point", str(path))
        self._refresh_track_motion()

    def _refresh_track_motion(self):
        mean = self.track_storm_motion()
        now = motion_at(self._track.points, self._track_now()) if len(self._track) else None
        self.track_controls.set_motion(mean.short() if mean else None, now.short() if now else None)
        if getattr(self, "_sounding_dialog_obj", None) is not None:
            self._sounding_dialog_obj.set_observed_storm_motion(mean)
        if hasattr(self, "trail_controls"):
            self._on_trail_track_changed(mean)

    def track_storm_motion(self):
        """The track's mean storm motion (core.storm_motion.StormMotion), or None."""
        return mean_motion(self._track.points)

    def _on_track_time_changed(self, _when):
        # the "current" point and the segment motion follow the clock
        if len(self._track):
            self._push_track_geojson()
            self._refresh_track_motion()
