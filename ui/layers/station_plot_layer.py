
from __future__ import annotations
import io
import math
import logging
import threading
from collections import OrderedDict

from core.observation import Observation

log = logging.getLogger(__name__)



_CALM_KT = 2.0
_PNG_CACHE_SIZE = 512

# shared across vehicle and surface layers; also used from the surface render thread
_png_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_png_cache_lock = threading.Lock()


def _display_key(obs: Observation) -> tuple:
    """
    A hashable key holding exactly what the plot shows: temperature and
    dewpoint in whole °F, pressure in whole mb, and the wind barb in whole
    knots and degrees (None when calm). Position is not part of it, so a
    moving vehicle with steady readings never re-renders.
    """
    t_f = round(obs.temperature_c * 9 / 5 + 32) if obs.temperature_c is not None else None
    dp_f = round(obs.dewpoint_c * 9 / 5 + 32) if obs.dewpoint_c is not None else None
    pres = round(obs.pressure_mb) if obs.pressure_mb is not None else None
    wind = None
    if obs.wind_speed_ms is not None and obs.wind_dir_deg is not None:
        spd_kts = obs.wind_speed_ms * 1.94384
        if spd_kts >= _CALM_KT:
            wind = (round(spd_kts), round(obs.wind_dir_deg) % 360)
    return (t_f, dp_f, pres, wind)


def _render_key(key: tuple, center_color: str) -> bytes:
    """
    Render a station plot for a display key and return a transparent PNG.
    The image is 135×135 px (1.5" × 1.5" at 90 dpi).

    Uses Figure() directly (not pyplot) to avoid global pyplot state and
    the extra overhead of the pyplot figure manager.
    """
    # metpy pulls in xarray, pint and cartopy; load it on the first render
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from metpy.plots import StationPlot

    t_f, dp_f, pres, wind = key
    fig = Figure(figsize=(1.5, 1.5), dpi=90)
    # figureCanvasAgg must be attached so savefig() works without a display
    FigureCanvasAgg(fig)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-1, 1)
    ax.set_ylim(-1, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_alpha(0.0)
    ax.patch.set_alpha(0.0)

    sp = StationPlot(ax, [0.0], [0.0], fontsize=11, spacing=17)

    ax.scatter([0.0], [0.0], s=46, c=center_color, edgecolors="#0A0A0F", linewidths=0.8, zorder=5)

    # temperature (°F) — upper-left, red
    if t_f is not None:
        sp.plot_parameter("NW", [t_f], color="#FF6464", fontweight="bold")

    # dewpoint (°F) — lower-left, green
    if dp_f is not None:
        sp.plot_parameter("SW", [dp_f], color="#64FF96", fontweight="bold")

    if pres is not None:
        sp.plot_parameter(
            "NE",
            [float(pres)],
            color="#E8EAF0",
            fontweight="bold",
            formatter=lambda v: f"{int(round(v))}",
        )

    # wind barb — white
    if wind is not None:
        spd_kts, wdir = wind
        dir_rad = math.radians(wdir)
        u = -spd_kts * math.sin(dir_rad)
        v = -spd_kts * math.cos(dir_rad)
        sp.plot_barb([u], [v], color="#E8EAF0")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, bbox_inches="tight",
                pad_inches=0, dpi=90)
    buf.seek(0)
    return buf.read()


def render_cached(key: tuple, center_color: str = "#E8EAF0") -> bytes:
    """PNG for (display key, colour) from the shared LRU, rendering on a miss."""
    cache_key = (key, center_color)
    with _png_cache_lock:
        png = _png_cache.get(cache_key)
        if png is not None:
            _png_cache.move_to_end(cache_key)
            return png
    png = _render_key(key, center_color)
    with _png_cache_lock:
        _png_cache[cache_key] = png
        while len(_png_cache) > _PNG_CACHE_SIZE:
            _png_cache.popitem(last=False)
    return png


def _render(obs: Observation, center_color: str = "#E8EAF0") -> bytes:
    """Render a station plot for *obs* (through the shared cache)."""
    return render_cached(_display_key(obs), center_color)



class StationPlotLayer:
    """
    Manages station-plot PNG markers on the MapWidget.

    Re-renders only when the displayed values change; a position-only change
    just moves the existing marker (important on slow field laptops).
    """

    def __init__(self, map_widget):
        self._map = map_widget
        # vehicle_id → (display key, (lat, lon)) of the marker on the map
        self._shown: dict[str, tuple[tuple, tuple[float, float]]] = {}

    def update(self, vehicle_id: str, lat: float, lon: float, obs: Observation) -> None:
        """Push a new plot if the values changed, else move the marker if it moved."""
        key = _display_key(obs)
        pos = (round(lat, 5), round(lon, 5))

        shown = self._shown.get(vehicle_id)
        if shown and shown[0] == key:
            if shown[1] != pos:
                self._map.move_station_plot(vehicle_id, lat, lon)
                self._shown[vehicle_id] = (key, pos)
            return

        try:
            png_bytes = render_cached(key)
        except Exception as e:
            log.error("StationPlotLayer: render failed for %s: %s", vehicle_id, e, exc_info=True)
            return

        self._shown[vehicle_id] = (key, pos)
        self._map.add_station_plot(vehicle_id, lat, lon, png_bytes)
        log.debug("StationPlotLayer: pushed %s", vehicle_id)

    def remove(self, vehicle_id: str) -> None:
        self._shown.pop(vehicle_id, None)
        self._map.remove_station_plot(vehicle_id)

    def set_visible(self, visible: bool) -> None:
        self._map.set_station_plots_visible(visible)
