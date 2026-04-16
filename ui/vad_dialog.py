# ui/vad_dialog.py
# VAD Wind Profile dialog with hodograph display and time scrubber.

import logging
import numpy as np
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QWidget, QGridLayout, QSizePolicy, QSlider, QFrame,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette, QColor

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from config import ACCENT_COLOR as _STORM_CYAN
from core.vad import VADProfile, VADSet
from data.vad_fetcher import fetch_recent_vads

log = logging.getLogger(__name__)

# ── Palette (matches sounding_dialog) ────────────────────────────────────────
_FIG_BG  = "#0a0a0f"
_AX_BG   = "#0f0f1a"
_BORDER  = "#2a2a40"
_ACCENT  = "#ff6b35"
_TEXT    = "#e8eaf0"
_MUTED   = "#666688"
_HDR_CLR = "#b8b8d8"

_HODO_LAYERS = [
    ("#ff6b6b", 0,  3),
    ("#ffd700", 3,  6),
    ("#4fc3f7", 6,  9),
    ("#888899", 9, 99),
]

_RM_CLR = "#ff6b6b"

_THRESHOLDS: dict[str, list] = {
    "srh01":   [(0, _MUTED), (100, "#f9ca24"), (200, "#f0932b"), (400, "#eb4d4b")],
    "srh03":   [(0, _MUTED), (150, "#f9ca24"), (300, "#f0932b"), (500, "#eb4d4b")],
    "shear01": [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "shear03": [(0, _MUTED), (20,  "#f9ca24"), (30,  "#f0932b"), (45,  "#eb4d4b")],
    "bwd06":   [(0, _MUTED), (30,  "#f9ca24"), (40,  "#f0932b"), (50,  "#eb4d4b")],
    "srw01":   [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "srw03":   [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "srw06":   [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
}

_SLIDER_QSS = f"""
QSlider {{ background-color: transparent; }}
QSlider::groove:horizontal {{
    background-color: #1a1a2e; height: 4px; border-radius: 2px; margin: 0;
}}
QSlider::sub-page:horizontal {{
    background-color: {_STORM_CYAN}; height: 4px; border-radius: 2px; margin: 0;
}}
QSlider::handle:horizontal {{
    background-color: {_STORM_CYAN}; border: 2px solid {_FIG_BG};
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
"""


def _threshold_color(key: str, value) -> str | None:
    thresholds = _THRESHOLDS.get(key)
    if not thresholds or value is None:
        return None
    color = thresholds[0][1]
    for threshold, c in thresholds:
        if value >= threshold:
            color = c
    return color


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


def _lbl(text, color=_TEXT, size=10, bold=False,
         align=Qt.AlignmentFlag.AlignLeft):
    w = QLabel(text)
    weight = "bold" if bold else "normal"
    w.setStyleSheet(
        f"background-color: transparent; color: {color}; "
        f"font-size: {size}px; font-weight: {weight};"
    )
    w.setAlignment(align)
    return w


class VADDialog(QDialog):
    """Dialog displaying VAD wind profile hodograph with time scrubber."""

    def __init__(self, site: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.site = site
        self.vad_set: Optional[VADSet] = None
        self._cur_idx = 0
        self._tick_labels: list[QLabel] = []
        self._param_labels: dict[str, QLabel] = {}
        self._hodo_ax = None
        self._current_profile: Optional[VADProfile] = None
        self._cursor_dot = None

        self.setWindowTitle(f"VAD Wind Profile — {site}")
        self.setMinimumSize(640, 660)
        self.resize(700, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        _force_bg(self)
        self.setStyleSheet(
            f"QDialog {{ background-color: {_FIG_BG}; }}"
            f"QLabel  {{ background-color: transparent; color: {_TEXT}; }}"
            f"QFrame  {{ background-color: {_BORDER}; border: none; }}"
            + _SLIDER_QSS
        )

        self._build_ui()

        # Fetch VAD data in background
        QTimer.singleShot(100, self._fetch_data)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        _A = Qt.AlignmentFlag.AlignCenter

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(3)

        # ── Header ──────────────────────────────────────���────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._header_line1 = _lbl(f"VAD  ·  {self.site}", color=_TEXT, size=11, bold=True)
        self._header_line2 = _lbl("Loading...", color=_MUTED, size=10)
        text_col.addWidget(self._header_line1)
        text_col.addWidget(self._header_line2)
        header_row.addLayout(text_col, stretch=1)

        self._scrubber_widget = QWidget()
        self._scrubber_widget.setAutoFillBackground(False)
        scrubber_col = QVBoxLayout(self._scrubber_widget)
        scrubber_col.setSpacing(1)
        scrubber_col.setContentsMargins(0, 0, 0, 0)

        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.setMinimum(0)
        self._scrubber.setMaximum(0)
        self._scrubber.setValue(0)
        self._scrubber.setTickPosition(QSlider.TickPosition.NoTicks)
        self._scrubber.setSingleStep(1)
        self._scrubber.setPageStep(1)
        self._scrubber.setFixedWidth(185)
        self._scrubber.setFixedHeight(16)
        _force_bg(self._scrubber)
        self._scrubber.valueChanged.connect(self._on_scrubber_changed)
        scrubber_col.addWidget(self._scrubber, alignment=Qt.AlignmentFlag.AlignRight)

        self._tick_row = QHBoxLayout()
        self._tick_row.setContentsMargins(0, 0, 0, 0)
        self._tick_row.setSpacing(0)
        scrubber_col.addLayout(self._tick_row)

        header_row.addWidget(self._scrubber_widget)
        root.addLayout(header_row)

        # ── Matplotlib canvas ─────────────────────────────────────────────────
        self._fig = Figure(facecolor=_FIG_BG)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setAutoFillBackground(False)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._canvas.mpl_connect("axes_leave_event",    self._on_axes_leave)
        root.addWidget(self._canvas, stretch=1)

        # ── Cursor readout ────────────────────────────────────────────────────
        self._cursor_label = _lbl("", color=_MUTED, size=10)
        self._cursor_label.setFixedHeight(15)
        root.addWidget(self._cursor_label)

        # ── Separator ─────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # ── Bottom panel: kinematics ──────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.setContentsMargins(4, 2, 4, 0)

        kin_w = QWidget()
        kin_w.setAutoFillBackground(False)
        kg = QGridLayout(kin_w)
        kg.setContentsMargins(0, 0, 0, 0)
        kg.setHorizontalSpacing(10)
        kg.setVerticalSpacing(1)

        kg.addWidget(_lbl("", color=_HDR_CLR, size=9, bold=True), 0, 0)
        for c, hdr in enumerate(["Shear", "SRH", "GRW", "SRW"]):
            kg.addWidget(_lbl(hdr, color=_HDR_CLR, size=9, bold=True, align=_A), 0, c + 1)

        _kin_rows = [
            ("0-1 km", "shear01", "srh01", "grw01", "srw01"),
            ("0-3 km", "shear03", "srh03", "grw03", "srw03"),
            ("0-6 km", "bwd06",   None,    "grw06", "srw06"),
        ]
        for r, (row_lbl, *keys) in enumerate(_kin_rows):
            kg.addWidget(_lbl(row_lbl, color=_HDR_CLR, size=10, bold=True), r + 1, 0)
            for c, key in enumerate(keys):
                if key is None:
                    kg.addWidget(_lbl("—", color=_MUTED, size=12, bold=True, align=_A),
                                 r + 1, c + 1)
                else:
                    vl = _lbl("—", color=_MUTED, size=12, bold=True, align=_A)
                    kg.addWidget(vl, r + 1, c + 1)
                    self._param_labels[key] = vl

        bottom.addWidget(kin_w, stretch=3)

        # Vertical divider
        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.Shape.VLine)
        vdiv.setFixedWidth(1)
        bottom.addWidget(vdiv)

        # Storm motion panel
        sm_w = QWidget()
        sm_w.setAutoFillBackground(False)
        sg = QGridLayout(sm_w)
        sg.setContentsMargins(0, 0, 0, 0)
        sg.setHorizontalSpacing(10)
        sg.setVerticalSpacing(1)

        sg.addWidget(_lbl("Bunkers RM", color=_HDR_CLR, size=9, bold=True), 0, 0)
        vl = _lbl("—", color=_MUTED, size=12, bold=True, align=_A)
        sg.addWidget(vl, 1, 0)
        self._param_labels["bunkers"] = vl

        bottom.addWidget(sm_w, stretch=1)

        root.addLayout(bottom)

    # ── Scrubber ─────────────────────────────────────────────────────────────

    def _rebuild_scrubber(self):
        while self._tick_row.count():
            item = self._tick_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tick_labels.clear()

        if not self.vad_set or len(self.vad_set) == 0:
            return

        count = len(self.vad_set)
        self._scrubber_widget.setVisible(count > 1)
        self._scrubber.blockSignals(True)
        self._scrubber.setMaximum(count - 1)
        self._scrubber.setValue(self._cur_idx)
        self._scrubber.blockSignals(False)

        # With many profiles (~12), showing every label is too crowded.
        # Show only first, last, and evenly-spaced intermediate labels
        # (roughly every 3rd) to keep the row readable.
        if count <= 5:
            visible = set(range(count))
        else:
            step = max(2, count // 4)
            visible = {0, count - 1}
            i = step
            while i < count - 1:
                visible.add(i)
                i += step

        self._tick_row.addStretch(1)
        for i, profile in enumerate(self.vad_set.profiles):
            f_str = profile.timestamp.strftime("%H%MZ")
            lbl = _lbl(f_str, color=_MUTED, size=8,
                       align=Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setFixedWidth(36)
            lbl.setVisible(i in visible)
            self._tick_labels.append(lbl)
            self._tick_row.addWidget(lbl)
            if i < count - 1:
                self._tick_row.addStretch(2)
        self._tick_row.addStretch(1)

        self._update_header()

    def _on_scrubber_changed(self, idx: int):
        self._cur_idx = idx
        self._update_header()
        self._draw()

    def _update_header(self):
        if not self.vad_set or len(self.vad_set) == 0:
            return
        profile = self.vad_set[self._cur_idx]
        valid_str = profile.timestamp.strftime("%H%MZ %d %b %Y")
        self._header_line1.setText(f"VAD  ·  {self.site}")
        self._header_line2.setText(f"Valid {valid_str}")

        # Determine which fixed labels to show — always include the active one
        count = len(self.vad_set)
        if count <= 5:
            visible = set(range(count))
        else:
            step = max(2, count // 4)
            visible = {0, count - 1, self._cur_idx}
            i = step
            while i < count - 1:
                visible.add(i)
                i += step

        for i, lbl in enumerate(self._tick_labels):
            show = i in visible
            lbl.setVisible(show)
            active = (i == self._cur_idx)
            lbl.setStyleSheet(
                "background-color: transparent; font-size: 8px; "
                + (f"color: {_STORM_CYAN}; font-weight: bold;" if active
                   else f"color: {_MUTED};")
            )

    # ── Data fetch ───────────────────────────────────────────────────────────

    def _fetch_data(self):
        """Fetch VAD data from THREDDS."""
        try:
            self.vad_set = fetch_recent_vads(self.site, count=12)

            if len(self.vad_set) == 0:
                self._header_line2.setText("VAD data not available via THREDDS")
                self._header_line2.setStyleSheet(
                    f"background-color: transparent; color: #FFD166; "
                    f"font-size: 10px; font-weight: 600;"
                )
                return

            self._cur_idx = 0
            self._rebuild_scrubber()
            self._draw()

        except Exception as e:
            log.error(f"Failed to fetch VAD data: {e}", exc_info=True)
            self._header_line2.setText(f"Error: {e}")
            self._header_line2.setStyleSheet(
                f"background-color: transparent; color: #E53935; "
                f"font-size: 10px; font-weight: 500;"
            )

    # ── Drawing ──────────────────────────────────────────────────────────────

    def _draw(self):
        if not self.vad_set or len(self.vad_set) == 0:
            return
        profile = self.vad_set[min(self._cur_idx, len(self.vad_set) - 1)]
        self._current_profile = profile

        self._fig.clear()
        self._cursor_dot = None
        self._hodo_ax = None

        self._draw_hodograph(profile)
        self._update_params(profile)
        self._canvas.draw_idle()

    def _draw_hodograph(self, profile: VADProfile):
        ax = self._fig.add_subplot(111)
        self._hodo_ax = ax
        ax.set_facecolor(_AX_BG)

        u = profile.u_component()
        v = profile.v_component()
        heights_km = profile.heights_m / 1000.0

        # Plot hodograph in layer-colored segments
        for color, z_lo, z_hi in _HODO_LAYERS:
            mask = (heights_km >= z_lo) & (heights_km <= z_hi)
            if mask.sum() < 2:
                continue
            # Extend mask by one point on each side for continuity
            idxs = np.where(mask)[0]
            lo = max(0, idxs[0] - 1)
            hi = min(len(heights_km) - 1, idxs[-1] + 1)
            seg = slice(lo, hi + 1)
            ax.plot(u[seg], v[seg], color=color, linewidth=2.2,
                    solid_capstyle="round")

        # Height markers at key levels
        for height_km in [1, 3, 6, 9]:
            height_m = height_km * 1000
            if height_m <= profile.heights_m.max():
                idx = np.argmin(np.abs(profile.heights_m - height_m))
                ax.plot(u[idx], v[idx], 'o', color=_TEXT, markersize=5, zorder=5)
                ax.annotate(f"{height_km}",
                            (u[idx], v[idx]),
                            textcoords="offset points", xytext=(5, 5),
                            color=_TEXT, fontsize=9, fontweight="bold",
                            zorder=6)

        # Bunkers storm motion
        params = self._calculate_parameters(profile)
        if params["bunkers_spd"] > 0:
            u_rm = -params["bunkers_spd"] * np.sin(np.deg2rad(params["bunkers_dir"]))
            v_rm = -params["bunkers_spd"] * np.cos(np.deg2rad(params["bunkers_dir"]))
            ax.plot(u_rm, v_rm, 's', color=_RM_CLR, markersize=7,
                    markeredgecolor="white", markeredgewidth=0.8, zorder=7,
                    label="RM")

        # Origin
        ax.plot(0, 0, '+', color=_MUTED, markersize=12, markeredgewidth=1.5)

        # Range rings
        import matplotlib.pyplot as plt
        for ring_kt in [20, 40, 60, 80, 100]:
            circle = plt.Circle((0, 0), ring_kt, fill=False,
                                color="#556080", linewidth=0.9, linestyle="--")
            ax.add_patch(circle)
            ax.text(ring_kt * 0.707, ring_kt * 0.707, f"{ring_kt}",
                    color="#aaaacc", fontsize=7, ha="left", va="bottom")

        # Cursor dot (hidden until mouse hover)
        self._cursor_dot, = ax.plot([], [], 'o', color=_ACCENT, markersize=8,
                                    zorder=10, alpha=0)

        # Axis styling
        max_wind = max(60, np.max(np.sqrt(u**2 + v**2)) + 15)
        ax.set_xlim(-max_wind, max_wind)
        ax.set_ylim(-max_wind, max_wind)
        ax.set_aspect("equal")
        ax.spines["left"].set_color(_BORDER)
        ax.spines["bottom"].set_color(_BORDER)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(colors=_MUTED, labelsize=8)
        ax.grid(True, color=_BORDER, linewidth=0.4, alpha=0.5)
        ax.set_xlabel("U (kt)", color=_MUTED, fontsize=9)
        ax.set_ylabel("V (kt)", color=_MUTED, fontsize=9)

        self._fig.subplots_adjust(left=0.10, right=0.96, top=0.96, bottom=0.08)

    # ── Interactive cursor ───────────────────────────────────────────────────

    def _on_mouse_move(self, event):
        if event.inaxes is not self._hodo_ax or self._current_profile is None:
            return
        if event.xdata is None or event.ydata is None:
            return

        profile = self._current_profile
        u = profile.u_component()
        v = profile.v_component()

        # Find nearest data point to mouse position
        dist = (u - event.xdata)**2 + (v - event.ydata)**2
        idx = int(np.argmin(dist))

        h_m = profile.heights_m[idx]
        h_kft = h_m / (1000.0 * 0.3048)
        wd = profile.wind_dir[idx]
        ws = profile.wind_spd[idx]

        self._cursor_label.setText(
            f"{h_m:.0f} m ({h_kft:.1f} kft)  ·  "
            f"Wind {wd:.0f}\u00b0 @ {ws:.0f} kt  ·  "
            f"U {u[idx]:+.0f}  V {v[idx]:+.0f}"
        )

        if self._cursor_dot is not None:
            self._cursor_dot.set_data([u[idx]], [v[idx]])
            self._cursor_dot.set_alpha(0.9)
            self._canvas.draw_idle()

    def _on_axes_leave(self, event):
        self._cursor_label.setText("")
        if self._cursor_dot is not None:
            self._cursor_dot.set_alpha(0)
            self._canvas.draw_idle()

    # ── Parameters ───────────────────────────────────────────────────────────

    def _update_params(self, profile: VADProfile):
        params = self._calculate_parameters(profile)

        _map = {
            "shear01": (params["shear_0_1"], "{:.0f} kt"),
            "shear03": (params["shear_0_3"], "{:.0f} kt"),
            "bwd06":   (params["bwd_0_6"],   "{:.0f} kt"),
            "srh01":   (params["srh_0_1"],   "{:.0f}"),
            "srh03":   (params["srh_0_3"],   "{:.0f}"),
            "grw01":   (params["grw_0_1"],   "{:.0f} kt"),
            "grw03":   (params["grw_0_3"],   "{:.0f} kt"),
            "grw06":   (params["grw_0_6"],   "{:.0f} kt"),
            "srw01":   (params["srw_0_1"],   "{:.0f} kt"),
            "srw03":   (params["srw_0_3"],   "{:.0f} kt"),
            "srw06":   (params["srw_0_6"],   "{:.0f} kt"),
            "bunkers": (None, ""),
        }

        for key, (val, fmt) in _map.items():
            lbl = self._param_labels.get(key)
            if not lbl:
                continue
            if key == "bunkers":
                txt = f"{params['bunkers_dir']:.0f}\u00b0 / {params['bunkers_spd']:.0f} kt"
                lbl.setText(txt)
                lbl.setStyleSheet(
                    f"background-color: transparent; color: {_TEXT}; "
                    f"font-size: 12px; font-weight: bold;"
                )
                continue

            lbl.setText(fmt.format(val))
            tc = _threshold_color(key, val)
            c = tc if tc else _MUTED
            lbl.setStyleSheet(
                f"background-color: transparent; color: {c}; "
                f"font-size: 12px; font-weight: bold;"
            )

    def _calculate_parameters(self, profile: VADProfile) -> dict:
        """Calculate hodograph-derived parameters."""
        def get_wind_at_height(height_m):
            dir_val, spd_val = profile.interpolate_to_height(height_m)
            if np.isnan(dir_val) or np.isnan(spd_val):
                return 0.0, 0.0
            u = -spd_val * np.sin(np.deg2rad(dir_val))
            v = -spd_val * np.cos(np.deg2rad(dir_val))
            return u, v

        sfc_h = profile.heights_m.min()
        u_sfc, v_sfc = get_wind_at_height(sfc_h)
        u_1km, v_1km = get_wind_at_height(sfc_h + 1000)
        u_3km, v_3km = get_wind_at_height(sfc_h + 3000)
        u_6km, v_6km = get_wind_at_height(sfc_h + 6000)

        bwd_0_6 = np.sqrt((u_6km - u_sfc)**2 + (v_6km - v_sfc)**2)
        shear_0_1 = np.sqrt((u_1km - u_sfc)**2 + (v_1km - v_sfc)**2)
        shear_0_3 = np.sqrt((u_3km - u_sfc)**2 + (v_3km - v_sfc)**2)

        # Bunkers right-mover
        u_mean = (u_sfc + u_6km) / 2
        v_mean = (v_sfc + v_6km) / 2
        shear_u = u_6km - u_sfc
        shear_v = v_6km - v_sfc
        deviation = 7.5 * 1.94384  # m/s → kt
        shear_mag = np.sqrt(shear_u**2 + shear_v**2)
        if shear_mag > 0:
            u_rm = u_mean + deviation * (-shear_v / shear_mag)
            v_rm = v_mean + deviation * (shear_u / shear_mag)
        else:
            u_rm, v_rm = u_mean, v_mean

        bunkers_spd = np.sqrt(u_rm**2 + v_rm**2)
        bunkers_dir = (np.degrees(np.arctan2(-u_rm, -v_rm)) + 360) % 360

        # Storm-relative helicity
        srh_0_1 = self._calculate_srh(profile, sfc_h, sfc_h + 1000, u_rm, v_rm)
        srh_0_3 = self._calculate_srh(profile, sfc_h, sfc_h + 3000, u_rm, v_rm)

        # Layer-averaged GRW and SRW (magnitude of mean wind vector in layer)
        def _layer_avg_wind(z_bot, z_top):
            """Return (grw, srw) as layer-averaged magnitudes in kt."""
            mask = (profile.heights_m >= z_bot) & (profile.heights_m <= z_top)
            if mask.sum() < 2:
                return 0.0, 0.0
            u_layer = profile.u_component()[mask]
            v_layer = profile.v_component()[mask]
            u_mean_l = np.mean(u_layer)
            v_mean_l = np.mean(v_layer)
            grw = np.sqrt(u_mean_l**2 + v_mean_l**2)
            srw = np.sqrt((u_mean_l - u_rm)**2 + (v_mean_l - v_rm)**2)
            return float(grw), float(srw)

        grw_0_1, srw_0_1 = _layer_avg_wind(sfc_h, sfc_h + 1000)
        grw_0_3, srw_0_3 = _layer_avg_wind(sfc_h, sfc_h + 3000)
        grw_0_6, srw_0_6 = _layer_avg_wind(sfc_h, sfc_h + 6000)

        return {
            "bwd_0_6": bwd_0_6,
            "shear_0_1": shear_0_1,
            "shear_0_3": shear_0_3,
            "bunkers_dir": bunkers_dir,
            "bunkers_spd": bunkers_spd,
            "srh_0_1": srh_0_1,
            "srh_0_3": srh_0_3,
            "grw_0_1": grw_0_1,
            "grw_0_3": grw_0_3,
            "grw_0_6": grw_0_6,
            "srw_0_1": srw_0_1,
            "srw_0_3": srw_0_3,
            "srw_0_6": srw_0_6,
        }

    def _calculate_srh(self, profile: VADProfile, z_bottom: float, z_top: float,
                       u_storm: float, v_storm: float) -> float:
        """Calculate storm-relative helicity over a layer."""
        mask = (profile.heights_m >= z_bottom) & (profile.heights_m <= z_top)
        if mask.sum() < 2:
            return 0.0

        u = profile.u_component()[mask]
        v = profile.v_component()[mask]

        u_sr = u - u_storm
        v_sr = v - v_storm

        srh = 0.0
        for i in range(len(u) - 1):
            du = u[i + 1] - u[i]
            dv = v[i + 1] - v[i]
            srh += (u_sr[i] * dv - v_sr[i] * du)

        return srh
