# ui/station_plot_layer.py
# Renders MetPy station plot PNGs and pushes them to the map as custom markers.
#
# Performance notes:
#   - Uses matplotlib.figure.Figure directly (not plt.figure) to avoid creating
#     pyplot global state and the overhead that comes with it.
#   - Caches the last rendered PNG per vehicle keyed on the obs data fingerprint.
#     If the same obs values arrive again (e.g. from a slow update cycle) the
#     cached bytes are reused and no re-render happens.
#   - On slower laptops the render takes ~30-80 ms — caching keeps this from
#     firing on every clock tick or redundant obs update.

from __future__ import annotations
import io
import math
import logging

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from metpy.plots import StationPlot

from core.observation import Observation

log = logging.getLogger(__name__)


# ── Rendering ─────────────────────────────────────────────────────────────────

def _obs_fingerprint(obs: Observation) -> tuple:
    """
    A hashable key representing the displayable content of an observation.
    Used to skip re-renders when obs data hasn't changed since last draw.
    Rounded to 4 decimal places for lat/lon so minor GPS jitter doesn't
    trigger unnecessary redraws.
    """
    return (
        round(obs.lat, 4),
        round(obs.lon, 4),
        round(obs.temperature_c,  1) if obs.temperature_c  is not None else None,
        round(obs.dewpoint_c,     1) if obs.dewpoint_c     is not None else None,
        round(obs.wind_speed_ms,  1) if obs.wind_speed_ms  is not None else None,
        round(obs.wind_dir_deg,   0) if obs.wind_dir_deg   is not None else None,
        round(obs.pressure_mb,    1) if obs.pressure_mb    is not None else None,
    )


def _render(obs: Observation) -> bytes:
    """
    Render a station plot for *obs* and return a transparent PNG as bytes.
    The image is 135×135 px (1.5" × 1.5" at 90 dpi).

    Uses Figure() directly (not pyplot) to avoid global pyplot state and
    the extra overhead of the pyplot figure manager.
    """
    fig = Figure(figsize=(1.5, 1.5), dpi=90)
    # FigureCanvasAgg must be attached so savefig() works without a display
    FigureCanvasAgg(fig)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-1, 1)
    ax.set_ylim(-1, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_alpha(0.0)
    ax.patch.set_alpha(0.0)

    sp = StationPlot(ax, [0.0], [0.0], fontsize=9, spacing=22)

    # Temperature (°F) — upper-left, red
    if obs.temperature_c is not None:
        t_f = round(obs.temperature_c * 9 / 5 + 32)
        sp.plot_parameter("NW", [t_f], color="#FF6464")

    # Dewpoint (°F) — lower-left, green
    if obs.dewpoint_c is not None:
        dp_f = round(obs.dewpoint_c * 9 / 5 + 32)
        sp.plot_parameter("SW", [dp_f], color="#64FF96")

    # Pressure encoding — upper-right, white
    # Standard station plot encoding: last 3 digits of (mb × 10), zero-padded.
    # e.g. 1013.2 mb → 1132 → "132"  |  965.8 mb → 9658 → "658"
    if obs.pressure_mb is not None:
        pres_code = int(round(obs.pressure_mb * 10)) % 1000
        sp.plot_parameter("NE", [float(pres_code)], color="#E8EAF0",
                          formatter=lambda v: f"{int(round(v)) % 1000:03d}")

    # Wind barb — white
    # MetPy expects u/v components in knots pointing *into* the station
    # (meteorological convention: u/v point toward where the wind is going FROM).
    # Negate sin/cos to convert "wind coming from" direction to "wind going to".
    if obs.wind_speed_ms is not None and obs.wind_dir_deg is not None:
        spd_kts = obs.wind_speed_ms * 1.94384   # m/s → knots for MetPy barbs
        dir_rad = math.radians(obs.wind_dir_deg)
        u = -spd_kts * math.sin(dir_rad)
        v = -spd_kts * math.cos(dir_rad)
        sp.plot_barb([u], [v], color="#E8EAF0")

    # Center station dot
    ax.plot([0], [0], "o", color="white", markersize=3, zorder=5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, bbox_inches="tight",
                pad_inches=0, dpi=90)
    buf.seek(0)
    return buf.read()


# ── Layer ─────────────────────────────────────────────────────────────────────

class StationPlotLayer:
    """
    Manages station-plot PNG markers on the MapWidget.

    Maintains a per-vehicle PNG cache keyed on obs content so unchanged
    observations never trigger a re-render (important on slow field laptops).
    """

    def __init__(self, map_widget):
        self._map = map_widget
        # cache: vehicle_id → (fingerprint_tuple, png_bytes)
        self._cache: dict[str, tuple[tuple, bytes]] = {}

    def update(self, vehicle_id: str, lat: float, lon: float, obs: Observation) -> None:
        """Re-render the station plot for *vehicle_id* only if obs data changed."""
        fp = _obs_fingerprint(obs)

        cached = self._cache.get(vehicle_id)
        if cached and cached[0] == fp:
            # obs data identical to last render — reuse cached PNG, skip render
            log.debug("StationPlotLayer: cache hit for %s, skipping render", vehicle_id)
            return

        try:
            png_bytes = _render(obs)
        except Exception as e:
            log.error("StationPlotLayer: render failed for %s: %s", vehicle_id, e, exc_info=True)
            return

        self._cache[vehicle_id] = (fp, png_bytes)
        self._map.add_station_plot(vehicle_id, lat, lon, png_bytes)
        log.debug("StationPlotLayer: rendered and pushed %s", vehicle_id)

    def remove(self, vehicle_id: str) -> None:
        self._cache.pop(vehicle_id, None)
        self._map.remove_station_plot(vehicle_id)

    def set_visible(self, visible: bool) -> None:
        self._map.set_station_plots_visible(visible)
