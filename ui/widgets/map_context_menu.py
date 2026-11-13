"""The map's right-click menu.

``build_menu`` turns what is under the cursor and what this launch mode allows
into a tree of ``Item``s, without touching Qt, so the contents can be tested per
mode and feature. ``show_menu`` turns that tree into a ``QMenu`` and returns the
chosen item. Running the chosen action is up to the caller, which sends each one
through the same handler as its toolbar path.

Features arrive from the map page as dicts with a ``kind``:
``hazard`` (``features``: the list the left-click handler gets), ``annotation`` /
``drawing`` / ``cone`` / ``vehicle`` (``id``), ``surface_station`` (``id``,
``label``, ``lat``, ``lon``), and in TRACK mode ``track_point`` (``id``) and
``track_marker``. The caller adds what only Python knows, such as an
annotation's ``type_key``.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.annotation import ANNOTATION_TYPE_MAP
from core.drawing import CUSTOM_TYPES, FRONT_TYPES
from ui.session_state import ARCHIVE, VEHICLE, VIEWER

# point annotations in the order of the ANNOTATE drawer; fork is a fixed screen
# overlay and the storm cone needs two radar fixes, so neither starts at a point
PLACE_TYPES = ("road_closure", "construction", "flooded", "downed_lines", "debris",
               "pressure_system")
DRAW_TYPES = tuple(t["key"] for t in FRONT_TYPES + CUSTOM_TYPES)


@dataclass(frozen=True)
class Item:
    label: str = ""
    action: str = ""                      # "" for a submenu or a separator
    args: tuple = ()
    children: tuple["Item", ...] = ()

    @property
    def is_separator(self) -> bool:
        return not self.label


SEPARATOR = Item()


@dataclass
class MenuContext:
    lat: float
    lon: float
    mode: str = VEHICLE                   # ui.session_state mode key
    feature: dict | None = None
    hrrr: bool = False                    # a point model sounding can be fetched
    raob: dict | None = None              # nearest radiosonde (sounding_utils.nearest_obs_station)
    clamps: bool = False                  # the last CLAMPS launch is in range of this point
    radar_sites: tuple[tuple[str, float], ...] = ()   # (site id, distance km), nearest first
    annotations: bool = False             # annotation and drawing tools are running
    cones: bool = False                   # storm cones are running
    vehicle_followable: bool = False      # follow mode would track this vehicle
    vehicle_timeseries: bool = False      # this vehicle has met history to plot


def _km_to_mi(km: float) -> float:
    return km * 0.621371


def _sounding_menu(ctx: MenuContext, lat: float, lon: float) -> Item | None:
    items = []
    if ctx.hrrr:
        items.append(Item("HRRR", "sounding_hrrr", (lat, lon)))
    if ctx.raob:
        r = ctx.raob
        items.append(Item(
            f"Nearest RAOB: {r['station_id']} ({_km_to_mi(r['distance_km']):.0f} mi)",
            "sounding_raob", (r["station_id"],),
        ))
    if ctx.clamps and ctx.mode != ARCHIVE:
        items.append(Item("CLAMPS", "sounding_clamps"))
    if not items:
        return None
    return Item("Sounding here", children=tuple(items))


def _feature_items(ctx: MenuContext) -> list[Item]:
    f = ctx.feature or {}
    kind = f.get("kind", "")
    fid = f.get("id", "")
    editable = ctx.mode != ARCHIVE

    if kind == "hazard" and f.get("features"):
        return [Item("Open text", "hazard_text", (tuple(f["features"]),))]
    if kind == "annotation" and ctx.annotations and editable and fid:
        items = [Item("Edit…", "annotation_edit", (fid,))]
        if f.get("type_key") != "fork":         # the fork stays centred on screen
            items.append(Item("Move", "annotation_move", (fid,)))
        items.append(Item("Delete", "annotation_delete", (fid,)))
        return items
    if kind == "drawing" and ctx.annotations and editable and fid:
        return [Item("Edit…", "drawing_edit", (fid,)),
                Item("Move", "drawing_move", (fid,)),
                Item("Delete", "drawing_delete", (fid,))]
    if kind == "cone" and ctx.cones and editable and fid:
        return [Item("Edit…", "cone_edit", (fid,)),
                Item("Move", "cone_move", (fid,)),
                Item("Delete", "cone_delete", (fid,))]
    if kind == "vehicle" and fid:
        items = []
        if ctx.vehicle_followable:
            items.append(Item("Follow", "vehicle_follow", (fid,)))
        items.append(Item("Center", "vehicle_center", (fid,)))
        if ctx.vehicle_timeseries:
            items.append(Item("Timeseries", "vehicle_timeseries", (fid,)))
        return items
    if kind == "surface_station" and ctx.raob:
        return [Item("Sounding from nearest RAOB", "sounding_raob",
                     (ctx.raob["station_id"],))]
    return []


def _feature_title(ctx: MenuContext) -> str:
    f = ctx.feature or {}
    kind = f.get("kind", "")
    if kind == "annotation":
        meta = ANNOTATION_TYPE_MAP.get(f.get("type_key", ""), {})
        return meta.get("label", "Annotation")
    if kind == "drawing":
        return f.get("title") or "Drawing"
    if kind == "cone":
        return "Storm cone"
    if kind == "vehicle":
        return f.get("id", "Vehicle")
    if kind == "surface_station":
        return f.get("label") or f.get("id", "Station")
    if kind == "hazard":
        return "Hazard"
    return ""


def build_menu(ctx: MenuContext) -> list[Item]:
    """The menu for a right-click at (``ctx.lat``, ``ctx.lon``), top to bottom.

    Items for the feature under the cursor come first, under a disabled title;
    the items for the point itself always follow, since a warning polygon can
    cover most of the screen.
    """
    lat, lon = ctx.lat, ctx.lon
    items: list[Item] = []

    feature_items = _feature_items(ctx)
    if feature_items:
        items.append(Item(_feature_title(ctx), "title"))
        items.extend(feature_items)
        items.append(SEPARATOR)

    sounding = _sounding_menu(ctx, lat, lon)
    if sounding is not None:
        items.append(sounding)

    if ctx.mode != VIEWER:
        items.append(Item("Route to here", "route_to", (lat, lon)))
        items.append(Item("Route from here", "route_from", (lat, lon)))
    items.append(Item("Measure from here", "measure_from", (lat, lon)))

    if ctx.annotations and ctx.mode != ARCHIVE:
        place = []
        for key in PLACE_TYPES:
            if key == "pressure_system":
                label = "High / Low Pressure"
            else:
                label = ANNOTATION_TYPE_MAP[key]["label"]
            place.append(Item(label, "annotation_place", (key, lat, lon)))
        items.append(Item("Place annotation", children=tuple(place)))
        draw_labels = {t["key"]: t["label"] for t in FRONT_TYPES + CUSTOM_TYPES}
        items.append(Item("Draw", children=tuple(
            Item(draw_labels[key], "draw_from", (key, lat, lon)) for key in DRAW_TYPES
        )))

    if ctx.radar_sites:
        items.append(Item("Nearest radar site", children=tuple(
            Item(f"{site}  ({_km_to_mi(km):.0f} mi)", "radar_site", (site,))
            for site, km in ctx.radar_sites[:3]
        )))

    items.append(SEPARATOR)
    items.append(Item("Add private pin", "private_pin", (lat, lon)))
    items.append(Item(f"Copy {lat:.5f}, {lon:.5f}", "copy_latlon", (lat, lon)))
    return items


def build_track_menu(ctx: MenuContext, marker_label: str | None) -> list[Item]:
    """The menu while TRACK is editing (as in MESO-VIEW): place or move the one
    reference marker, rename or remove it, or select / delete the point under
    the cursor. ``marker_label`` is the marker's label, None when there's none."""
    f = ctx.feature or {}
    items: list[Item] = []
    if f.get("kind") == "track_point":
        pid = f.get("id")
        items += [Item(f"Track point {pid}", "title"),
                  Item("Select", "track_point_select", (pid,)),
                  Item("Delete", "track_point_delete", (pid,)),
                  SEPARATOR]
    elif f.get("kind") == "track_marker" and marker_label is not None:
        items += [Item(f"Marker {marker_label}", "title")]
    items.append(Item("Move marker here" if marker_label is not None else "Add marker",
                      "track_marker_add", (ctx.lat, ctx.lon)))
    if marker_label is not None:
        items.append(Item("Rename marker…", "track_marker_rename"))
        items.append(Item("Remove marker", "track_marker_remove"))
    return items


def find(items, action: str) -> Item | None:
    """The first item (depth first) with ``action``; for tests and callers."""
    for item in items:
        if item.action == action:
            return item
        hit = find(item.children, action)
        if hit is not None:
            return hit
    return None


def show_menu(items: list[Item], parent, global_pos) -> Item | None:
    """Open ``items`` as a QMenu at ``global_pos``; the chosen item or None."""
    from PyQt6.QtWidgets import QMenu  # noqa: PLC0415

    chosen: dict[int, Item] = {}

    def fill(menu, entries):
        for item in entries:
            if item.is_separator:
                menu.addSeparator()
            elif item.children:
                fill(menu.addMenu(item.label), item.children)
            else:
                act = menu.addAction(item.label)
                if item.action == "title":
                    act.setEnabled(False)
                chosen[id(act)] = item
                act.setData(id(act))

    menu = QMenu(parent)
    fill(menu, items)
    act = menu.exec(global_pos)
    if act is None:
        return None
    return chosen.get(act.data())
