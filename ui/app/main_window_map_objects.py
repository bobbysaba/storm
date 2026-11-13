"""Annotations, drawings, storm motion cones and the measure tool."""

import logging
import json
from datetime import datetime, timezone
from PyQt6.QtCore import QTimer
from ui.click_mode import kind_of
from ui.dialogs.annotation_dialog import (
    AnnotationPlaceDialog, AnnotationEditDialog, AnnotationMoveConfirmDialog,
)
from ui.dialogs.drawing_dialog import (
    DrawingTitleDialog, DrawingEditDialog, DrawingPlaceConfirmDialog,
    DrawingMoveConfirmDialog,
)
from ui.dialogs.storm_cone_dialog import (
    StormConeInputDialog, StormConePlaceConfirmDialog, StormConeMotionConfirmDialog,
    StormConeMoveConfirmDialog,
)
from core.annotation import Annotation, ANNOTATION_TYPE_MAP
from core.storm_cone import StormCone, motion_from_fixes
from core.drawing import DrawingAnnotation, DRAWING_TYPE_MAP, FRONT_TYPE_KEYS
from network.annotation_sync import AnnotationSync
from network.drawing_sync import DrawingSync
from ui.app.main_window_util import _ANNOTATE_KINDS, _coords_close

log = logging.getLogger("ui.app.main_window")


