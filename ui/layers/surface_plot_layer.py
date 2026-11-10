from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.observation import Observation
from ui.layers.station_plot_layer import _display_key, render_cached

log = logging.getLogger(__name__)


def _surface_obs_fingerprint(obs: Observation, station_id: str = "") -> tuple:
    """Displayed values, position and age-colour bucket — not the timestamp.

    The colour is re-evaluated each fetch cycle, so a station that stops
    reporting still turns yellow, then red.
    """
    color = SurfacePlotLayer._obs_age_color(obs, station_id)
    return (_display_key(obs), round(obs.lat, 5), round(obs.lon, 5), color)


def thin_stations(items: list[dict], cell_deg: float) -> list[dict]:
    """Keep one station per cell_deg x cell_deg cell (lite mode), in input order.

    Deterministic, so the same stations survive every fetch cycle and plots
    don't flicker between neighbours.
    """
    if cell_deg <= 0:
        return items
    seen: set[tuple[int, int]] = set()
    kept = []
    for item in sorted(items, key=lambda it: it["id"]):
        obs = item["obs"]
        cell = (int(obs.lat // cell_deg), int(obs.lon // cell_deg))
        if cell in seen:
            continue
        seen.add(cell)
        kept.append(item)
    return kept


def render_surface(fp: tuple) -> bytes:
    """PNG for a surface fingerprint, from the shared station plot cache."""
    return render_cached(fp[0], center_color=fp[3])


class SurfacePlotLayer:
    """Manages independently-toggleable surface station plots on the map."""

    def __init__(self, map_widget):
        self._map = map_widget
        self._cache: dict[str, tuple[tuple, bytes]] = {}

    def update(self, station_id: str, lat: float, lon: float, obs: Observation, name: str = "") -> None:
        fp = _surface_obs_fingerprint(obs, station_id)
        cached = self._cache.get(station_id)
        if cached and cached[0] == fp:
            return

        try:
            png_bytes = render_surface(fp)
        except Exception as exc:
            log.error("SurfacePlotLayer: render failed for %s: %s", station_id, exc, exc_info=True)
            return

        self._cache[station_id] = (fp, png_bytes)
        self._map.add_surface_station_plot(station_id, lat, lon, png_bytes, name=name)

    def remove(self, station_id: str) -> None:
        self._cache.pop(station_id, None)
        self._map.remove_surface_station_plot(station_id)

    def clear(self) -> None:
        for station_id in list(self._cache):
            self.remove(station_id)

    def set_visible(self, visible: bool) -> None:
        self._map.set_surface_station_plots_visible(visible)

    @staticmethod
    def _obs_age_color(obs: Observation, station_id: str = "") -> str:
        ts = obs.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        age_min = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 60.0)
        if station_id.startswith("surface:asos:"):
            # asos reports hourly — use wider freshness thresholds
            if age_min <= 70.0:
                return "#39D98A"
            if age_min <= 90.0:
                return "#FFD166"
            return "#E53935"
        # ok Mesonet / WTM report every 5 min
        if age_min <= 5.0:
            return "#39D98A"
        if age_min <= 10.0:
            return "#FFD166"
        return "#E53935"
