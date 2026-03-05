# ui/history_widget.py
# Compact 4-panel time-series widget showing the last 10 minutes of
# meteorological data for a single vehicle.
#
# Uses matplotlib via FigureCanvasQTAgg (already a project dependency)
# to avoid adding PyQtGraph.  Creates the Figure directly (not via pyplot)
# so there is no global backend conflict with radar_overlay.py.
#
# Panels (top→bottom): Temperature (°F), Dewpoint (°F),
#                       Wind Speed (kts),  Pressure (mb)

import logging
from datetime import timezone

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.dates as mdates

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt

from core.observation import Observation

log = logging.getLogger(__name__)

# ── Theme constants (match ui/theme.py colours) ────────────────────────────────
_BG       = "#0A0A0F"
_PANEL_BG = "#0F0F1A"
_GRID     = "#1E1E2E"
_TEXT     = "#B5BDCC"

_COLORS = {
    "temp": "#FF6B35",   # orange — temperature
    "dewp": "#4A9EFF",   # blue   — dewpoint
    "wind": "#39D98A",   # green  — wind speed
    "pres": "#FFD166",   # yellow — pressure
}

_YLABELS  = ["T (°F)", "Td (°F)", "Spd (kt)", "Pres (mb)"]
_COLORKEYS = ["temp", "dewp", "wind", "pres"]


class HistoryWidget(QWidget):
    """
    Embed in the vehicle dock panel.  Call update() whenever new obs arrive.

    The widget always shows data for the most recently updated vehicle.
    Pass obs_list oldest-first (ObsHistoryStore.get() returns them that way).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_vehicle: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(2)

        self._title = QLabel("HISTORY — no data")
        self._title.setStyleSheet(
            f"color: {_TEXT}; font-size: 9px; font-weight: 600;"
            f" letter-spacing: 0.5px; padding: 0 2px;"
        )
        self._title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._title)

        # Build figure directly — no pyplot call, no global backend side-effects
        self._fig = Figure(figsize=(2.8, 3.2), facecolor=_BG)
        self._axes = self._fig.subplots(4, 1, sharex=True)
        self._fig.subplots_adjust(
            left=0.20, right=0.97, top=0.97, bottom=0.13, hspace=0.18
        )

        for ax, label in zip(self._axes, _YLABELS):
            _style_ax(ax, label)

        self._axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self._axes[-1].tick_params(axis="x", colors=_TEXT, labelsize=6)

        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setMinimumHeight(170)
        self._canvas.setMaximumHeight(220)
        layout.addWidget(self._canvas)

    # ── Public API ─────────────────────────────────────────────────────────────

    def update(self, vehicle_id: str, obs_list: list[Observation]) -> None:
        """
        Redraw all panels with fresh data.
        obs_list should be in chronological order (oldest first).
        """
        self._current_vehicle = vehicle_id
        n = len(obs_list)
        self._title.setText(f"HISTORY — {vehicle_id}  ({n} obs)")

        if not obs_list:
            for ax in self._axes:
                ax.cla()
                _style_ax(ax, "")
            self._canvas.draw_idle()
            return

        # convert tz-aware UTC datetimes → naive UTC for matplotlib
        times = [o.timestamp.replace(tzinfo=None) for o in obs_list]

        series = [
            [_c_to_f(o.temperature_c) for o in obs_list],
            [_c_to_f(o.dewpoint_c)    for o in obs_list],
            [_ms_to_kt(o.wind_speed_ms) for o in obs_list],
            [o.pressure_mb              for o in obs_list],
        ]

        for ax, vals, ckey, label in zip(self._axes, series, _COLORKEYS, _YLABELS):
            ax.cla()
            _style_ax(ax, label)

            pairs = [(t, v) for t, v in zip(times, vals) if v is not None]
            if pairs:
                t_plot, v_plot = zip(*pairs)
                ax.plot(
                    t_plot, v_plot,
                    color=_COLORS[ckey],
                    linewidth=1.0,
                    marker=".",
                    markersize=2,
                )

        self._axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self._axes[-1].tick_params(axis="x", colors=_TEXT, labelsize=6)
        self._fig.autofmt_xdate(rotation=0, ha="center")
        self._canvas.draw_idle()

    def clear(self) -> None:
        for ax in self._axes:
            ax.cla()
            _style_ax(ax, "")
        self._canvas.draw_idle()
        self._title.setText("HISTORY — no data")
        self._current_vehicle = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _style_ax(ax, ylabel: str) -> None:
    ax.set_facecolor(_PANEL_BG)
    ax.grid(True, color=_GRID, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.tick_params(colors=_TEXT, labelsize=6, length=2)
    ax.set_ylabel(ylabel, color=_TEXT, fontsize=6, labelpad=2)


def _c_to_f(c: float | None) -> float | None:
    return c * 9 / 5 + 32 if c is not None else None


def _ms_to_kt(ms: float | None) -> float | None:
    return ms * 1.94384 if ms is not None else None
