# ui/vehicle_timeseries_dialog.py
# Floating timeseries dialog for vehicles pushing meteorological data.

import logging
from collections import deque

import numpy as np
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette, QColor

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from core.observation import Observation

log = logging.getLogger(__name__)

# ── Palette (reuse from sounding_dialog) ─────────────────────────────────────
_FIG_BG    = "#0a0a0f"
_AX_BG     = "#0f0f1a"
_BORDER    = "#2a2a40"
_TEXT      = "#e8eaf0"
_MUTED     = "#666688"

_TEMP_CLR  = "#ff6b6b"
_DEWP_CLR  = "#3ddc84"
_WSPD_CLR  = "#4fc3f7"
_WDIR_CLR  = "#ffd700"
_PRES_CLR  = "#b39ddb"
_CURSOR_CLR = "#ffffff"


def _force_bg(widget):
    bg  = QColor(_FIG_BG)
    txt = QColor(_TEXT)
    pal = widget.palette()
    for role in (
        QPalette.ColorRole.Window,    QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button,
        QPalette.ColorRole.Midlight,  QPalette.ColorRole.Light,
        QPalette.ColorRole.Mid,       QPalette.ColorRole.Dark,
        QPalette.ColorRole.Shadow,
    ):
        pal.setColor(role, bg)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Text,       QPalette.ColorRole.BrightText,
    ):
        pal.setColor(role, txt)
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)


class VehicleTimeseriesDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Vehicle Timeseries")
        self.setMinimumSize(800, 600)
        self.resize(1100, 700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        _force_bg(self)
        self.setStyleSheet(
            f"QDialog {{ background-color: {_FIG_BG}; }}"
            f"QLabel  {{ background-color: transparent; color: {_TEXT}; }}"
        )

        self._observations: list[Observation] = []
        self._vehicle_id: str = ""
        self._cursor_vlines: list = []
        self._zoom_rect = None  # For click-drag zoom selection
        self._zoom_start = None

        self._build_ui()

    # ── Public ───────────────────────────────────────────────────────────────

    def load(self, vehicle_id: str, observations: list[Observation]):
        """Load a new timeseries dataset and redraw."""
        self._vehicle_id = vehicle_id
        self._observations = observations
        self.setWindowTitle(f"Vehicle Timeseries — {vehicle_id}")
        self._draw()
        if not self.isVisible():
            self.show()
        self.raise_()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(3)

        # ── Header ───────────────────────────────────────────────────────────
        self._header_label = QLabel("")
        self._header_label.setStyleSheet(
            f"background-color: transparent; color: {_TEXT}; "
            f"font-size: 11px; font-weight: bold;"
        )
        root.addWidget(self._header_label)

        # ── Matplotlib canvas ─────────────────────────────────────────────────
        self._fig    = Figure(facecolor=_FIG_BG)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setAutoFillBackground(False)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._canvas.mpl_connect("axes_leave_event",    self._on_axes_leave)
        self._canvas.mpl_connect("scroll_event",        self._on_scroll)
        self._canvas.mpl_connect("button_press_event",  self._on_button_press)
        self._canvas.mpl_connect("button_release_event", self._on_button_release)
        root.addWidget(self._canvas, stretch=1)

        # ── Cursor readout (below temp/dewp axis) ─────────────────────────────
        self._cursor_temp_label = QLabel("")
        self._cursor_temp_label.setStyleSheet(
            f"background-color: transparent; color: {_MUTED}; font-size: 9px; font-family: monospace;"
        )
        self._cursor_temp_label.setFixedHeight(14)
        root.addWidget(self._cursor_temp_label)
        
        # ── Canvas for wind panel ─────────────────────────────────────────────
        # (This is a placeholder - we'll position labels between subplots)
        
        # ── Cursor readout (below wind axis) ──────────────────────────────────
        self._cursor_wind_label = QLabel("")
        self._cursor_wind_label.setStyleSheet(
            f"background-color: transparent; color: {_MUTED}; font-size: 9px; font-family: monospace;"
        )
        self._cursor_wind_label.setFixedHeight(14)
        root.addWidget(self._cursor_wind_label)
        
        # ── Cursor readout (below pressure axis) ──────────────────────────────
        self._cursor_pres_label = QLabel("")
        self._cursor_pres_label.setStyleSheet(
            f"background-color: transparent; color: {_MUTED}; font-size: 9px; font-family: monospace;"
        )
        self._cursor_pres_label.setFixedHeight(14)
        root.addWidget(self._cursor_pres_label)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        if not self._observations:
            return

        obs_list = self._observations
        n = len(obs_list)

        # Update header
        time_range = (
            f"{obs_list[0].timestamp.strftime('%H:%M')} – "
            f"{obs_list[-1].timestamp.strftime('%H:%M UTC')}"
        )
        self._header_label.setText(
            f"{self._vehicle_id}  ·  {n} observation{'s' if n != 1 else ''}  ·  {time_range}"
        )

        # Extract time series with 10-second grid (gaps automatically become NaN)
        if not obs_list:
            return
        
        # Create 10-second time grid from first to last observation
        first_time = obs_list[0].timestamp
        last_time = obs_list[-1].timestamp
        total_seconds = int((last_time - first_time).total_seconds())
        
        # Build time array at 10-second intervals
        from datetime import timedelta
        times = [first_time + timedelta(seconds=i*10) for i in range(total_seconds // 10 + 1)]
        
        # Initialize data arrays with NaN
        temp_f = np.full(len(times), np.nan)
        dewp_f = np.full(len(times), np.nan)
        wspd_kt = np.full(len(times), np.nan)
        wdir_deg = np.full(len(times), np.nan)
        pres_mb = np.full(len(times), np.nan)
        
        # Map grid index to original observation for cursor readout
        self._grid_to_obs = {}
        
        # Fill in actual observations at their nearest 10-second grid point
        for o in obs_list:
            # Find nearest grid point (round to nearest 10 seconds)
            seconds_from_start = (o.timestamp - first_time).total_seconds()
            grid_idx = int(round(seconds_from_start / 10))
            
            if 0 <= grid_idx < len(times):
                # Store the observation for this grid point
                self._grid_to_obs[grid_idx] = o
                
                if o.temperature_c is not None:
                    temp_f[grid_idx] = o.temperature_c * 9/5 + 32
                if o.dewpoint_c is not None:
                    dewp_f[grid_idx] = o.dewpoint_c * 9/5 + 32
                if o.wind_speed_ms is not None:
                    wspd_kt[grid_idx] = o.wind_speed_ms * 1.94384
                if o.wind_dir_deg is not None:
                    wdir_deg[grid_idx] = o.wind_dir_deg
                if o.pressure_mb is not None:
                    pres_mb[grid_idx] = o.pressure_mb
        
        times = np.array(times)
        self._plot_times = times  # Store for cursor interaction

        self._fig.clear()
        self._cursor_vlines = []

        # Three panels with custom spacing to fit readout labels between them
        from matplotlib.gridspec import GridSpec
        gs = GridSpec(3, 1, figure=self._fig, hspace=0.35, left=0.08, right=0.92, top=0.96, bottom=0.08)
        
        ax1 = self._fig.add_subplot(gs[0])
        ax2 = self._fig.add_subplot(gs[1], sharex=ax1)
        ax3 = self._fig.add_subplot(gs[2], sharex=ax1)

        for ax in (ax1, ax2, ax3):
            ax.set_facecolor(_AX_BG)
            for sp in ax.spines.values():
                sp.set_edgecolor(_BORDER)
            ax.tick_params(colors=_MUTED, labelsize=8)
            ax.grid(True, color=_BORDER, alpha=0.45, linewidth=0.5)

        # ── Panel 1: Temperature / Dewpoint ───────────────────────────────────
        ax1.plot(times, temp_f, color=_TEMP_CLR, linewidth=1.2)
        ax1.plot(times, dewp_f, color=_DEWP_CLR, linewidth=1.2)
        ax1.set_ylabel('°F', color=_MUTED, fontsize=9)
        ax1.tick_params(labelbottom=False)
        # Tight x-axis bounds (no buffer)
        ax1.set_xlim(times[0], times[-1])

        # ── Panel 2: Wind Speed / Direction (dual y-axes) ─────────────────────
        ax2_right = ax2.twinx()
        ax2.plot(times, wspd_kt, color=_WSPD_CLR, linewidth=1.2)
        ax2.set_ylabel('kt', color=_MUTED, fontsize=9)
        ax2_right.scatter(times, wdir_deg, color=_WDIR_CLR, s=6, alpha=0.7)
        ax2_right.set_ylabel('deg', color=_MUTED, fontsize=9)
        ax2_right.set_ylim(0, 360)
        ax2_right.set_yticks([0, 90, 180, 270, 360])
        ax2_right.tick_params(colors=_MUTED, labelsize=8)
        for sp in ax2_right.spines.values():
            sp.set_edgecolor(_BORDER)
        ax2.tick_params(labelbottom=False)
        # Tight x-axis bounds (no buffer)
        ax2.set_xlim(times[0], times[-1])

        # ── Panel 3: Pressure ──────────────────────────────────────────────────
        ax3.plot(times, pres_mb, color=_PRES_CLR, linewidth=1.2)
        ax3.set_ylabel('mb', color=_MUTED, fontsize=9)
        ax3.set_xlabel("Time (UTC)", color=_MUTED, fontsize=8)
        # Tight x-axis bounds (no buffer)
        ax3.set_xlim(times[0], times[-1])

        # Format x-axis as HH:MM UTC
        import matplotlib.dates as mdates
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=times[0].tzinfo))
        ax3.tick_params(axis="x", rotation=0)

        # Store axes and right axis for cursor interaction
        self._ax1 = ax1
        self._ax2 = ax2
        self._ax2_right = ax2_right
        self._ax3 = ax3

        self._canvas.draw_idle()

    # ── Interactive cursor ────────────────────────────────────────────────────

    def _on_mouse_move(self, event):
        # Check if mouse is in any of the three main axes or the right y-axis
        if event.inaxes not in (self._ax1, self._ax2, self._ax2_right, self._ax3):
            return
        if not hasattr(self, '_plot_times') or not hasattr(self, '_grid_to_obs'):
            return

        # Snap to nearest grid point
        from matplotlib import dates as mdates
        mouse_time = mdates.num2date(event.xdata)
        
        # Find nearest grid index
        time_diffs = [abs((t - mouse_time).total_seconds()) for t in self._plot_times]
        grid_idx = np.argmin(time_diffs)
        
        # Only show readout if there's an actual observation at this grid point
        if grid_idx not in self._grid_to_obs:
            self._cursor_temp_label.setText("")
            self._cursor_wind_label.setText("")
            self._cursor_pres_label.setText("")
            for vline in self._cursor_vlines:
                vline.remove()
            self._cursor_vlines.clear()
            self._canvas.draw_idle()
            return
        
        obs = self._grid_to_obs[grid_idx]
        snap_time = self._plot_times[grid_idx]

        # Format values first, then wrap in color spans
        temp_val = f"{obs.temperature_c * 9/5 + 32:.0f}°F" if obs.temperature_c is not None else "--"
        dewp_val = f"{obs.dewpoint_c * 9/5 + 32:.0f}°F" if obs.dewpoint_c is not None else "--"
        wspd_val = f"{obs.wind_speed_ms * 1.94384:.0f} kt" if obs.wind_speed_ms is not None else "--"
        wdir_val = f"{obs.wind_dir_deg:.0f}°" if obs.wind_dir_deg is not None else "--"
        pres_val = f"{obs.pressure_mb:.1f} mb" if obs.pressure_mb is not None else "--"
        
        # Build colored HTML strings
        temp_str = f"<span style='color:{_TEMP_CLR}'>T {temp_val}</span>"
        dewp_str = f"<span style='color:{_DEWP_CLR}'>Td {dewp_val}</span>"
        wspd_str = f"<span style='color:{_WSPD_CLR}'>{wspd_val}</span>"
        wdir_str = f"<span style='color:{_WDIR_CLR}'>{wdir_val}</span>"
        pres_str = f"<span style='color:{_PRES_CLR}'>{pres_val}</span>"

        self._cursor_temp_label.setText(
            f"{obs.timestamp.strftime('%H:%M:%S UTC')}  ·  {temp_str}  {dewp_str}"
        )
        self._cursor_wind_label.setText(
            f"{obs.timestamp.strftime('%H:%M:%S UTC')}  ·  Speed {wspd_str}  ·  Dir {wdir_str}"
        )
        self._cursor_pres_label.setText(
            f"{obs.timestamp.strftime('%H:%M:%S UTC')}  ·  {pres_str}"
        )

        # Draw vertical line at snapped grid time
        for vline in self._cursor_vlines:
            vline.remove()
        self._cursor_vlines.clear()

        for ax in (self._ax1, self._ax2, self._ax3):
            vline = ax.axvline(snap_time, color=_CURSOR_CLR, linewidth=0.9, alpha=0.4, zorder=10)
            self._cursor_vlines.append(vline)

        self._canvas.draw_idle()

    def _on_axes_leave(self, event):
        self._cursor_temp_label.setText("")
        self._cursor_wind_label.setText("")
        self._cursor_pres_label.setText("")
        for vline in self._cursor_vlines:
            vline.remove()
        self._cursor_vlines.clear()
        self._canvas.draw_idle()

    # ── Zoom/Pan interaction ──────────────────────────────────────────────────

    def _on_scroll(self, event):
        """Zoom in/out with scroll wheel centered on cursor position."""
        if event.inaxes not in (self._ax1, self._ax2, self._ax2_right, self._ax3):
            return
        if not hasattr(self, '_plot_times'):
            return
        
        # Get current x-axis limits
        ax = self._ax1  # All axes share x-axis
        xmin, xmax = ax.get_xlim()
        
        # Zoom factor: scroll up = zoom in, scroll down = zoom out
        zoom_factor = 0.9 if event.button == 'up' else 1.1
        
        # Get cursor position in data coordinates
        from matplotlib import dates as mdates
        cursor_x = event.xdata
        if cursor_x is None:
            return
        
        # Calculate new limits centered on cursor
        range_width = xmax - xmin
        new_width = range_width * zoom_factor
        
        # Keep cursor position at same relative location
        cursor_frac = (cursor_x - xmin) / range_width
        new_xmin = cursor_x - new_width * cursor_frac
        new_xmax = cursor_x + new_width * (1 - cursor_frac)
        
        # Clamp to data bounds
        data_min = mdates.date2num(self._plot_times[0])
        data_max = mdates.date2num(self._plot_times[-1])
        new_xmin = max(new_xmin, data_min)
        new_xmax = min(new_xmax, data_max)
        
        # Apply zoom to all axes
        for ax in (self._ax1, self._ax2, self._ax3):
            ax.set_xlim(new_xmin, new_xmax)
        
        self._canvas.draw_idle()

    def _on_button_press(self, event):
        """Start click-drag zoom selection."""
        if event.inaxes not in (self._ax1, self._ax2, self._ax3):
            return
        if event.button != 1:  # Only left mouse button
            return
        
        self._zoom_start = event.xdata
        
    def _on_button_release(self, event):
        """Complete click-drag zoom selection."""
        if self._zoom_start is None:
            return
        if event.inaxes not in (self._ax1, self._ax2, self._ax3):
            self._zoom_start = None
            return
        if event.button != 1:
            return
        
        zoom_end = event.xdata
        if zoom_end is None:
            self._zoom_start = None
            return
        
        # Only zoom if drag was significant (> 1% of current range)
        ax = self._ax1
        xmin, xmax = ax.get_xlim()
        drag_distance = abs(zoom_end - self._zoom_start)
        if drag_distance < (xmax - xmin) * 0.01:
            self._zoom_start = None
            return
        
        # Apply zoom to selected region
        new_xmin = min(self._zoom_start, zoom_end)
        new_xmax = max(self._zoom_start, zoom_end)
        
        for ax in (self._ax1, self._ax2, self._ax3):
            ax.set_xlim(new_xmin, new_xmax)
        
        self._zoom_start = None
        self._canvas.draw_idle()


# Fix missing import
from matplotlib import dates as mdates
