"""The map's right-click menu: what to offer here, and running the chosen item.

The contents come from ``ui.widgets.map_context_menu.build_menu``. Each action
goes through the same handler, or enters the same click mode, as its toolbar
path, so the menu adds no second way of doing anything.
"""

import json
import logging

import config
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QMessageBox
from data.stations.sounding_utils import (
    _haversine_km, nearest_obs_station, nssl_within_radius_km,
)
from ui.widgets import map_context_menu
from ui.widgets.map_context_menu import MenuContext

log = logging.getLogger("ui.app.main_window")


class MainWindowContextMenuMixin:

    def _on_map_context_menu(self, lat: float, lon: float, feature_json: str):
        if self._click_modes.is_active("track"):
            items = self._track_context_menu(lat, lon, feature_json)
        # a tool that owns map clicks keeps right-click to itself (the page checks too)
        elif self._click_modes.is_active():
            return
        else:
            items = map_context_menu.build_menu(self._context_menu_context(lat, lon, feature_json))
        item = map_context_menu.show_menu(items, self, QCursor.pos())
        if item is not None:
            self._run_context_action(item.action, item.args)

    def _context_menu_context(self, lat: float, lon: float, feature_json: str) -> MenuContext:
        feature = None
        if feature_json:
            try:
                feature = json.loads(feature_json)
            except (json.JSONDecodeError, ValueError):
                log.warning("context menu: bad feature json %r", feature_json[:200])

        ctx = MenuContext(lat=lat, lon=lon, mode=self._session_mode(), feature=feature)
        ctx.hrrr = not self._archive and hasattr(self, "_sounding_fetcher")
        ctx.annotations = hasattr(self, "_annotation_sync")
        ctx.cones = hasattr(self, "_storm_cones")
        ctx.radar_sites = self._nearest_radar_sites(lat, lon)

        raob_at = (lat, lon)
        if feature and feature.get("kind") == "surface_station":
            raob_at = (feature.get("lat", lat), feature.get("lon", lon))
        ctx.raob = nearest_obs_station(*raob_at)

        nssl = getattr(self, "_last_nssl_sset", None)
        ctx.clamps = (
            hasattr(self, "_clamps_sounding_fetcher")
            and nssl is not None
            and nssl_within_radius_km(nssl.lat, nssl.lon, lat, lon)
        )

        if feature:
            kind, fid = feature.get("kind"), feature.get("id", "")
            if kind == "annotation":
                annotation = getattr(self, "_annotations", {}).get(fid)
                if annotation is None:
                    ctx.feature = None
                else:
                    feature["type_key"] = annotation.type_key
            elif kind == "drawing":
                drawing = getattr(self, "_drawings", {}).get(fid)
                if drawing is None:
                    ctx.feature = None
                else:
                    feature["title"] = drawing.title
            elif kind == "vehicle":
                ctx.vehicle_followable = self._vehicle_followable(fid)
                if self._archive:
                    ctx.vehicle_timeseries = hasattr(self, "_archive_mqtt")
                else:
                    ctx.vehicle_timeseries = bool(self._vehicle_history.get(fid))
        return ctx

    def _vehicle_followable(self, vehicle_id: str) -> bool:
        """Follow mode tracks this truck when it's on the map, else the first selected vehicle."""
        if vehicle_id not in self._vehicles:
            return False
        return vehicle_id == config.VEHICLE_ID or config.VEHICLE_ID not in self._vehicles

    def _nearest_radar_sites(self, lat: float, lon: float, count: int = 3):
        excluded = set()
        if self._archive:
            from archive.fetchers.radar_archive_fetcher import ARCHIVE_UNAVAILABLE_STATIONS  # noqa: PLC0415
            excluded = set(ARCHIVE_UNAVAILABLE_STATIONS)
        sites = []
        for info in (self._radar_station_sites or []):
            site_id = info.get("site_id", "")
            if not site_id or site_id in excluded:
                continue
            sites.append((site_id, _haversine_km(lat, lon, info.get("lat", 0), info.get("lon", 0))))
        sites.sort(key=lambda s: s[1])
        return tuple(sites[:count])

    def _run_context_action(self, action: str, args: tuple):
        handler = getattr(self, f"_ctx_{action}", None)
        if handler is None:
            log.warning("context menu: no handler for %r", action)
            return
        handler(*args)

    # ── soundings ─────────────────────────────────────────
    def _ctx_sounding_hrrr(self, lat: float, lon: float):
        self._on_sounding_map_click(lat, lon)

    def _ctx_sounding_raob(self, station_id: str):
        from data.stations.sounding_stations import STATIONS  # noqa: PLC0415
        info = STATIONS.get(station_id)
        if info is None:
            return
        args = (station_id, info["name"], info["lat"], info["lon"], info["elev"])
        if self._archive:
            self._on_archive_obs_station_click(*args)
        else:
            self._on_obs_station_click(*args)

    def _ctx_sounding_clamps(self):
        self.status_msg_label.setText("Fetching NSSL soundings…")
        self._clamps_sounding_fetcher.fetch()

    # ── routing and measuring ─────────────────────────────
    def _ctx_route_to(self, lat: float, lon: float):
        if not (self._viewer or self._monitor or self._archive):
            # from this truck's GPS fix, as a private pin's "Route to pin" does
            self._on_private_pin_route_requested(lat, lon, "")
            return
        self.routing_controls.set_point("dest", lat, lon)
        self.btn_route.setChecked(True)
        self._layout_overlays()

    def _ctx_route_from(self, lat: float, lon: float):
        self.routing_controls.set_point("origin", lat, lon)
        self.btn_route.setChecked(True)
        self._layout_overlays()

    def _ctx_measure_from(self, lat: float, lon: float):
        self.btn_measure.setChecked(True)
        self._click_modes.click(lat, lon)

    # ── annotations and drawings ──────────────────────────
    def _ctx_annotation_place(self, type_key: str, lat: float, lon: float):
        # one placement, then the tool is off again
        self._on_annotation_tool_selected(type_key)
        self._click_modes.click(lat, lon)
        self._click_modes.exit("annotation")

    def _ctx_draw_from(self, type_key: str, lat: float, lon: float):
        # the first point is here; the drawing goes on until a double-click or Esc
        self._on_annotation_tool_selected(type_key)
        self._click_modes.click(lat, lon)

    def _ctx_annotation_edit(self, annotation_id: str):
        self._on_annotation_clicked(annotation_id)

    def _ctx_annotation_move(self, annotation_id: str):
        if annotation_id in self._annotations:
            self._start_annotation_move(annotation_id)

    def _ctx_annotation_delete(self, annotation_id: str):
        annotation = self._annotations.get(annotation_id)
        if annotation is not None and self._confirm_context_delete("this annotation"):
            self._delete_annotation(annotation_id)

    def _ctx_drawing_edit(self, drawing_id: str):
        self._on_drawing_clicked(drawing_id)

    def _ctx_drawing_move(self, drawing_id: str):
        self._start_drawing_move(drawing_id)

    def _ctx_drawing_delete(self, drawing_id: str):
        if drawing_id in self._drawings and self._confirm_context_delete("this drawing"):
            self._delete_drawing(drawing_id)

    def _ctx_cone_edit(self, cone_id: str):
        self._on_storm_cone_clicked(cone_id)

    def _ctx_cone_move(self, cone_id: str):
        self._start_storm_cone_move(cone_id)

    def _ctx_cone_delete(self, cone_id: str):
        if cone_id in self._storm_cones and self._confirm_context_delete("this storm cone"):
            self._delete_storm_cone(cone_id)

    def _confirm_context_delete(self, what: str) -> bool:
        team = "" if self._viewer else " It is removed for the whole team."
        answer = QMessageBox.question(self, "Delete", f"Delete {what}?{team}")
        return answer == QMessageBox.StandardButton.Yes

    # ── features ──────────────────────────────────────────
    def _ctx_hazard_text(self, features: tuple):
        self._on_spc_feature_clicked(json.dumps(list(features)))

    def _ctx_vehicle_follow(self, vehicle_id: str):
        if vehicle_id not in self._vehicles:
            return
        if vehicle_id != config.VEHICLE_ID:
            # follow tracks the first selected vehicle when this truck isn't on the map
            self._selected_vehicle_ids = [vehicle_id] + [
                v for v in self._selected_vehicle_ids if v != vehicle_id
            ]
            self._refresh_vehicle_panel()
        self._on_recenter_clicked()

    def _ctx_vehicle_center(self, vehicle_id: str):
        v = self._vehicles.get(vehicle_id)
        if v is None:
            return
        if self._follow_mode and vehicle_id != self._follow_target_id():
            self._set_follow_mode(False)
        self.map_widget.fly_to(v.lat, v.lon)

    def _ctx_vehicle_timeseries(self, vehicle_id: str):
        self._on_timeseries_button_clicked(vehicle_id)

    # ── the point itself ──────────────────────────────────
    def _ctx_radar_site(self, site: str):
        if self._radar_station_picker_visible:
            self._set_radar_station_picker_visible(False)
        self._on_radar_station_clicked(site)

    def _ctx_private_pin(self, lat: float, lon: float):
        self.map_widget.add_private_pin(lat, lon)

    def _ctx_copy_latlon(self, lat: float, lon: float):
        QApplication.clipboard().setText(f"{lat:.5f}, {lon:.5f}")
        self.status_msg_label.setText(f"Copied {lat:.5f}, {lon:.5f}")
