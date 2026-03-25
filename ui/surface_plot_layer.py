from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.observation import Observation
from ui.station_plot_layer import _obs_fingerprint, _render

log = logging.getLogger(__name__)


class SurfacePlotLayer:
    """Manages independently-toggleable surface station plots on the map."""

    def __init__(self, map_widget):
        self._map = map_widget
        self._cache: dict[str, tuple[tuple, bytes]] = {}

    def update(self, station_id: str, lat: float, lon: float, obs: Observation, name: str = "") -> None:
        fp = _obs_fingerprint(obs)
        cached = self._cache.get(station_id)
        if cached and cached[0] == fp:
            return

        try:
            png_bytes = _render(
                obs,
                center_color=self._obs_age_color(obs),
                pressure_mode="full",
            )
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
    def _obs_age_color(obs: Observation) -> str:
        age_min = max(0.0, (datetime.now(timezone.utc) - obs.timestamp).total_seconds() / 60.0)
        if age_min <= 5.0:
            return "#39D98A"
        if age_min <= 10.0:
            return "#FFD166"
        return "#E53935"
