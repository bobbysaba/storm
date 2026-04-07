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

_LEGEND_QSS = (
    f"background-color: transparent; "
    f"font-size: 10px; font-family: monospace; font-weight: 600;"
)


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
        self._zoom_start = None
        self._zoom_rect_patches = []

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

        # ── QLabel overlays for cursor readout (children of canvas) ───────────
        # These float over the canvas at positions computed from axes bounds.
        # Using QLabels instead of fig.text avoids a full canvas redraw on
        # every mouse move — only the vlines need draw_idle().
        self._leg_temp_label = QLabel(self._canvas)
        self._leg_temp_label.setStyleSheet(_LEGEND_QSS)
        self._leg_temp_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._leg_wind_label = QLabel(self._canvas)
        self._leg_wind_label.setStyleSheet(_LEGEND_QSS)
        self._leg_wind_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._leg_pres_label = QLabel(self._canvas)
        self._leg_pres_label.setStyleSheet(_LEGEND_QSS)
        self._leg_pres_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._legend_labels = [self._leg_temp_label, self._leg_wind_label, self._leg_pres_label]
        for lbl in self._legend_labels:
            lbl.hide()

        # Reposition labels when canvas resizes
        self._canvas.mpl_connect("resize_event", lambda _: self._position_legend_labels())

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

        # Build plot arrays from raw observation timestamps.
        # Insert a NaN gap-breaker whenever two consecutive observations are
        # more than _GAP_THRESHOLD_S apart so matplotlib doesn't draw a
        # misleading line across a data dropout.
        _GAP_THRESHOLD_S = 30  # 3× the expected 10-second publish interval

        times_list = []
        temp_list  = []
        dewp_list  = []
        wspd_list  = []
        wdir_list  = []
        pres_list  = []
        # Map from plot-array index → original Observation for cursor readout
        self._idx_to_obs = {}

        prev_ts = None
        for o in obs_list:
            # If gap exceeds threshold, insert a NaN row to break the line
            if prev_ts is not None:
                gap = (o.timestamp - prev_ts).total_seconds()
                if gap > _GAP_THRESHOLD_S:
                    from datetime import timedelta
                    times_list.append(prev_ts + timedelta(seconds=1))
                    temp_list.append(np.nan)
                    dewp_list.append(np.nan)
                    wspd_list.append(np.nan)
                    wdir_list.append(np.nan)
                    pres_list.append(np.nan)

            idx = len(times_list)
            self._idx_to_obs[idx] = o
            times_list.append(o.timestamp)
            temp_list.append(o.temperature_c * 9/5 + 32 if o.temperature_c is not None else np.nan)
            dewp_list.append(o.dewpoint_c * 9/5 + 32 if o.dewpoint_c is not None else np.nan)
            wspd_list.append(o.wind_speed_ms * 1.94384 if o.wind_speed_ms is not None else np.nan)
            wdir_list.append(o.wind_dir_deg if o.wind_dir_deg is not None else np.nan)
            pres_list.append(o.pressure_mb if o.pressure_mb is not None else np.nan)
            prev_ts = o.timestamp

        times    = np.array(times_list)
        temp_f   = np.array(temp_list)
        dewp_f   = np.array(dewp_list)
        wspd_kt  = np.array(wspd_list)
        wdir_deg = np.array(wdir_list)
        pres_mb  = np.array(pres_list)
        self._plot_times = times  # Store for cursor interaction

        self._fig.clear()
        self._cursor_vlines = []

        # Three panels with extra hspace for QLabel readouts between them
        from matplotlib.gridspec import GridSpec
        gs = GridSpec(3, 1, figure=self._fig, hspace=0.35, left=0.08, right=0.92, top=0.96, bottom=0.12)

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
        ax1.plot(times, temp_f, color=_TEMP_CLR, linewidth=1.2,
                 marker='.', markersize=3)
        ax1.plot(times, dewp_f, color=_DEWP_CLR, linewidth=1.2,
                 marker='.', markersize=3)
        ax1.set_ylabel('°F', color=_MUTED, fontsize=9)
        ax1.tick_params(labelbottom=False)
        ax1.set_xlim(times[0], times[-1])

        # ── Panel 2: Wind Speed / Direction (dual y-axes) ─────────────────────
        ax2_right = ax2.twinx()
        ax2.plot(times, wspd_kt, color=_WSPD_CLR, linewidth=1.2,
                 marker='.', markersize=3)
        ax2.set_ylabel('kt', color=_MUTED, fontsize=9)
        ax2_right.scatter(times, wdir_deg, color=_WDIR_CLR, s=6, alpha=0.7)
        ax2_right.set_ylabel('deg', color=_MUTED, fontsize=9)
        ax2_right.set_ylim(0, 360)
        ax2_right.set_yticks([0, 90, 180, 270, 360])
        ax2_right.tick_params(colors=_MUTED, labelsize=8)
        for sp in ax2_right.spines.values():
            sp.set_edgecolor(_BORDER)
        ax2.tick_params(labelbottom=False)
        ax2.set_xlim(times[0], times[-1])

        # ── Panel 3: Pressure ──────────────────────────────────────────────────
        ax3.plot(times, pres_mb, color=_PRES_CLR, linewidth=1.2,
                 marker='.', markersize=3)
        ax3.set_ylabel('mb', color=_MUTED, fontsize=9)
        ax3.set_xlabel("Time (UTC)", color=_MUTED, fontsize=8)
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

        # Set default legend text and position QLabel overlays
        self._set_legend_defaults()
        # Defer positioning until after the canvas has actually rendered
        QTimer.singleShot(0, self._position_legend_labels)

    # ── QLabel legend positioning ─────────────────────────────────────────────

    def _position_legend_labels(self):
        """Move QLabel overlays to pixel positions centered between axes."""
        if not hasattr(self, '_ax1'):
            return

        # device_pixel_ratio is a property in newer matplotlib, not a method
        dpi_ratio = getattr(self._canvas, 'device_pixel_ratio', lambda: 1.0)
        if callable(dpi_ratio):
            dpi_ratio = dpi_ratio()
        canvas_h = self._canvas.height()

        # Position labels in the gap between axes, centered horizontally
        axes_pairs = [
            (self._ax1, self._ax2, self._leg_temp_label),
            (self._ax2, self._ax3, self._leg_wind_label),
        ]
        
        for ax_above, ax_below, lbl in axes_pairs:
            bbox_above = ax_above.get_position()
            bbox_below = ax_below.get_position()
            
            # Center horizontally within the axes width
            px_x = int(bbox_above.x0 * self._canvas.width())
            label_width = int((bbox_above.x1 - bbox_above.x0) * self._canvas.width())
            
            # Position vertically in the middle of the gap between axes
            # Figure y=0 is bottom, QWidget y=0 is top — so flip
            gap_top = canvas_h - bbox_above.y0 * canvas_h
            gap_bottom = canvas_h - bbox_below.y1 * canvas_h
            px_y = int((gap_top + gap_bottom) / 2 - 8)  # -8 to vertically center text
            
            lbl.move(px_x, px_y)
            lbl.resize(label_width, 18)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.show()
        
        # Position the pressure label in the bottom margin area (below x-axis label)
        bbox = self._ax3.get_position()
        px_x = int(bbox.x0 * self._canvas.width())
        label_width = int((bbox.x1 - bbox.x0) * self._canvas.width())
        # Place it near the bottom of the canvas to avoid x-axis label
        px_y = int(canvas_h - 22)  # 22px from bottom
        
        self._leg_pres_label.move(px_x, px_y)
        self._leg_pres_label.resize(label_width, 18)
        self._leg_pres_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._leg_pres_label.show()

    def _set_legend_defaults(self):
        """Set the static default text on all legend labels with color coding."""
        self._leg_temp_label.setText(
            f'<span style="color: {_TEMP_CLR};">━ T  --</span>    '
            f'<span style="color: {_DEWP_CLR};">━ Td  --</span>'
        )
        self._leg_wind_label.setText(
            f'<span style="color: {_WSPD_CLR};">━ Speed  --</span>    '
            f'<span style="color: {_WDIR_CLR};">● Dir  --</span>'
        )
        self._leg_pres_label.setText(
            f'<span style="color: {_PRES_CLR};">━ Pressure  --</span>'
        )

    # ── Interactive cursor ────────────────────────────────────────────────────

    def _on_mouse_move(self, event):
        if not hasattr(self, '_plot_times') or not hasattr(self, '_idx_to_obs'):
            return

        # ── If dragging, update the selection rectangle instead of cursor ────
        if self._zoom_start is not None and event.xdata is not None:
            self._update_zoom_rect(event.xdata)
            return

        # Check if mouse is in any of the three main axes or the right y-axis
        if event.inaxes not in (self._ax1, self._ax2, self._ax2_right, self._ax3):
            return

        # Snap to nearest grid point
        from matplotlib import dates as mdates
        mouse_time = mdates.num2date(event.xdata)

        # Find nearest grid index
        time_diffs = [abs((t - mouse_time).total_seconds()) for t in self._plot_times]
        grid_idx = np.argmin(time_diffs)

        # Only show readout if there's an actual observation at this grid point
        if grid_idx not in self._idx_to_obs:
            self._clear_cursor()
            return

        obs = self._idx_to_obs[grid_idx]
        snap_time = self._plot_times[grid_idx]

        # Format values
        temp_val = f"{obs.temperature_c * 9/5 + 32:.0f}°F" if obs.temperature_c is not None else "--"
        dewp_val = f"{obs.dewpoint_c * 9/5 + 32:.0f}°F" if obs.dewpoint_c is not None else "--"
        wspd_val = f"{obs.wind_speed_ms * 1.94384:.0f} kt" if obs.wind_speed_ms is not None else "--"
        wdir_val = f"{obs.wind_dir_deg:.0f}°" if obs.wind_dir_deg is not None else "--"
        pres_val = f"{obs.pressure_mb:.1f} mb" if obs.pressure_mb is not None else "--"
        ts_str = obs.timestamp.strftime('%H:%M:%S UTC')

        # Update QLabel text (instant, no canvas redraw needed) with color coding
        self._leg_temp_label.setText(
            f'<span style="color: {_MUTED};">{ts_str}</span>  '
            f'<span style="color: {_TEMP_CLR};">━ T {temp_val}</span>    '
            f'<span style="color: {_DEWP_CLR};">━ Td {dewp_val}</span>'
        )
        self._leg_wind_label.setText(
            f'<span style="color: {_MUTED};">{ts_str}</span>  '
            f'<span style="color: {_WSPD_CLR};">━ Speed {wspd_val}</span>    '
            f'<span style="color: {_WDIR_CLR};">● Dir {wdir_val}</span>'
        )
        self._leg_pres_label.setText(
            f'<span style="color: {_MUTED};">{ts_str}</span>  '
            f'<span style="color: {_PRES_CLR};">━ Pressure {pres_val}</span>'
        )

        # Draw vertical line at snapped grid time
        for vline in self._cursor_vlines:
            vline.remove()
        self._cursor_vlines.clear()

        for ax in (self._ax1, self._ax2, self._ax3):
            vline = ax.axvline(snap_time, color=_CURSOR_CLR, linewidth=0.9, alpha=0.4, zorder=10)
            self._cursor_vlines.append(vline)

        self._canvas.draw_idle()

    def _clear_cursor(self):
        """Reset cursor vlines and legend labels to defaults."""
        for vline in self._cursor_vlines:
            vline.remove()
        self._cursor_vlines.clear()
        self._set_legend_defaults()
        self._canvas.draw_idle()

    def _on_axes_leave(self, event):
        if self._zoom_start is not None:
            return  # Don't clear cursor during drag
        self._clear_cursor()

    # ── Zoom/Pan interaction ──────────────────────────────────────────────────

    def _on_scroll(self, event):
        """Zoom in/out with scroll wheel centered on cursor position."""
        if event.inaxes not in (self._ax1, self._ax2, self._ax2_right, self._ax3):
            return
        if not hasattr(self, '_plot_times'):
            return

        # Get current x-axis limits
        xmin, xmax = self._ax1.get_xlim()

        # Zoom factor scaled by scroll step size (trackpad-friendly)
        step = getattr(event, 'step', 1)
        zoom_factor = 0.8 ** step  # scroll up (positive step) = zoom in

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
        """Start click-drag zoom or double-click to reset."""
        if event.inaxes not in (self._ax1, self._ax2, self._ax2_right, self._ax3):
            return

        # Double-click: reset zoom to full extent
        if event.dblclick:
            if hasattr(self, '_plot_times') and len(self._plot_times) > 0:
                from matplotlib import dates as mdates
                data_min = mdates.date2num(self._plot_times[0])
                data_max = mdates.date2num(self._plot_times[-1])
                for ax in (self._ax1, self._ax2, self._ax3):
                    ax.set_xlim(data_min, data_max)
                self._canvas.draw_idle()
            return

        if event.button != 1:  # Only left mouse button for drag-zoom
            return

        self._zoom_start = event.xdata

    def _on_button_release(self, event):
        """Complete click-drag zoom selection."""
        if self._zoom_start is None:
            return
        if event.button != 1:
            return

        # Clean up selection rectangle
        self._clear_zoom_rect()

        zoom_end = event.xdata
        if zoom_end is None or event.inaxes not in (
            self._ax1, self._ax2, self._ax2_right, self._ax3
        ):
            self._zoom_start = None
            return

        # Only zoom if drag was significant (> 1% of current range)
        xmin, xmax = self._ax1.get_xlim()
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

    def _update_zoom_rect(self, current_x):
        """Draw/update a selection highlight rectangle during drag-zoom."""
        self._clear_zoom_rect()
        self._zoom_rect_patches = []
        xmin = min(self._zoom_start, current_x)
        xmax = max(self._zoom_start, current_x)
        for ax in (self._ax1, self._ax2, self._ax3):
            rect = ax.axvspan(xmin, xmax, color=_CURSOR_CLR, alpha=0.12, zorder=5)
            self._zoom_rect_patches.append(rect)
        self._canvas.draw_idle()

    def _clear_zoom_rect(self):
        """Remove any active selection highlight rectangles."""
        for patch in getattr(self, '_zoom_rect_patches', []):
            patch.remove()
        self._zoom_rect_patches = []
