# ui/sounding_dialog.py
# Floating Skew-T log-P dialog for HRRR point soundings.

import logging

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.transforms import blended_transform_factory
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QWidget, QGridLayout, QSizePolicy, QSlider, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

import metpy.calc as mpcalc
from metpy.plots import SkewT, Hodograph
from metpy.units import units

from config import ACCENT_COLOR as _STORM_CYAN
from core.sounding import Sounding, SoundingSet

log = logging.getLogger(__name__)

# ── Palette ──────────────────────────────────────────────────────────────────
_FIG_BG    = "#0a0a0f"
_AX_BG     = "#0f0f1a"
_BORDER    = "#2a2a40"
_ACCENT    = "#ff6b35"
_TEXT      = "#e8eaf0"
_MUTED     = "#666688"
_HDR_CLR   = "#b8b8d8"   # bright table row/column headers

_TEMP_CLR   = "#ff6b6b"
_DEWP_CLR   = "#3ddc84"
_BARB_CLR   = "#aaaacc"
_PARCEL_CLR = "#ffffff"
_VTEMP_CLR  = "#ffaa88"
_EIL_CLR    = "#00e676"
_RM_CLR     = "#ff6b6b"
_LM_CLR     = "#4fc3f7"

_HODO_LAYERS = [
    ("#ff6b6b", 0,  3),
    ("#ffd700", 3,  6),
    ("#4fc3f7", 6,  9),
    ("#888899", 9, 99),
]