class MainWindowMapObjectsMixin:

    def _init_annotations(self):
        self._annotations: dict[str, Annotation] = {}
        self._active_annotation_type: str = ""
        self._annotation_sync = AnnotationSync(self._mqtt_client, read_only=self._viewer, parent=self)

        # tool selection → set cursor mode
        self.annotation_tools.tool_selected.connect(self._on_annotation_tool_selected)
        self.annotation_tools.refresh_requested.connect(self._on_refresh_current_json)

        # map click → place annotation (if tool is active)
        self.map_widget.map_clicked.connect(self._on_map_click)

        # annotation marker click → edit/delete dialog
        self.map_widget.annotation_clicked.connect(self._on_annotation_clicked)
        self.map_widget.annotation_drag_ended.connect(self._on_annotation_drag_end)
        self._moving_annotation_id = None

        # remote annotations arriving over MQTT — update map without re-publishing
        self._annotation_sync.annotation_received.connect(self._recv_remote_annotation)
        self._annotation_sync.annotation_deleted.connect(self._recv_remote_annotation_deleted)

        self._init_drawings()

    def _init_drawings(self):
        self._drawings: dict[str, DrawingAnnotation] = {}
        self._active_drawing_type: str = ""
        self._drawing_points: list = []
        self._moving_drawing_id: str | None = None
        self._moving_drawing_original_coordinates: list | None = None
        self._drawing_sync = DrawingSync(self._mqtt_client, read_only=self._viewer, parent=self)

        self.map_widget.map_double_clicked.connect(self._on_map_dblclick)
        self.map_widget.drawing_clicked.connect(self._on_drawing_clicked)
        self.map_widget.drawing_drag_ended.connect(self._on_drawing_drag_end)
        self._drawing_sync.drawing_received.connect(self._recv_remote_drawing)
        self._drawing_sync.drawing_deleted.connect(self._recv_remote_drawing_deleted)

    def _set_placement_prompt(self, msg: str, needs_click: bool = True):
        """Name what the next map click or drag does, in the on-map banner."""
        text = f"{msg}  —  click map to place" if needs_click else msg
        if self._click_modes.is_active():
            self._click_modes.set_prompt(text)
        else:
            # drag-to-move flows aren't click modes; Esc doesn't cancel them
            self._show_mode_banner(text, cancellable=False)

    def _clear_placement_prompt(self):
        if not self._click_modes.is_active():
            self._show_mode_banner("", cancellable=False)

    def _on_annotation_tool_selected(self, type_key: str):
        if not type_key:
            # drawer closed or the active tool clicked again. A storm-motion fix in
            # progress survives closing the drawer, so radar frames can be stepped.
            if (self._click_modes.is_active("storm_motion_fix")
                    and self._pending_cone_motion_fix is not None):
                return
            for kind in _ANNOTATE_KINDS:
                self._click_modes.exit(kind)
            return

        if type_key in DRAWING_TYPE_MAP:
            # drawing tool (front or custom shape)
            self._click_modes.enter(f"drawing:{type_key}")
            self._active_drawing_type = type_key
            self.map_widget.set_drawing_mode(True, type_key)
            meta = DRAWING_TYPE_MAP[type_key]
            self._set_placement_prompt(
                f"{meta['label']} — click to add points, double-click to finish",
                needs_click=False,
            )
        elif type_key == "storm_motion":
            self._click_modes.enter("storm_motion_fix")
            self._active_annotation_type = type_key
            self.map_widget.set_annotation_mode(True)
            self._set_placement_prompt(
                "storm cone — select first radar feature position",
                needs_click=False,
            )
        else:
            self._click_modes.enter(f"annotation:{type_key}")
            self._active_annotation_type = type_key
            self.map_widget.set_annotation_mode(True)
            if type_key == "pressure_system":
                label = "pressure system"
            else:
                label = ANNOTATION_TYPE_MAP.get(type_key, {}).get("label", "annotation")
            self._set_placement_prompt(label)

    def _exit_annotation_mode(self, _mode: str, next_mode: str):
        self._active_annotation_type = ""
        self._pending_cone_params = None
        self._pending_cone_motion_fix = None
        self.map_widget.set_annotation_mode(False)
        self.map_widget.set_storm_cone_placement_mode(False)
        if kind_of(next_mode) not in _ANNOTATE_KINDS:
            self.annotation_tools.deactivate_tool()

    def _exit_drawing_mode(self, _mode: str, next_mode: str):
        self._cancel_drawing()
        if kind_of(next_mode) not in _ANNOTATE_KINDS:
            self.annotation_tools.deactivate_tool()

    def _on_map_click(self, lat: float, lon: float):
        self._click_modes.click(lat, lon)

    def _on_annotation_place_click(self, lat: float, lon: float):
        type_key = self._active_annotation_type
        if type_key == "fork":
            # only one fork on the map: remove the old one before placing
            existing_forks = [aid for aid, a in self._annotations.items() if a.type_key == "fork"]
            for fid in existing_forks:
                self._delete_annotation(fid)
            self._click_modes.exit()
            self._place_annotation(Annotation.new(type_key="fork", lat=lat, lon=lon))
            return
        dlg = AnnotationPlaceDialog(type_key, lat, lon, viewer_mode=self._viewer, parent=self)
        if dlg.exec() == AnnotationPlaceDialog.DialogCode.Accepted:
            annotation = Annotation.new(
                type_key=dlg.result_type_key(),
                lat=lat,
                lon=lon,
                label=dlg.result_label(),
            )
            self._place_annotation(annotation)

    def _on_annotation_clicked(self, annotation_id: str):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        dlg = AnnotationEditDialog(annotation, viewer_mode=self._viewer, parent=self)
        if dlg.exec() == AnnotationEditDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_annotation(annotation_id)
            elif dlg.action() == "save":
                annotation.label = dlg.result_label()
                self._update_annotation(annotation)
            elif dlg.action() == "move":
                self._start_annotation_move(annotation_id)

    def _start_annotation_move(self, annotation_id: str):
        self._moving_annotation_id = annotation_id
        self.map_widget.set_annotation_draggable(annotation_id, True)
        self._set_placement_prompt("drag the annotation to its new location", needs_click=False)

    def _on_annotation_drag_end(self, annotation_id: str, lat: float, lon: float):
        annotation = self._annotations.get(annotation_id)
        if annotation is None:
            return
        self.map_widget.set_annotation_draggable(annotation_id, False)
        self._moving_annotation_id = None
        self._clear_placement_prompt()

        dlg = AnnotationMoveConfirmDialog(annotation, lat, lon, parent=self)
        if dlg.exec() == AnnotationMoveConfirmDialog.DialogCode.Accepted:
            annotation.lat = lat
            annotation.lon = lon
            self._update_annotation(annotation)
        else:
            # revert to original position
            self.map_widget.move_annotation(annotation_id, annotation.lat, annotation.lon)

    def _place_annotation(self, annotation: Annotation):
        self._annotations[annotation.id] = annotation
        self.map_widget.add_annotation(annotation)
        self._annotation_sync.publish_create(annotation)
        self._refresh_annotation_layer()
        log.info("annotation placed: %s at (%.4f, %.4f)", annotation.type_key, annotation.lat, annotation.lon)

    def _delete_annotation(self, annotation_id: str):
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._annotation_sync.publish_delete(annotation_id)
        self._refresh_annotation_layer()
        log.info("annotation deleted: %s", annotation_id)

    def _update_annotation(self, annotation: Annotation):
        self._annotations[annotation.id] = annotation
        # re-add marker so label tooltip reflects new text
        self.map_widget.add_annotation(annotation)
        self._annotation_sync.publish_update(annotation)
        log.info("annotation updated: %s label=%s", annotation.id, annotation.label)

    def _recv_remote_annotation(self, annotation: Annotation):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._annotations[annotation.id] = annotation
        self.map_widget.add_annotation(annotation)
        self._refresh_annotation_layer()
        log.info("remote annotation received: %s (%s)", annotation.id, annotation.type_key)

    def _recv_remote_annotation_deleted(self, annotation_id: str, deleted_at: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._annotations.pop(annotation_id, None)
        self.map_widget.remove_annotation(annotation_id)
        self._refresh_annotation_layer()
        log.info("remote annotation deleted: %s at %s", annotation_id, deleted_at or "unknown")

    def _on_drawing_click(self, lat: float, lon: float):
        """Add a point to the in-progress drawing."""
        self._drawing_points.append([lat, lon])
        self.map_widget.drawing_update_preview(self._drawing_points)
        n = len(self._drawing_points)
        meta = DRAWING_TYPE_MAP.get(self._active_drawing_type, {})
        self._set_placement_prompt(
            f"{meta.get('label', '')} — {n} point{'s' if n != 1 else ''} — double-click to finish",
            needs_click=False,
        )

    def _on_map_dblclick(self, lat: float, lon: float):
        if not self._click_modes.is_active("drawing"):
            return
        self._finalize_drawing(lat, lon)

    def _finalize_drawing(self, lat: float, lon: float):
        pts = self._drawing_points[:]
        while pts and _coords_close(pts[-1], (lat, lon)):
            pts.pop()
        pts.append([lat, lon])

        drawing_type = self._active_drawing_type

        if len(pts) < 2:
            self._drawing_points = pts
            self.map_widget.drawing_update_preview(self._drawing_points)
            meta = DRAWING_TYPE_MAP.get(drawing_type, {})
            self._set_placement_prompt(
                f"{meta.get('label', 'Drawing')} needs at least 2 points — keep drawing, then double-click to finish",
                needs_click=False,
            )
            return
        if drawing_type == "polygon" and len(pts) < 3:
            self._drawing_points = pts
            self.map_widget.drawing_update_preview(self._drawing_points)
            self._set_placement_prompt(
                "Polygon needs at least 3 points — keep drawing, then double-click to finish",
                needs_click=False,
            )
            return

        self._click_modes.exit("drawing")   # clear state + preview before showing dialog

        if drawing_type in ("polyline", "polygon"):
            dlg = DrawingTitleDialog(drawing_type, parent=self)
            if dlg.exec() != DrawingTitleDialog.DialogCode.Accepted:
                return
            title = dlg.title()
            color = dlg.color()
            line_style = dlg.line_style()
        else:
            title = DRAWING_TYPE_MAP.get(drawing_type, {}).get("label", drawing_type)
            color = None
            line_style = "solid"

        drawing = DrawingAnnotation.new(
            drawing_type=drawing_type,
            coordinates=pts,
            title=title,
            color=color,
            line_style=line_style,
        )
        dlg = DrawingPlaceConfirmDialog(drawing_type, len(pts), parent=self)
        if dlg.exec() != DrawingPlaceConfirmDialog.DialogCode.Accepted:
            return
        self._place_drawing(drawing)
        if drawing_type in FRONT_TYPE_KEYS:
            self.btn_annotate.setChecked(False)

    def _cancel_drawing(self):
        self._drawing_points.clear()
        self._active_drawing_type = ""
        self.map_widget.set_drawing_mode(False)
        self._clear_placement_prompt()

    def _on_drawing_clicked(self, drawing_id: str):
        # ignore while a map click mode is active
        if self._click_modes.is_active():
            return
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        dlg = DrawingEditDialog(drawing, parent=self)
        if dlg.exec() != DrawingEditDialog.DialogCode.Accepted:
            return
        action = dlg.action()
        if action == "delete":
            self._delete_drawing(drawing_id)
        elif action == "flip":
            drawing.flipped = not drawing.flipped
            self._update_drawing(drawing)
        elif action == "move":
            self._start_drawing_move(drawing_id)
        elif action == "save":
            drawing.title = dlg.result_title()
            drawing.color = dlg.result_color()
            drawing.line_style = dlg.result_line_style()
            self._update_drawing(drawing)

    def _start_drawing_move(self, drawing_id: str):
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        self._moving_drawing_id = drawing_id
        self._moving_drawing_original_coordinates = [pt[:] for pt in drawing.coordinates]
        self.map_widget.set_drawing_draggable(drawing_id, True)
        self._set_placement_prompt("drag the drawing to its new location", needs_click=False)

    def _on_drawing_drag_end(self, drawing_id: str, coordinates_json: str):
        drawing = self._drawings.get(drawing_id)
        if drawing is None:
            return
        self.map_widget.set_drawing_draggable(drawing_id, False)
        self._moving_drawing_id = None
        try:
            new_coordinates = json.loads(coordinates_json)
        except json.JSONDecodeError:
            log.warning("drawing drag end parse failed for %s", drawing_id)
            self._clear_placement_prompt()
            self._update_drawing(drawing)
            return
        dlg = DrawingMoveConfirmDialog(drawing, new_coordinates, parent=self)
        if dlg.exec() == DrawingMoveConfirmDialog.DialogCode.Accepted:
            drawing.coordinates = new_coordinates
        elif self._moving_drawing_original_coordinates is not None:
            drawing.coordinates = [pt[:] for pt in self._moving_drawing_original_coordinates]
        self._moving_drawing_original_coordinates = None
        self._clear_placement_prompt()
        self._update_drawing(drawing)

    def _place_drawing(self, drawing: DrawingAnnotation):
        self._drawings[drawing.id] = drawing
        self.map_widget.add_drawing(drawing)
        self._drawing_sync.publish_create(drawing)
        self._refresh_annotation_layer()
        log.info("drawing placed: %s at %d points", drawing.drawing_type, len(drawing.coordinates))

    def _delete_drawing(self, drawing_id: str):
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._drawing_sync.publish_delete(drawing_id)
        self._refresh_annotation_layer()
        log.info("drawing deleted: %s", drawing_id)

    def _update_drawing(self, drawing: DrawingAnnotation):
        self._drawings[drawing.id] = drawing
        self.map_widget.remove_drawing(drawing.id)
        self.map_widget.add_drawing(drawing)
        self._drawing_sync.publish_update(drawing)
        log.info("drawing updated: %s", drawing.id)

    def _recv_remote_drawing(self, drawing: DrawingAnnotation):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._drawings[drawing.id] = drawing
        self.map_widget.add_drawing(drawing)
        self._refresh_annotation_layer()
        log.info("remote drawing received: %s (%s)", drawing.id, drawing.drawing_type)

    def _recv_remote_drawing_deleted(self, drawing_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._drawings.pop(drawing_id, None)
        self.map_widget.remove_drawing(drawing_id)
        self._refresh_annotation_layer()
        log.info("remote drawing deleted: %s", drawing_id)

    def _init_storm_cone(self):
        self._storm_cones: dict[str, StormCone] = {}
        self._pending_cone_params: dict | None = None
        self._pending_cone_motion_fix: dict | None = None
        self._moving_cone_id: str | None = None
        self._moving_cone_original_location: tuple[float, float] | None = None

        # cone placed via ANNOTATE drawer — map cone-click → edit dialog
        self.map_widget.storm_cone_clicked.connect(self._on_storm_cone_clicked)
        self.map_widget.storm_cone_drag_ended.connect(self._on_storm_cone_drag_end)
        self.map_widget.storm_cone_place_drag_ended.connect(self._on_storm_cone_place_drag_end)

        # remote cones arriving over MQTT — update map without re-publishing
        self._storm_cone_sync.cone_received.connect(self._recv_remote_storm_cone)
        self._storm_cone_sync.cone_deleted.connect(self._recv_remote_storm_cone_deleted)

        # auto-expire cones older than 1 hour (checked every 60 seconds).
        self._cone_expire_timer = QTimer(self)
        self._cone_expire_timer.setInterval(60_000)
        self._cone_expire_timer.timeout.connect(self._expire_storm_cones)
        self._cone_expire_timer.start()

    def _on_storm_motion_fix_click(self, lat: float, lon: float):
        scan = self._current_displayed_radar_scan()
        if scan is None:
            self._set_placement_prompt(
                "storm cone — show a radar frame before selecting motion",
                needs_click=False,
            )
            return

        scan_time = scan.scan_time
        if scan_time.tzinfo is None:
            scan_time = scan_time.replace(tzinfo=timezone.utc)
        time_label = scan_time.strftime("%H:%MZ")

        if self._pending_cone_motion_fix is None:
            self._pending_cone_motion_fix = {
                "lat": lat,
                "lon": lon,
                "scan_time": scan_time,
                "site": getattr(scan, "site", ""),
                "product": getattr(scan, "product", ""),
                "time_label": time_label,
            }
            self._set_placement_prompt(
                f"storm cone — first fix {time_label}; move to a later radar frame and select the same feature",
                needs_click=False,
            )
            return

        first = self._pending_cone_motion_fix
        if getattr(scan, "site", "") != first.get("site", ""):
            self._set_placement_prompt(
                "storm cone — radar site changed; select the first fix again",
                needs_click=False,
            )
            self._pending_cone_motion_fix = None
            return

        try:
            motion = motion_from_fixes(
                first["lat"], first["lon"], first["scan_time"],
                lat, lon, scan_time,
            )
        except ValueError:
            self._set_placement_prompt(
                "storm cone — choose a radar frame later than the first fix",
                needs_click=False,
            )
            return

        if motion["distance_nm"] < 0.05:
            self._set_placement_prompt(
                "storm cone — second fix is too close; choose the same feature on a later frame",
                needs_click=False,
            )
            return

        speed_kts = motion["speed_kts"]
        heading = int(round(motion["heading"])) % 360
        dlg = StormConeMotionConfirmDialog(
            lat,
            lon,
            speed_kts,
            heading,
            first["time_label"],
            time_label,
            motion["distance_nm"],
            parent=self,
        )
        if dlg.exec() != StormConeMotionConfirmDialog.DialogCode.Accepted:
            self._set_placement_prompt(
                f"storm cone — first fix {first['time_label']}; select a second point",
                needs_click=False,
            )
            return

        cone = StormCone.new(
            lat,
            lon,
            heading=heading,
            speed_kts=speed_kts,
            valid_at=scan_time,
        )
        self._click_modes.exit()
        self._place_storm_cone(cone)

    def _on_storm_cone_clicked(self, cone_id: str):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        dlg = StormConeInputDialog(
            edit_mode=True,
            speed_kts=cone.speed_kts,
            heading=int(cone.heading),
            parent=self,
        )
        if dlg.exec() == StormConeInputDialog.DialogCode.Accepted:
            if dlg.action() == "delete":
                self._delete_storm_cone(cone_id)
            elif dlg.action() == "save":
                cone.speed_kts = dlg.speed_kts()
                cone.heading = dlg.heading()
                cone.created_at = datetime.now(timezone.utc)
                self._update_storm_cone(cone)
            elif dlg.action() == "move":
                self._start_storm_cone_move(cone_id)

    def _start_storm_cone_move(self, cone_id: str):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        self._moving_cone_id = cone_id
        self._moving_cone_original_location = (cone.lat, cone.lon)
        self.map_widget.set_storm_cone_draggable(cone_id, True)
        self._set_placement_prompt("drag the storm cone to its new location", needs_click=False)

    def _on_storm_cone_drag_end(self, cone_id: str, lat: float, lon: float):
        cone = self._storm_cones.get(cone_id)
        if cone is None:
            return
        self.map_widget.set_storm_cone_draggable(cone_id, False)
        self._moving_cone_id = None
        dlg = StormConeMoveConfirmDialog(
            self._moving_cone_original_location[0] if self._moving_cone_original_location else cone.lat,
            self._moving_cone_original_location[1] if self._moving_cone_original_location else cone.lon,
            lat,
            lon,
            parent=self,
        )
        if dlg.exec() == StormConeMoveConfirmDialog.DialogCode.Accepted:
            cone.lat = lat
            cone.lon = lon
            cone.created_at = datetime.now(timezone.utc)
        elif self._moving_cone_original_location is not None:
            cone.lat, cone.lon = self._moving_cone_original_location
        self._moving_cone_original_location = None
        self._clear_placement_prompt()
        self._update_storm_cone(cone)

    def _on_storm_cone_place_drag_end(self, lat: float, lon: float):
        if self._pending_cone_params is None:
            return
        dlg = StormConePlaceConfirmDialog(
            lat,
            lon,
            self._pending_cone_params["speed_kts"],
            self._pending_cone_params["heading"],
            parent=self,
        )
        if dlg.exec() != StormConePlaceConfirmDialog.DialogCode.Accepted:
            self._set_placement_prompt("storm cone — click and drag to place", needs_click=False)
            return
        cone = StormCone.new(lat, lon, **self._pending_cone_params)
        self._pending_cone_params = None
        self._click_modes.exit()
        self._clear_placement_prompt()
        self._place_storm_cone(cone)

    def _place_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._storm_cone_sync.publish_create(cone)
        self._refresh_storm_cone_layer()
        log.info("storm cone placed: id=%s lat=%.4f lon=%.4f hdg=%.0f spd=%.0f",
                 cone.id, cone.lat, cone.lon, cone.heading, cone.speed_kts)

    def _delete_storm_cone(self, cone_id: str):
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._storm_cone_sync.publish_delete(cone_id)
        self._refresh_storm_cone_layer()
        log.info("storm cone deleted: %s", cone_id)

    def _update_storm_cone(self, cone: StormCone):
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)   # re-add rebuilds geometry
        self._storm_cone_sync.publish_update(cone)
        log.info("storm cone updated: id=%s hdg=%.0f spd=%.0f",
                 cone.id, cone.heading, cone.speed_kts)

    def _recv_remote_storm_cone(self, cone: StormCone):
        """Inbound from MQTT — update map/dict but do NOT republish."""
        self._storm_cones[cone.id] = cone
        self.map_widget.add_storm_cone(cone)
        self._refresh_storm_cone_layer()
        log.info("remote storm cone received: %s", cone.id)

    def _recv_remote_storm_cone_deleted(self, cone_id: str):
        """Inbound delete from MQTT — remove from map/dict but do NOT republish."""
        self._storm_cones.pop(cone_id, None)
        self.map_widget.remove_storm_cone(cone_id)
        self._refresh_storm_cone_layer()
        log.info("remote storm cone deleted: %s", cone_id)

    def _expire_storm_cones(self):
        """Remove any storm cone whose created_at is more than 1 hour ago.

        Called every 60 s by _cone_expire_timer.  Expired cones are silently
        removed from the map and dict — no MQTT delete is published, because
        the broker's own 1-hour message-expiry will have already purged the
        retained message on the broker side.
        """
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        expired = [
            cone_id
            for cone_id, cone in list(self._storm_cones.items())
            if cone.created_at < cutoff
        ]
        for cone_id in expired:
            self._storm_cones.pop(cone_id, None)
            self.map_widget.remove_storm_cone(cone_id)
            log.info("storm cone auto-expired (>1 h): %s", cone_id)
        if expired:
            self._refresh_storm_cone_layer()

    def _init_measure(self):
        self._measure_has_anchor = False
        self._measure_complete = False
        self.btn_measure.toggled.connect(self._on_measure_toggled)

    def _on_measure_toggled(self, active: bool):
        if active:
            self._click_modes.enter("measure")
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(True)
            self._set_placement_prompt("measure — click first point")
        else:
            self._click_modes.exit("measure")
            # if user exits mid-measure after first point, clear partial artifacts.
            if self._measure_has_anchor or self._measure_complete:
                self.map_widget.clear_measure()
            self._measure_has_anchor = False
            self._measure_complete = False
            self.map_widget.set_measure_mode(False)
            self._clear_placement_prompt()

    def _on_measure_click(self, lat: float, lon: float):
        if self._measure_complete:
            self._set_placement_prompt("measure complete", needs_click=False)
            return

        self.map_widget.measure_click(lat, lon)
        if not self._measure_has_anchor:
            self._measure_has_anchor = True
            self._set_placement_prompt("measure — click second point")
        else:
            # second point placed — keep tool selected so user can toggle off to clear.
            self._measure_has_anchor = False
            self._measure_complete = True
            self.map_widget.set_measure_mode(False)   # reset cursor while keeping line visible
            self._set_placement_prompt("measure complete", needs_click=False)
