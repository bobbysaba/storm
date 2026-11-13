"""The command palette's providers: what can be run from ⌘K / Ctrl+K right now.

Each provider returns only what this launch mode offers, by the same rules as
the toolbar (a button left out for this mode is left out here), and each command
runs through the same button, shortcut callback or handler as its toolbar path.
"""

import logging
import math

import config
from PyQt6.QtCore import QSettings
from data.fetchers.routing_fetcher import search_places
from ui import shortcuts, text_scale
from ui.command_registry import Command, CommandRegistry
from ui.widgets.command_palette import CommandPalette

log = logging.getLogger("ui.app.main_window")

_RECENT_KEY = "ui/palette_recent"

# drawer key → shortcut action that toggles the same button
_DRAWER_SHORTCUTS = {
    "radar": "radar", "satellite": "satellite", "hazards": "hazards", "surface": "surface",
    "sounding": "sounding", "annotate": "annotate", "track": "track",
}
_DRAWER_NAMES = {
    "radar": "Radar", "prev_locs": "Previous locations", "hazards": "Hazards",
    "satellite": "Satellite", "mesoanalysis": "Mesoanalysis", "sfcoa": "SFCOA",
    "surface": "Surface obs", "sounding": "Sounding", "annotate": "Annotate", "map": "Map tools",
    "route": "Route", "landcover": "Land cover", "track": "Storm track", "lidar": "Doppler lidar",
}
# shortcut actions the palette leaves out: Esc means nothing without a key press,
# and the palette doesn't list itself
_SKIPPED_ACTIONS = {"escape", "palette"}
_SECTOR_NAMES = {"conus": "CONUS", "meso1": "MESO-1", "meso2": "MESO-2"}
_DEPLOY_NEAREST = 5


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _mi(km: float) -> str:
    return f"{km * 0.621371:.0f} mi"