# ── Threshold coloring ───────────────────────────────────────────────────────
_THRESHOLDS: dict[str, list] = {
    "sbcape":    [(0, _MUTED), (500, "#f9ca24"), (1500, "#f0932b"), (3000, "#eb4d4b")],
    "mlcape":    [(0, _MUTED), (500, "#f9ca24"), (1500, "#f0932b"), (3000, "#eb4d4b")],
    "mucape":    [(0, _MUTED), (500, "#f9ca24"), (1500, "#f0932b"), (3000, "#eb4d4b")],
    "sbcin":     [(-250, "#eb4d4b"), (-150, "#f0932b"), (-75, "#f9ca24"), (-25, _MUTED)],
    "mlcin":     [(-250, "#eb4d4b"), (-150, "#f0932b"), (-75, "#f9ca24"), (-25, _MUTED)],
    "mucin":     [(-250, "#eb4d4b"), (-150, "#f0932b"), (-75, "#f9ca24"), (-25, _MUTED)],
    "srh_500":   [(0, _MUTED), (50,  "#f9ca24"), (100, "#f0932b"), (200, "#eb4d4b")],
    "srh01":     [(0, _MUTED), (100, "#f9ca24"), (200, "#f0932b"), (400, "#eb4d4b")],
    "srh03":     [(0, _MUTED), (150, "#f9ca24"), (300, "#f0932b"), (500, "#eb4d4b")],
    "shear_500": [(0, _MUTED), (10,  "#f9ca24"), (20,  "#f0932b"), (30,  "#eb4d4b")],
    "shear01":   [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "shear03":   [(0, _MUTED), (20,  "#f9ca24"), (30,  "#f0932b"), (45,  "#eb4d4b")],
    "shear06":   [(0, _MUTED), (30,  "#f9ca24"), (40,  "#f0932b"), (50,  "#eb4d4b")],
    "srw_500":   [(0, _MUTED), (10,  "#f9ca24"), (20,  "#f0932b"), (30,  "#eb4d4b")],
    "srw01":     [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "srw03":     [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "srw06":     [(0, _MUTED), (15,  "#f9ca24"), (25,  "#f0932b"), (35,  "#eb4d4b")],
    "stp":       [(0, _MUTED), (1,   "#f9ca24"), (3,   "#f0932b"), (5,   "#eb4d4b"),
                  (8, "#c0392b")],
    "scp":       [(0, _MUTED), (2,   "#f9ca24"), (4,   "#f0932b"), (8,   "#eb4d4b")],
    "ehi":       [(0, _MUTED), (1,   "#f9ca24"), (2,   "#f0932b"), (3,   "#eb4d4b")],
    "lr75":      [(0, _MUTED), (7.0, "#f9ca24"), (8.0, "#f0932b"), (9.0, "#eb4d4b")],
    "lr03":      [(5, _MUTED), (7.0, "#f9ca24"), (8.0, "#f0932b"), (9.0, "#eb4d4b")],
    "sfc_the":   [(320, _MUTED), (335, "#f9ca24"), (350, "#f0932b"), (365, "#eb4d4b")],
    "pw":        [(0, _MUTED), (25,  "#f9ca24"), (38,  "#4fc3f7"), (50,  "#74b9ff")],
}


def _threshold_color(key: str, value) -> str | None:
    thresholds = _THRESHOLDS.get(key)
    if not thresholds or value is None:
        return None
    color = thresholds[0][1]
    for threshold, c in thresholds:
        if value >= threshold:
            color = c
    return color


# ── Scalar params (flat row at bottom) ───────────────────────────────────────
_SCALAR_PARAMS = [
    ("lr75",    "LR 700-500", "°C/km", _MUTED),
    ("lr03",    "LR 0-3 km",  "°C/km", _MUTED),
    ("sfc_the", "SFC θe",     "K",     "#ff9f43"),
    ("pw",      "PW",         "mm",    "#4ecdc4"),
    ("conv_t",  "Conv Temp",  "°C",    "#ff9f43"),
    ("stp",     "STP",        "",      "#fd79a8"),
    ("scp",     "SCP",        "",      "#b39ddb"),
    ("ehi",     "EHI",        "",      "#fd79a8"),
]

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


# Pressure levels used for wind barbs and hodograph thinning.
# Denser than strict mandatory levels to give ~18-20 points per sounding —
# enough detail without the noise of every significant level.
_MANDATORY_PRES = np.array(
    [1000, 975, 950, 925, 900, 875, 850, 825, 800, 750, 700,
     650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100],
    dtype=float
)

def _thin_to_mandatory(pres_arr: np.ndarray, tol: float = 15.0) -> np.ndarray:
    """Return sorted indices of levels closest to each mandatory pressure level.

    Skips a mandatory level if no data point falls within *tol* hPa of it.
    Each data index is included at most once.
    """
    indices: list[int] = []
    seen: set[int] = set()
    for mp in _MANDATORY_PRES:
        if mp > pres_arr[0] + tol or mp < pres_arr[-1] - tol:
            continue  # outside the sounding's pressure range
        i = int(np.argmin(np.abs(pres_arr - mp)))
        if np.abs(pres_arr[i] - mp) <= tol and i not in seen:
            seen.add(i)
            indices.append(i)
    return np.array(sorted(indices), dtype=int)


def _p_at_t(snd: Sounding, target_c: float) -> float | None:
    """Pressure (hPa) where temperature first crosses target_c °C from surface up."""
    for i in range(len(snd.temperature) - 1):
        t0, t1 = snd.temperature[i], snd.temperature[i + 1]
        if (t0 >= target_c >= t1) or (t0 <= target_c <= t1):
            frac = (target_c - t0) / (t1 - t0) if t1 != t0 else 0.0
            return float(snd.pressure[i] + frac * (snd.pressure[i + 1] - snd.pressure[i]))
    return None


class SoundingDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("HRRR Point Sounding")
        self.setMinimumSize(640, 660)
        self.resize(760, 800)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        _force_bg(self)
        self.setStyleSheet(
            f"QDialog {{ background-color: {_FIG_BG}; }}"
            f"QLabel  {{ background-color: transparent; color: {_TEXT}; }}"
            f"QFrame  {{ background-color: {_BORDER}; border: none; }}"
            + _SLIDER_QSS
        )

        self._sset: SoundingSet | None = None
        self._cur_idx       = 0
        self._param_labels: dict[str, QLabel] = {}
        self._param_colors: dict[str, str]    = {}
        self._tick_labels:  list[QLabel]       = []

        self._skewt_ax     = None
        self._current_snd: Sounding | None = None
        self._cursor_hline = None

        self._eil_base_p: float | None = None
        self._eil_top_p:  float | None = None

        self._build_ui()

    # ── Public ───────────────────────────────────────────────────────────────

    def load(self, sset: SoundingSet):
        self._sset    = sset
        self._cur_idx = next(
            (i for i, s in enumerate(sset.soundings) if s.slot_offset == 0), 0
        )
        if sset.is_nssl:
            self.setWindowTitle("NSSL Observed Sounding")
        elif sset.is_observed:
            self.setWindowTitle("Observed Sounding")
        else:
            self.setWindowTitle("HRRR Point Sounding")
        self._rebuild_scrubber()
        self._draw()
        if not self.isVisible():
            self.show()
        self.raise_()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        _A = Qt.AlignmentFlag.AlignCenter

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(3)

        # ── Header ───────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._header_line1 = _lbl("HRRR  —", color=_TEXT,  size=11, bold=True)
        self._header_line2 = _lbl("—",       color=_MUTED, size=10)
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
        self._scrubber.setMaximum(3)
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
        self._fig    = Figure(facecolor=_FIG_BG)
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

        # ── Bottom panel ──────────────────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.setContentsMargins(4, 2, 4, 0)

        # LEFT: parcel table
        parcel_w = QWidget()
        parcel_w.setAutoFillBackground(False)
        pg = QGridLayout(parcel_w)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setHorizontalSpacing(10)
        pg.setVerticalSpacing(1)

        # Parcel table header row
        pg.addWidget(_lbl("", color=_HDR_CLR, size=9, bold=True), 0, 0)
        for c, hdr in enumerate(["CAPE", "CIN", "LCL", "LFC", "EL"]):
            pg.addWidget(_lbl(hdr, color=_HDR_CLR, size=9, bold=True, align=_A), 0, c + 1)

        # Parcel table data rows
        _parcel_rows = [
            ("SB", ["sbcape", "sbcin", "sblcl", "sblfc", "sbel"]),
            ("ML", ["mlcape", "mlcin", "mllcl", "mllfc", "mlel"]),
            ("MU", ["mucape", "mucin", "mulcl", "mulfc", "muel"]),
        ]
        for r, (row_lbl, keys) in enumerate(_parcel_rows):
            pg.addWidget(_lbl(row_lbl, color=_HDR_CLR, size=10, bold=True), r + 1, 0)
            for c, key in enumerate(keys):
                vl = _lbl("—", color=_MUTED, size=12, bold=True, align=_A)
                pg.addWidget(vl, r + 1, c + 1)
                self._param_labels[key] = vl
                self._param_colors[key] = _MUTED

        bottom.addWidget(parcel_w, stretch=3)

        # Vertical divider between tables
        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.Shape.VLine)
        vdiv.setFixedWidth(1)
        bottom.addWidget(vdiv)

        # RIGHT: kinematics table
        kin_w = QWidget()
        kin_w.setAutoFillBackground(False)
        kg = QGridLayout(kin_w)
        kg.setContentsMargins(0, 0, 0, 0)
        kg.setHorizontalSpacing(10)
        kg.setVerticalSpacing(1)

        kg.addWidget(_lbl("", color=_HDR_CLR, size=9, bold=True), 0, 0)
        for c, hdr in enumerate(["Shear", "SRH", "SRW"]):
            kg.addWidget(_lbl(hdr, color=_HDR_CLR, size=9, bold=True, align=_A), 0, c + 1)

        _kin_rows = [
            ("0-500m", "shear_500", "srh_500", "srw_500"),
            ("0-1km",  "shear01",   "srh01",   "srw01"),
            ("0-3km",  "shear03",   "srh03",   "srw03"),
            ("0-6km",  "shear06",   None,      "srw06"),
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
                    self._param_colors[key] = _MUTED

        bottom.addWidget(kin_w, stretch=2)

        root.addLayout(bottom)

        # Horizontal separator between tables and scalar row
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        root.addWidget(sep2)

        # ── Scalar params (flat row) ──────────────────────────────────────────
        scalar_w = QWidget()
        scalar_w.setAutoFillBackground(False)
        sg = QGridLayout(scalar_w)
        sg.setContentsMargins(4, 1, 4, 2)
        sg.setHorizontalSpacing(8)
        sg.setVerticalSpacing(1)

        for c, (key, label, unit, def_color) in enumerate(_SCALAR_PARAMS):
            hdr_text = f"{label} ({unit})" if unit else label
            sg.addWidget(_lbl(hdr_text, color=_HDR_CLR, size=9, align=_A), 0, c)
            vl = _lbl("—", color=def_color, size=12, bold=True, align=_A)
            sg.addWidget(vl, 1, c)
            self._param_labels[key] = vl
            self._param_colors[key] = def_color

        root.addWidget(scalar_w)

    # ── Scrubber ──────────────────────────────────────────────────────────────

    def _rebuild_scrubber(self):
        while self._tick_row.count():
            item = self._tick_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tick_labels.clear()

        if not self._sset:
            return

        count = len(self._sset.soundings)
        self._scrubber_widget.setVisible(count > 1)
        self._scrubber.blockSignals(True)
        self._scrubber.setMaximum(count - 1)
        self._scrubber.setValue(self._cur_idx)
        self._scrubber.blockSignals(False)

        # For obs/nssl: show the day number if any two soundings share the same hour.
        time_based = self._sset.is_observed or self._sset.is_nssl
        snd_hours = (
            [s.valid_time.strftime("%H%d") for s in self._sset.soundings]
            if time_based else []
        )
        needs_day = len(snd_hours) != len(set(
            s.valid_time.strftime("%H") for s in self._sset.soundings
        )) if time_based else False

        self._tick_row.addStretch(1)
        for i, snd in enumerate(self._sset.soundings):
            if time_based:
                if needs_day:
                    f_str = f"{snd.valid_time.strftime('%H%MZ')}\n{snd.valid_time.day}"
                else:
                    f_str = snd.valid_time.strftime("%H%MZ")
            else:
                if snd.slot_offset == 0:
                    f_str = "F0"
                elif snd.slot_offset < 0:
                    f_str = f"T{snd.slot_offset}h"
                else:
                    f_str = f"F+{snd.slot_offset}h"
            lbl = _lbl(f_str, color=_MUTED, size=8,
                       align=Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setFixedWidth(36)
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
        if not self._sset or not self._sset.soundings:
            return
        snd = self._sset.soundings[self._cur_idx]
        if not snd:
            return

        if self._sset.is_nssl:
            valid_str = snd.valid_time.strftime("%H%MZ %d %b %Y")
            self._header_line1.setText("NSSL  ·  DL Truck")
            self._header_line2.setText(
                f"Valid {valid_str}  ·  {self._sset.elevation:.0f} m MSL"
            )
        elif self._sset.is_observed:
            valid_str = snd.valid_time.strftime("%Hz %d %b %Y")
            self._header_line1.setText(
                f"OBS  ·  {self._sset.station_name} ({self._sset.station_id})"
            )
            self._header_line2.setText(
                f"Valid {valid_str}  ·  {self._sset.elevation:.0f} m MSL"
            )
        else:
            f0 = self._sset.get(0)
            if not f0:
                return
            init_str  = f0.valid_time.strftime("%Hz %d %b %Y")
            valid_str = snd.valid_time.strftime("%Hz %d %b %Y")
            if snd.slot_offset == 0:
                f_str = "Analysis"
            elif snd.slot_offset < 0:
                f_str = f"T{snd.slot_offset}h"
            else:
                f_str = f"F+{snd.slot_offset}h"
            lat, lon  = self._sset.lat, self._sset.lon
            self._header_line1.setText(
                f"HRRR  Init {init_str}  ·  Valid {valid_str} ({f_str})"
            )
            self._header_line2.setText(
                f"{abs(lat):.3f}°{'N' if lat >= 0 else 'S'}  "
                f"{abs(lon):.3f}°{'E' if lon >= 0 else 'W'}  ·  "
                f"{self._sset.elevation:.0f} m MSL"
            )

        for i, lbl in enumerate(self._tick_labels):
            active = (i == self._cur_idx)
            lbl.setStyleSheet(
                "background-color: transparent; font-size: 8px; "
                + (f"color: {_STORM_CYAN}; font-weight: bold;" if active
                   else f"color: {_MUTED};")
            )

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        if not self._sset or not self._sset.soundings:
            return
        snd = self._sset.soundings[min(self._cur_idx, len(self._sset.soundings) - 1)]
        self._current_snd = snd

        self._eil_base_p = self._eil_top_p = None
        try:
            pres = snd.pressure    * units.hPa
            temp = snd.temperature * units.degC
            dewp = snd.dewpoint    * units.degC
            eil_base, eil_top = mpcalc.effective_inflow_layer(pres, temp, dewp)
            self._eil_base_p = float(eil_base.to("hPa").m)
            self._eil_top_p  = float(eil_top.to("hPa").m)
        except Exception as e:
            log.debug("EIL calculation failed: %s", e)

        self._fig.clear()
        self._cursor_hline = None
        self._skewt_ax     = None

        self._draw_skewt(snd)
        self._update_params(snd)
        self._canvas.draw_idle()

    # ── Skew-T ────────────────────────────────────────────────────────────────

    def _draw_skewt(self, snd: Sounding):
        skewt = SkewT(self._fig, rotation=45, rect=(0.07, 0.04, 0.91, 0.93))
        ax = skewt.ax
        self._skewt_ax = ax

        ax.set_facecolor(_AX_BG)
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.tick_params(colors=_MUTED, labelsize=8)
        ax.set_xlabel("Temperature (°C)", color=_MUTED, fontsize=8)
        ax.set_ylabel("Pressure (hPa)",   color=_MUTED, fontsize=8)
        ax.grid(True, color=_BORDER, alpha=0.45, linewidth=0.5)

        pres = snd.pressure    * units.hPa
        temp = snd.temperature * units.degC
        dewp = snd.dewpoint    * units.degC
        u_kt = (snd.u_wind * units("m/s")).to("knots")
        v_kt = (snd.v_wind * units("m/s")).to("knots")

        # Index set for plotted traces.  High-resolution soundings (>200 levels,
        # e.g. CLAMPS lidar-sonde) are thinned to mandatory pressure levels so
        # the rendered line density matches a standard radiosonde.  All MetPy
        # calculations (parcel profile, CAPE/CIN shading, vtemp) continue to use
        # the full-resolution arrays above.
        _pt = _thin_to_mandatory(snd.pressure) if len(snd.pressure) > 200 \
              else np.arange(len(snd.pressure))

        skewt.plot_dry_adiabats(
            t0=np.arange(-40, 200, 10) * units.degC,
            alpha=0.14, colors="saddlebrown", linewidths=0.7,
        )
        skewt.plot_moist_adiabats(
            t0=np.arange(-20, 35, 5) * units.degC,
            alpha=0.14, colors="forestgreen", linewidths=0.7,
        )
        skewt.plot_mixing_lines(
            pressure=np.arange(1000, 99, -50) * units.hPa,
            alpha=0.14, colors="dodgerblue", linewidths=0.7,
        )

        # DGZ shading
        p_neg10 = _p_at_t(snd, -10.0)
        p_neg20 = _p_at_t(snd, -20.0)
        if p_neg10 is not None and p_neg20 is not None:
            ax.axhspan(p_neg10, p_neg20, alpha=0.07, color="#4fc3f7", zorder=1)

        # Main traces — thinned for high-res soundings, full-res otherwise
        skewt.plot(pres[_pt], temp[_pt], color=_TEMP_CLR, linewidth=2, zorder=5)
        skewt.plot(pres[_pt], dewp[_pt], color=_DEWP_CLR, linewidth=2, zorder=5)

        # Virtual temperature (dashed, warm tint)
        # Mixing ratio computed on full-res; only the plotted line is thinned.
        try:
            mr    = mpcalc.mixing_ratio_from_dewpoint(pres, dewp)
            vtemp = mpcalc.virtual_temperature(temp, mr).to("degC")
            skewt.plot(pres[_pt], vtemp[_pt], color=_VTEMP_CLR, linewidth=1.1,
                       linestyle="--", alpha=0.6, zorder=5)
        except Exception as e:
            log.debug("virtual temperature plot failed: %s", e)

        # Wind barbs at mandatory pressure levels only
        thin = _thin_to_mandatory(snd.pressure)
        skewt.plot_barbs(pres[thin], u_kt[thin], v_kt[thin], color=_BARB_CLR,
                         linewidth=0.8, length=6)

        # Parcel profile + CAPE/CIN shading.
        # Parcel calculation and shading use the full-resolution profile for
        # accuracy.  Only the plotted parcel trace is thinned.
        sb_parcel = None
        try:
            sb_parcel = mpcalc.parcel_profile(pres, temp[0], dewp[0])
            skewt.plot(pres[_pt], sb_parcel[_pt], color=_PARCEL_CLR, linewidth=1,
                       linestyle="--", alpha=0.5, zorder=5)
            skewt.shade_cape(pres, temp, sb_parcel, alpha=0.18, color=_TEMP_CLR)
            skewt.shade_cin(pres, temp, sb_parcel,  alpha=0.18, color="#4fc3f7")
        except Exception as e:
            log.debug("parcel profile / CAPE-CIN shading failed: %s", e)

        # LCL marker
        try:
            lcl_p, lcl_t = mpcalc.lcl(pres[0], temp[0], dewp[0])
            skewt.plot(lcl_p, lcl_t, "o", color="cyan", markersize=5,
                       markerfacecolor="none", markeredgewidth=1.5, zorder=6)
        except Exception as e:
            log.debug("LCL marker failed: %s", e)

        ax.set_ylim(1050, 100)
        ax.set_xlim(-40, 50)

        # ── AGL height labels on left spine ──────────────────────────────────
        _agl_trans = blended_transform_factory(ax.transAxes, ax.transData)
        z_sfc = snd.height[0]
        for agl_m, agl_lbl in [
            (500,  "0.5"), (1000, "1"), (2000, "2"),
            (3000, "3"),   (4000, "4"), (6000, "6"), (9000, "9"),
        ]:
            z_target = z_sfc + agl_m
            if z_target > snd.height[-1]:
                continue
            p_agl = float(np.interp(z_target, snd.height, snd.pressure))
            if not (100 < p_agl < 1050):
                continue
            ax.axhline(p_agl, color="#cc3333", linewidth=0.5,
                       linestyle=":", alpha=0.35, zorder=1)
            ax.text(0.008, p_agl, f"{agl_lbl}",
                    transform=_agl_trans, color="#cc3333",
                    fontsize=6, va="center", ha="left",
                    alpha=0.85, clip_on=False)

        self._cursor_hline = ax.axhline(
            y=850, color=_ACCENT, linewidth=0.9, alpha=0, zorder=10,
        )

        # EIL bracket on left spine
        if self._eil_base_p is not None and self._eil_top_p is not None:
            _bl = blended_transform_factory(ax.transAxes, ax.transData)
            ax.plot([0.012, 0.012],
                    [self._eil_base_p, self._eil_top_p],
                    color=_EIL_CLR, linewidth=2.5,
                    transform=_bl, solid_capstyle="round",
                    clip_on=False, zorder=9)
            for p in (self._eil_base_p, self._eil_top_p):
                ax.plot([0.012, 0.038], [p, p],
                        color=_EIL_CLR, linewidth=2.5,
                        transform=_bl, solid_capstyle="round",
                        clip_on=False, zorder=9)
            mid_p = (self._eil_base_p + self._eil_top_p) / 2
            ax.text(0.045, mid_p, "EIL",
                    transform=_bl, color=_EIL_CLR,
                    fontsize=6, va="center", ha="left", alpha=0.85)

        ax_hodo = ax.inset_axes([0.675, 0.625, 0.30, 0.36])
        self._draw_hodograph_inset(snd, ax_hodo)

    # ── Hodograph inset ───────────────────────────────────────────────────────

    def _draw_hodograph_inset(self, snd: Sounding, ax):
        ax.set_facecolor(_AX_BG + "dd")
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.tick_params(left=False, bottom=False,
                       labelleft=False, labelbottom=False)

        h = Hodograph(ax, component_range=60)
        h.add_grid(increment=20, color=_BORDER, alpha=0.7, linewidth=0.6)

        for spd in (20, 40, 60):
            ax.text(spd * 0.72, spd * 0.72, f"{spd}", fontsize=5,
                    color=_MUTED, ha="center", va="center", alpha=0.7)

        # Use only levels that have actual wind observations — skipping T/Td-only
        # significant levels that carry no wind data.  For high-res soundings
        # (CLAMPS etc.) first thin to mandatory levels to avoid noise, then
        # filter to wind-observed levels.  This matches SPC's hodograph approach.
        if len(snd.pressure) > 200:
            candidate_idx = _thin_to_mandatory(snd.pressure)
        else:
            candidate_idx = np.arange(len(snd.pressure))
        wind_mask  = ~np.isnan(snd.u_wind[candidate_idx])
        thin       = candidate_idx[wind_mask]
        u_kt       = (snd.u_wind[thin] * units("m/s")).to("knots").magnitude
        v_kt       = (snd.v_wind[thin] * units("m/s")).to("knots").magnitude
        pres_thin  = snd.pressure[thin]
        hgt_thin   = snd.height[thin]
        hgt_agl_km = (hgt_thin - hgt_thin[0]) / 1000.0

        # Gapless height-colored trace via LineCollection
        if len(u_kt) >= 2:
            pts  = np.column_stack([u_kt, v_kt]).reshape(-1, 1, 2)
            segs = np.concatenate([pts[:-1], pts[1:]], axis=1)

            seg_colors = []
            for i in range(len(hgt_agl_km) - 1):
                mid_h = (hgt_agl_km[i] + hgt_agl_km[i + 1]) / 2.0
                clr = _HODO_LAYERS[-1][0]
                for c, lo, hi in _HODO_LAYERS:
                    if lo <= mid_h < hi:
                        clr = c
                        break
                seg_colors.append(clr)

            ax.add_collection(
                LineCollection(segs, colors=seg_colors, linewidth=2.0, zorder=4)
            )

            # EIL highlight
            if self._eil_base_p is not None and self._eil_top_p is not None:
                eil_colors = []
                for i in range(len(pres_thin) - 1):
                    p_mid = (pres_thin[i] + pres_thin[i + 1]) / 2.0
                    in_eil = self._eil_top_p <= p_mid <= self._eil_base_p
                    eil_colors.append(_EIL_CLR if in_eil else (0, 0, 0, 0))
                ax.add_collection(
                    LineCollection(segs, colors=eil_colors,
                                   linewidth=5.5, alpha=0.28, zorder=3)
                )

        # Height labels
        for km in (1, 3, 6):
            i = int(np.argmin(np.abs(hgt_agl_km - km)))
            if i < len(u_kt):
                ax.annotate(f"{km}", xy=(u_kt[i], v_kt[i]),
                            fontsize=5, color=_TEXT, alpha=0.85,
                            xytext=(3, 3), textcoords="offset points")

        # Bunkers storm motion — colored dot markers + corner text
        try:
            pres = snd.pressure * units.hPa
            u_ms = snd.u_wind   * units("m/s")
            v_ms = snd.v_wind   * units("m/s")
            hgt  = snd.height   * units.m
            rm, lm, _ = mpcalc.bunkers_storm_motion(pres, u_ms, v_ms, hgt)

            rm_u_kt = float(rm[0].to("knots").m)
            rm_v_kt = float(rm[1].to("knots").m)
            lm_u_kt = float(lm[0].to("knots").m)
            lm_v_kt = float(lm[1].to("knots").m)

            rm_spd = float(np.sqrt(rm_u_kt**2 + rm_v_kt**2))
            rm_dir = float(np.degrees(np.arctan2(rm[0].to("m/s").m, rm[1].to("m/s").m)) % 360)
            lm_spd = float(np.sqrt(lm_u_kt**2 + lm_v_kt**2))
            lm_dir = float(np.degrees(np.arctan2(lm[0].to("m/s").m, lm[1].to("m/s").m)) % 360)

            ax.plot(rm_u_kt, rm_v_kt, "o", color=_RM_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)
            ax.plot(lm_u_kt, lm_v_kt, "o", color=_LM_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)

            # Dir/spd text in top-left corner of hodograph
            ax.text(0.03, 0.97, f"RM  {rm_dir:.0f}°/{rm_spd:.0f}kt",
                    transform=ax.transAxes, color=_RM_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)
            ax.text(0.03, 0.90, f"LM  {lm_dir:.0f}°/{lm_spd:.0f}kt",
                    transform=ax.transAxes, color=_LM_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)
        except Exception as e:
            log.debug("Bunkers storm motion failed: %s", e)

    # ── Interactive cursor ────────────────────────────────────────────────────

    def _on_mouse_move(self, event):
        if event.inaxes is not self._skewt_ax or self._current_snd is None:
            return
        if event.ydata is None:
            return
        snd = self._current_snd
        i   = int(np.argmin(np.abs(snd.pressure - event.ydata)))
        p   = snd.pressure[i]
        t   = snd.temperature[i]
        td  = snd.dewpoint[i]
        ws  = float(snd.wind_speed[i] * 1.944)
        wd  = float(snd.wind_direction[i])
        hh  = snd.height[i]
        self._cursor_label.setText(
            f"{p:.0f} hPa  ·  T {t:+.1f}°C  Td {td:+.1f}°C  ·  "
            f"Wind {wd:.0f}° @ {ws:.0f} kt  ·  {hh:.0f} m MSL"
        )
        if self._cursor_hline is not None:
            self._cursor_hline.set_ydata([p, p])
            self._cursor_hline.set_alpha(0.55)
            self._canvas.draw_idle()

    def _on_axes_leave(self, event):
        self._cursor_label.setText("")
        if self._cursor_hline is not None:
            self._cursor_hline.set_alpha(0)
            self._canvas.draw_idle()

    # ── Derived parameters ────────────────────────────────────────────────────

    def _update_params(self, snd: Sounding):
        def _set(key, value, fmt="{:.0f}"):
            lbl = self._param_labels.get(key)
            if lbl is None:
                return
            lbl.setText(fmt.format(value) if value is not None else "—")
            thr = _threshold_color(key, value) if value is not None else None
            color = thr if thr is not None else self._param_colors.get(key, _TEXT)
            if value is None:
                color = _MUTED
            lbl.setStyleSheet(
                f"background-color: transparent; color: {color}; "
                f"font-size: 12px; font-weight: bold;"
            )

        pres    = snd.pressure    * units.hPa
        temp    = snd.temperature * units.degC
        dewp    = snd.dewpoint    * units.degC
        u_ms    = snd.u_wind      * units("m/s")
        v_ms    = snd.v_wind      * units("m/s")
        hgt     = snd.height      * units.m
        hgt_agl = (snd.height - snd.height[0]) * units.m

        # ── Surface-based parcel ──────────────────────────────────────────────
        sb_parcel = sbcape_val = None
        try:
            sb_parcel = mpcalc.parcel_profile(pres, temp[0], dewp[0])
            sbcape, sbcin = mpcalc.cape_cin(pres, temp, dewp, sb_parcel)
            sbcape_val = float(sbcape.to("J/kg").m)
            _set("sbcape", sbcape_val)
            _set("sbcin",  float(sbcin.to("J/kg").m))
        except Exception as e:
            log.debug("SB CAPE/CIN failed: %s", e)
            _set("sbcape", None); _set("sbcin", None)

        lcl_m = None
        try:
            lcl_p, _ = mpcalc.lcl(pres[0], temp[0], dewp[0])
            lcl_m = float(np.interp(
                lcl_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
            )) - snd.height[0]
            _set("sblcl", lcl_m)
        except Exception as e:
            log.debug("SB LCL failed: %s", e)
            _set("sblcl", None)

        try:
            lfc_p, _ = mpcalc.lfc(pres, temp, dewp, sb_parcel)
            _set("sblfc", float(np.interp(
                lfc_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
            )) - snd.height[0])
        except Exception as e:
            log.debug("SB LFC failed: %s", e)
            _set("sblfc", None)

        try:
            el_p, _ = mpcalc.el(pres, temp, dewp, sb_parcel)
            _set("sbel", float(np.interp(
                el_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
            )) - snd.height[0])
        except Exception as e:
            log.debug("SB EL failed: %s", e)
            _set("sbel", None)

        # ── Mixed-layer parcel ────────────────────────────────────────────────
        ml_cape_val = ml_cin_val = None
        ml_p = ml_t = ml_td = ml_parcel = None
        try:
            ml_p, ml_t, ml_td = mpcalc.mixed_parcel(
                pres, temp, dewp, depth=100 * units.hPa
            )
            ml_parcel = mpcalc.parcel_profile(pres, ml_t, ml_td)
            mlcape, mlcin = mpcalc.cape_cin(pres, temp, dewp, ml_parcel)
            ml_cape_val = float(mlcape.to("J/kg").m)
            ml_cin_val  = float(mlcin.to("J/kg").m)
            _set("mlcape", ml_cape_val)
            _set("mlcin",  ml_cin_val)
        except Exception as e:
            log.debug("ML CAPE/CIN failed: %s", e)
            _set("mlcape", None); _set("mlcin", None)

        if ml_p is not None:
            try:
                mllcl_p, _ = mpcalc.lcl(ml_p, ml_t, ml_td)
                _set("mllcl", float(np.interp(
                    mllcl_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
                )) - snd.height[0])
            except Exception as e:
                log.debug("ML LCL failed: %s", e)
                _set("mllcl", None)
            if ml_parcel is not None:
                try:
                    mllfc_p, _ = mpcalc.lfc(pres, temp, dewp, ml_parcel)
                    _set("mllfc", float(np.interp(
                        mllfc_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
                    )) - snd.height[0])
                except Exception as e:
                    log.debug("ML LFC failed: %s", e)
                    _set("mllfc", None)
                try:
                    mlel_p, _ = mpcalc.el(pres, temp, dewp, ml_parcel)
                    _set("mlel", float(np.interp(
                        mlel_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
                    )) - snd.height[0])
                except Exception as e:
                    log.debug("ML EL failed: %s", e)
                    _set("mlel", None)
        else:
            _set("mllcl", None); _set("mllfc", None); _set("mlel", None)

        # ── Most-unstable parcel ──────────────────────────────────────────────
        mucape_val = None
        mu_p = mu_t = mu_td = mu_parcel = None
        try:
            mu_result = mpcalc.most_unstable_parcel(
                pres, temp, dewp, depth=300 * units.hPa
            )
            mu_p, mu_t, mu_td = mu_result[0], mu_result[1], mu_result[2]
            mu_parcel = mpcalc.parcel_profile(pres, mu_t, mu_td)
            mucape, mucin = mpcalc.cape_cin(pres, temp, dewp, mu_parcel)
            mucape_val = float(mucape.to("J/kg").m)
            _set("mucape", mucape_val)
            _set("mucin",  float(mucin.to("J/kg").m))
        except Exception as e:
            log.debug("MU parcel failed: %s", e)
            # Fallback for mucape only
            try:
                mucape_fb, _ = mpcalc.most_unstable_cape_cin(pres, temp, dewp)
                mucape_val = float(mucape_fb.to("J/kg").m)
                _set("mucape", mucape_val)
            except Exception as e2:
                log.debug("MU CAPE fallback failed: %s", e2)
                _set("mucape", None)
            _set("mucin", None)

        if mu_p is not None:
            try:
                mulcl_p, _ = mpcalc.lcl(mu_p, mu_t, mu_td)
                _set("mulcl", float(np.interp(
                    mulcl_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
                )) - snd.height[0])
            except Exception as e:
                log.debug("MU LCL failed: %s", e)
                _set("mulcl", None)
            if mu_parcel is not None:
                try:
                    mulfc_p, _ = mpcalc.lfc(pres, temp, dewp, mu_parcel)
                    _set("mulfc", float(np.interp(
                        mulfc_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
                    )) - snd.height[0])
                except Exception as e:
                    log.debug("MU LFC failed: %s", e)
                    _set("mulfc", None)
                try:
                    muel_p, _ = mpcalc.el(pres, temp, dewp, mu_parcel)
                    _set("muel", float(np.interp(
                        muel_p.to("hPa").m, snd.pressure[::-1], snd.height[::-1]
                    )) - snd.height[0])
                except Exception as e:
                    log.debug("MU EL failed: %s", e)
                    _set("muel", None)
        else:
            _set("mulcl", None); _set("mulfc", None); _set("muel", None)

        # ── Lapse rates ───────────────────────────────────────────────────────
        try:
            t700 = float(np.interp(700, snd.pressure[::-1], snd.temperature[::-1]))
            t500 = float(np.interp(500, snd.pressure[::-1], snd.temperature[::-1]))
            z700 = float(np.interp(700, snd.pressure[::-1], snd.height[::-1]))
            z500 = float(np.interp(500, snd.pressure[::-1], snd.height[::-1]))
            dz_km = (z500 - z700) / 1000.0
            _set("lr75", (t700 - t500) / dz_km if dz_km != 0.0 else None, fmt="{:.1f}")
        except Exception as e:
            log.debug("lr75 failed: %s", e)
            _set("lr75", None)

        try:
            z_sfc = snd.height[0]
            t_sfc = snd.temperature[0]
            t_3km = float(np.interp(z_sfc + 3000.0, snd.height, snd.temperature))
            _set("lr03", (t_sfc - t_3km) / 3.0, fmt="{:.1f}")
        except Exception as e:
            log.debug("LR03 failed: %s", e)
            _set("lr03", None)

        # ── Surface θe ────────────────────────────────────────────────────────
        try:
            the = mpcalc.equivalent_potential_temperature(pres[0], temp[0], dewp[0])
            _set("sfc_the", float(the.to("K").m), fmt="{:.0f}")
        except Exception as e:
            log.debug("SFC θe failed: %s", e)
            _set("sfc_the", None)

        # ── Precipitable water ────────────────────────────────────────────────
        try:
            _set("pw", float(mpcalc.precipitable_water(pres, dewp).to("mm").m))
        except Exception as e:
            log.debug("precipitable water failed: %s", e)
            _set("pw", None)

        # ── Convective temperature ────────────────────────────────────────────
        # Temperature the surface must reach for parcels to initiate convection.
        # Found by tracing the dry adiabat from the CCL back to surface pressure.
        try:
            ccl_p, ccl_t = mpcalc.convective_condensation_level(pres, temp, dewp)
            # Poisson's equation: T_conv = T_ccl * (p_sfc / p_ccl)^(Rd/cp)
            conv_t_K = float(ccl_t.to("K").m) * (
                float(pres[0].to("hPa").m) / float(ccl_p.to("hPa").m)
            ) ** 0.2854
            _set("conv_t", conv_t_K - 273.15, fmt="{:.1f}")
        except Exception as e:
            log.debug("convective temperature failed: %s", e)
            _set("conv_t", None)

        # ── Storm motion + SRH ────────────────────────────────────────────────
        srh01_val = None
        rm_u_ms_f = rm_v_ms_f = None
        try:
            rm, lm, _ = mpcalc.bunkers_storm_motion(pres, u_ms, v_ms, hgt)
            rm_u = rm[0].to("m/s")
            rm_v = rm[1].to("m/s")
            lm_u = lm[0].to("m/s")
            lm_v = lm[1].to("m/s")
            rm_u_ms_f = float(rm_u.m)
            rm_v_ms_f = float(rm_v.m)

            rm_spd = float(np.sqrt(rm_u**2 + rm_v**2).to("knots").m)
            rm_dir = float(np.degrees(np.arctan2(-rm_u.m, -rm_v.m)) % 360)
            lm_spd = float(np.sqrt(lm_u**2 + lm_v**2).to("knots").m)
            lm_dir = float(np.degrees(np.arctan2(-lm_u.m, -lm_v.m)) % 360)


            srh01, _, _ = mpcalc.storm_relative_helicity(
                hgt_agl, u_ms, v_ms, depth=1 * units.km,
                storm_u=rm_u, storm_v=rm_v,
            )
            srh03, _, _ = mpcalc.storm_relative_helicity(
                hgt_agl, u_ms, v_ms, depth=3 * units.km,
                storm_u=rm_u, storm_v=rm_v,
            )
            srh_500, _, _ = mpcalc.storm_relative_helicity(
                hgt_agl, u_ms, v_ms, depth=500 * units.m,
                storm_u=rm_u, storm_v=rm_v,
            )
            srh01_val = float(srh01.to("m**2/s**2").m)
            _set("srh01",   srh01_val)
            _set("srh03",   float(srh03.to("m**2/s**2").m))
            _set("srh_500", float(srh_500.to("m**2/s**2").m))
        except Exception as e:
            log.debug("storm motion / SRH failed: %s", e)
            for k in ("srh01", "srh03", "srh_500"):
                _set(k, None)

        # ── Bulk shear ────────────────────────────────────────────────────────
        shear06_kt = None
        for depth, key in [
            (500  * units.m,  "shear_500"),
            (1    * units.km, "shear01"),
            (3    * units.km, "shear03"),
            (6    * units.km, "shear06"),
        ]:
            try:
                us, vs = mpcalc.bulk_shear(
                    pres, u_ms, v_ms, height=hgt_agl, depth=depth,
                )
                val = float(np.sqrt(us**2 + vs**2).to("knots").m)
                _set(key, val)
                if key == "shear06":
                    shear06_kt = val
            except Exception as e:
                log.debug("bulk shear %s failed: %s", key, e)
                _set(key, None)

        # ── Storm-relative wind (mean per layer, RM-relative) ─────────────────
        hgt_agl_m = snd.height - snd.height[0]
        if rm_u_ms_f is not None:
            for depth_m, key in [
                (500,  "srw_500"),
                (1000, "srw01"),
                (3000, "srw03"),
                (6000, "srw06"),
            ]:
                try:
                    mask = hgt_agl_m <= depth_m
                    if mask.sum() >= 2:
                        rel_u = snd.u_wind[mask] - rm_u_ms_f
                        rel_v = snd.v_wind[mask] - rm_v_ms_f
                        _set(key, float(np.mean(np.sqrt(rel_u**2 + rel_v**2))) * 1.944)
                    else:
                        _set(key, None)
                except Exception as e:
                    log.debug("SRW %s failed: %s", key, e)
                    _set(key, None)
        else:
            for k in ("srw_500", "srw01", "srw03", "srw06"):
                _set(k, None)

        # ── Composite indices ─────────────────────────────────────────────────
        try:
            if (ml_cape_val is not None and lcl_m is not None
                    and srh01_val is not None and shear06_kt is not None):
                lcl_term   = max(0.0, (2000.0 - lcl_m) / 1000.0)
                shear_term = min(shear06_kt / 20.0, 1.5)
                stp = (ml_cape_val / 1500.0) * lcl_term * (srh01_val / 150.0) * shear_term
                if ml_cin_val is not None and ml_cin_val < -50:
                    stp *= (200.0 + ml_cin_val) / 150.0
                _set("stp", max(0.0, stp), fmt="{:.2f}")
            else:
                _set("stp", None)
        except Exception as e:
            log.debug("STP failed: %s", e)
            _set("stp", None)

        try:
            if (mucape_val is not None and srh01_val is not None
                    and shear06_kt is not None):
                scp = (mucape_val / 1000.0) * (srh01_val / 50.0) * (shear06_kt / 20.0)
                _set("scp", max(0.0, scp), fmt="{:.2f}")
            else:
                _set("scp", None)
        except Exception as e:
            log.debug("SCP failed: %s", e)
            _set("scp", None)

        try:
            if sbcape_val is not None and srh01_val is not None:
                _set("ehi", (sbcape_val * srh01_val) / 160000.0, fmt="{:.2f}")
            else:
                _set("ehi", None)
        except Exception as e:
            log.debug("EHI failed: %s", e)
            _set("ehi", None)