class MainWindowPaletteMixin:

    def _init_command_palette(self):
        settings = QSettings("NSSL", "STORM")
        self._command_registry = reg = CommandRegistry(
            load_recent=lambda: settings.value(_RECENT_KEY, [], type=list),
            save_recent=lambda ids: settings.setValue(_RECENT_KEY, ids),
        )
        reg.add_provider("actions", self._palette_actions)
        reg.add_provider("menu", self._palette_menu)
        reg.add_provider("drawers", self._palette_drawers)
        reg.add_provider("radar", self._palette_radar)
        reg.add_provider("satellite", self._palette_satellite)
        reg.add_provider("spc", self._palette_spc)
        reg.add_provider("vehicles", self._palette_vehicles)
        reg.add_provider("deploy_locs", self._palette_deploy_locs)
        self._command_palette = CommandPalette(
            reg, self._map_container,
            place_search=search_places,
            is_online=lambda: getattr(self, "_net_state", "ok") != "none",
            fly_to=lambda lat, lon: self.map_widget.fly_to(lat, lon, zoom=11),
        )
        self._shortcuts.bind("palette", self._command_palette.toggle)

    # ── availability ──────────────────────────────────────
    def _toolbar_button_available(self, btn) -> bool:
        """True if the toolbar offers ``btn`` in this mode, overflow menu included."""
        if btn is None or not btn.isEnabled():
            return False
        return not btn.isHidden() or btn in self._floating_toolbar.overflowed()

    def _palette_key(self, action: str) -> str:
        spec = self._shortcuts.shortcut(action)
        return shortcuts.display_key(spec.keys[0]) if spec else ""

    # ── providers ─────────────────────────────────────────
    def _palette_actions(self):
        drawer_actions = set(_DRAWER_SHORTCUTS.values())
        for spec in shortcuts.for_mode(self._shortcuts.mode):
            action = spec.action
            callback = self._shortcuts.callbacks.get(action)
            if callback is None or action in _SKIPPED_ACTIONS or action in drawer_actions:
                continue
            attr = self._SHORTCUT_BUTTONS.get(action)
            if attr is not None:
                if not self._toolbar_button_available(getattr(self, attr, None)):
                    continue
            yield Command(f"action:{action}", spec.label, "Action", callback,
                          keys=self._palette_key(action))

        yield Command("action:reset_layout", "Reset layout", "Action", self._on_reset_layout,
                      hint="Close all drawers, default pins and 100% text")
        current = text_scale.percent()
        for pct in text_scale.SCALES:
            yield Command(f"action:text_{pct}", f"Text size {pct}%", "Action",
                          lambda p=pct: self.settings_pill.choose_text_scale(p),
                          hint="current" if pct == current else "")

    def _palette_drawers(self):
        for key in self._drawers.keys():
            entry = self._drawers.entry(key)
            btn = entry.button
            if entry.child:
                # ROUTE and LAND COVER live inside the MAP drawer
                if btn.isHidden() or not btn.isEnabled():
                    continue
            elif not self._toolbar_button_available(btn):
                continue
            name = _DRAWER_NAMES.get(key, key.title())
            verb = "Close" if btn.isChecked() else "Open"
            action = _DRAWER_SHORTCUTS.get(key)
            yield Command(f"drawer:{key}", f"{verb} {name} drawer", "Drawer", btn.click,
                          keys=self._palette_key(action) if action else "",
                          keywords=f"{name} drawer toggle show hide")

    def _palette_radar(self):
        if not hasattr(self, "radar_controls"):
            return
        here = self._palette_position()
        sites = list(self._radar_station_sites or [])
        if here is not None:
            sites.sort(key=lambda s: _km(here[0], here[1], s["lat"], s["lon"]))
        current = self.radar_controls.current_site()
        excluded = set()
        if self._archive:
            from archive.fetchers.radar_archive_fetcher import ARCHIVE_UNAVAILABLE_STATIONS  # noqa: PLC0415
            excluded = set(ARCHIVE_UNAVAILABLE_STATIONS)
        for info in sites:
            site = info["site_id"]
            if site in excluded:
                continue
            hint = []
            if site == current:
                hint.append("current")
            if here is not None:
                hint.append(_mi(_km(here[0], here[1], info["lat"], info["lon"])))
            yield Command(f"site:{site}", f"{site} {info.get('name', '')}".strip(), "Radar site",
                          lambda s=site: self._ctx_radar_site(s), hint=" · ".join(hint))
        product = self.radar_controls.current_product()
        for code, label in self.radar_controls.products():
            yield Command(f"product:{code}", f"Radar {label}", "Radar product",
                          lambda c=code: self.radar_controls.select_product(c),
                          hint="current" if code == product else "", keywords=code)

    def _palette_satellite(self):
        if not hasattr(self, "satellite_controls"):
            return
        current = self.satellite_controls.current_mode()
        for mode, btn in self.satellite_controls.sector_buttons().items():
            if not btn.isEnabled():
                continue
            yield Command(f"satellite:{mode}", f"Satellite {_SECTOR_NAMES[mode]}", "Satellite",
                          lambda m=mode: self.satellite_controls.select_mode(m),
                          hint="current" if mode == current else "")
        band = self.satellite_controls.current_band()
        for key, btn in self.satellite_controls.band_buttons().items():
            if not btn.isEnabled():
                continue
            label = "infrared (IR)" if key == "ir" else "visible (VIS)"
            yield Command(f"satellite_band:{key}", f"Satellite {label}", "Satellite",
                          lambda b=key: self.satellite_controls.select_band(b),
                          hint="current" if key == band else "", keywords=key)

    def _palette_spc(self):
        if not hasattr(self, "hazard_controls"):
            return
        hc = self.hazard_controls
        day = hc.current_spc_day()
        for d in range(1, 9):
            yield Command(f"spc_day:{d}", f"SPC Day {d}", "SPC",
                          lambda d=d: hc.select_day(d), hint="current" if d == day else "")
        for key, label in hc.spc_products():
            yield Command(f"spc_product:{key}", f"SPC Day {day} {label.title()}", "SPC",
                          lambda k=key: hc.select_spc_product(k), keywords=f"outlook {key}")
        names = {"watches": "SPC watches", "mds": "SPC mesoscale discussions",
                 "nws_warnings": "NWS warnings", "cwa": "NWS CWA borders"}
        for key, btn in hc.overlay_buttons().items():
            if btn.isHidden() or not btn.isEnabled():
                continue
            verb = "Hide" if btn.isChecked() else "Show"
            yield Command(f"hazard:{key}", f"{verb} {names[key]}", "Hazards", btn.click,
                          keywords=key)

    def _palette_vehicles(self):
        for vid, v in sorted(self._vehicles.items()):
            yield Command(f"vehicle_center:{vid}", f"Center on {vid}", "Vehicle",
                          lambda i=vid: self._ctx_vehicle_center(i))
            if self._vehicle_followable(vid):
                yield Command(f"vehicle_follow:{vid}", f"Follow {vid}", "Vehicle",
                              lambda i=vid: self._ctx_vehicle_follow(i))

    def _palette_deploy_locs(self):
        points = getattr(self, "_deploy_points", None)
        if not points or not self._toolbar_button_available(getattr(self, "btn_prev_locs", None)):
            return
        here = self._palette_position() or (config.HOME_LAT, config.HOME_LON)
        # cheap planar distance to pick candidates, then real distance for the label
        coslat = math.cos(math.radians(here[0]))
        nearest = sorted(
            points,
            key=lambda p: (p["lat"] - here[0]) ** 2 + ((p["lon"] - here[1]) * coslat) ** 2,
        )[:_DEPLOY_NEAREST]
        for p in nearest:
            km = _km(here[0], here[1], p["lat"], p["lon"])
            yield Command(
                f"deploy:{p['lat']:.4f},{p['lon']:.4f}",
                f"Deployment location, ABI rank {p['rank_abi']}", "Deploy location",
                lambda p=p: self._palette_show_deploy_loc(p["lat"], p["lon"]),
                hint=f"{_mi(km)} · {p['lat']:.4f}, {p['lon']:.4f}",
                keywords="deploy nearest prev locs",
            )

    def _palette_show_deploy_loc(self, lat: float, lon: float):
        if not self.btn_prev_locs.isChecked():
            self.btn_prev_locs.click()
        self.map_widget.fly_to(lat, lon, zoom=12)

    def _palette_position(self):
        """This truck's position when it is on the map, else None."""
        v = self._vehicles.get(config.VEHICLE_ID)
        return (v.lat, v.lon) if v is not None else None
