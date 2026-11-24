
import logging

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QWidget, QGridLayout, QSizePolicy, QSlider, QFrame,
    QToolButton, QPushButton, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

import metpy.calc as mpcalc
from metpy.units import units

from config import ACCENT_COLOR as _STORM_CYAN
from core import units as display_units
from core.sounding import Sounding, SoundingSet
from ui.export_tools import copy_widget_png, save_widget_png

log = logging.getLogger(__name__)

from ui.sounding.params import (
    _key_params, _SCALAR_PARAMS, _convective_temperature_f, _finite_float,
    _THRESHOLDS, _vt_cape_cin,
)
from ui.sounding.plots import SoundingPlotsMixin
from ui.sounding.theme import (
    _BORDER, _EXPORT_BTN_QSS, _FIG_BG, _HDR_CLR, _MUTED, _SLIDER_QSS, _TEXT, _force_bg, _lbl,
    _threshold_color, _SOURCE_COLORS, _SOURCE_LABELS, _SOURCE_GLYPHS, COMPARISON_SOURCES,
)

_MAX_TICK_LABELS = 8      # above this, only about five evenly spaced tick labels


def _pill_qss(src: str, active: bool) -> str:
    color = _SOURCE_COLORS[src]
    if active:
        return (
            f"QPushButton {{ background-color: {color}22; color: {color}; "
            f"border: 1.5px solid {color}; border-radius: 10px; "
            f"padding: 2px 10px; font-size: 10px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: {color}44; }}"
        )
    return (
        f"QPushButton {{ background-color: transparent; color: {_MUTED}; "
        f"border: 1.5px solid {_BORDER}; border-radius: 10px; "
        f"padding: 2px 10px; font-size: 10px; font-weight: 700; }}"
        f"QPushButton:hover {{ border-color: {color}; color: {color}; }}"
    )


class SoundingDialog(SoundingPlotsMixin, QDialog):

    # emitted when user clicks an inactive source pill; main_window handles the fetch
    source_requested = pyqtSignal(str, dict)   # source_key, metadata

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

        # primary sounding state (original behaviour)
        self._sset: SoundingSet | None = None
        self._cur_idx       = 0
        self._param_labels: dict[str, QLabel] = {}
        self._param_colors: dict[str, str]    = {}
        self._tick_labels:  list[QLabel]       = []
        self._tick_indices: list[int]          = []   # the sounding each tick label names

        self._skewt_ax     = None
        self._current_snd: Sounding | None = None
        self._cursor_hline = None

        self._eil_base_p: float | None = None
        self._eil_top_p:  float | None = None

        # multi-source comparison state
        self._primary_source: str = "hrrr"
        self._sources:  dict[str, SoundingSet] = {}      # source_key → SoundingSet
        self._enabled_sources: set[str] = set()           # sources currently drawn
        self._source_cur_idx:  dict[str, int] = {}        # scrubber index per source
        self._available_sources: dict[str, dict] = {}     # source_key → fetch metadata
        self._pill_widgets: dict[str, QPushButton] = {}
        # comparison row labels: source_key → QLabel (one line per source)
        self._comp_row_labels: dict[str, QLabel] = {}
        # observed storm motion from the archive storm track: (u_ms, v_ms) or None
        self._observed_motion: tuple[float, float] | None = None

        self._build_ui()

    def set_observed_storm_motion(self, motion) -> None:
        """The storm track's mean motion (core.storm_motion.StormMotion), or
        None. Shown on the hodograph as OBS beside Bunkers RM/LM, and used
        for the 'SRH obs' column; the Bunkers-based values are unchanged."""
        value = (motion.u_ms, motion.v_ms) if motion is not None else None
        if value == self._observed_motion:
            return
        self._observed_motion = value
        if self._sset is not None and self.isVisible():
            self._draw()


    def load(self, sset: SoundingSet):
        # reset comparison state on new primary load
        self._sources = {}
        self._enabled_sources = set()
        self._source_cur_idx = {}
        self._available_sources = {}
        self._pill_widgets = {}
        self._clear_comp_strips()
        self._pill_scroll.setVisible(False)

        self._primary_source = sset.source
        self._sources[sset.source] = sset
        self._enabled_sources.add(sset.source)

        self._sset = sset

        # observed, provider, retrieval and UAS sets: soundings at clock times
        if sset.is_time_series:
            # default to the most recent (last) sounding
            self._cur_idx = len(sset.soundings) - 1
            self._source_cur_idx[sset.source] = self._cur_idx
            if sset.is_provider:
                title = f"{_SOURCE_LABELS['provider']} Observed Sounding"
            elif sset.is_retrieval:
                title = f"{sset.label or 'Retrieved'} Profile"
            elif sset.is_uas:
                title = f"{sset.label or 'UAS'} Profile"
            elif sset.source.startswith("source:"):
                title = sset.station_name or "Observed Sounding"
            else:
                title = "Observed Sounding"
            self.setWindowTitle(title)
        else:
            # existing logic for HRRR/Model soundings: default to F0 (Analysis)
            self._cur_idx = next(
                (i for i, s in enumerate(sset.soundings) if s.slot_offset == 0), 0
            )
            self._source_cur_idx[sset.source] = self._cur_idx
            self.setWindowTitle("HRRR Point Sounding")

        self._rebuild_scrubber()
        self._draw()
        if not self.isVisible():
            self.show()
        self.raise_()

    def set_available_sources(self, available: dict):
        """Tell dialog which comparison sources are available to load.

        available = {
            "hrrr": {"lat": ..., "lon": ...},
            "obs":  {"station_id": ..., "name": ..., "lat": ...,
                     "lon": ..., "elev": ..., "distance_km": ...},
            "provider": {},
        }
        Sources not present → pills hidden.  Primary source is never included.
        """
        self._available_sources = dict(available)
        self._rebuild_pill_row()

    def add_source(self, sset: SoundingSet):
        """Add a comparison sounding set to the overlay."""
        src = sset.source
        self._sources[src] = sset
        self._enabled_sources.add(src)
        # pick best matching index: for HRRR secondary snap to F0; otherwise the latest
        if sset.is_time_series:
            self._source_cur_idx[src] = len(sset.soundings) - 1
        else:
            self._source_cur_idx[src] = next(
                (i for i, s in enumerate(sset.soundings) if s.slot_offset == 0), 0
            )
        # update pill to active state
        if src in self._pill_widgets:
            self._pill_widgets[src].setStyleSheet(_pill_qss(src, True))
            self._pill_widgets[src].setText(self._pill_label(src))
        self._draw()

    def _remove_source(self, src: str):
        self._enabled_sources.discard(src)
        if src in self._pill_widgets:
            self._pill_widgets[src].setStyleSheet(_pill_qss(src, False))
        self._clear_comp_strip(src)
        self._draw()

    # ── pill row ────────────────────────────────────────────────────────────────

    def _pill_label(self, src: str) -> str:
        lbl = _SOURCE_LABELS.get(src, src.upper())
        meta = self._available_sources.get(src, {})
        if src == "obs" and "distance_km" in meta:
            lbl = f"{lbl} · {meta['distance_km']:.0f} km"
        return lbl

    def _rebuild_pill_row(self):
        # clear existing pill widgets
        while self._pill_layout.count():
            item = self._pill_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._pill_widgets.clear()

        sources_to_show = [s for s in COMPARISON_SOURCES
                           if s != self._primary_source and s in self._available_sources]
        if not sources_to_show:
            self._pill_scroll.setVisible(False)
            return

        self._pill_scroll.setVisible(True)
        for src in sources_to_show:
            active = src in self._enabled_sources
            btn = QPushButton(self._pill_label(src))
            btn.setStyleSheet(_pill_qss(src, active))
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda checked, s=src: self._on_pill_clicked(s))
            self._pill_layout.addWidget(btn)
            self._pill_widgets[src] = btn
        self._pill_layout.addStretch(1)

    def _on_pill_clicked(self, src: str):
        if src in self._enabled_sources:
            self._remove_source(src)
        elif src in self._sources:
            # already fetched, just re-enable
            self._enabled_sources.add(src)
            if src in self._pill_widgets:
                self._pill_widgets[src].setStyleSheet(_pill_qss(src, True))
            self._draw()
        else:
            # request fetch from main_window
            meta = self._available_sources.get(src, {})
            self.source_requested.emit(src, meta)


    def _build_ui(self):
        _A = Qt.AlignmentFlag.AlignCenter

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(3)

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

        export_row = QHBoxLayout()
        export_row.setSpacing(5)
        export_row.setContentsMargins(0, 0, 0, 0)
        self._btn_save_png = QToolButton()
        self._btn_save_png.setText("SAVE")
        self._btn_save_png.setToolTip("Save this sounding as a PNG")
        self._btn_save_png.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_save_png.clicked.connect(self._save_png)
        self._btn_copy_png = QToolButton()
        self._btn_copy_png.setText("COPY")
        self._btn_copy_png.setToolTip("Copy this sounding PNG to the clipboard")
        self._btn_copy_png.setStyleSheet(_EXPORT_BTN_QSS)
        self._btn_copy_png.clicked.connect(self._copy_png)
        export_row.addWidget(self._btn_save_png)
        export_row.addWidget(self._btn_copy_png)
        header_row.addLayout(export_row)

        root.addLayout(header_row)

        # source comparison pill row (hidden until available_sources is set)
        self._pill_scroll = QScrollArea()
        self._pill_scroll.setFixedHeight(30)
        self._pill_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pill_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pill_scroll.setWidgetResizable(True)
        self._pill_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        pill_inner = QWidget()
        pill_inner.setAutoFillBackground(False)
        self._pill_layout = QHBoxLayout(pill_inner)
        self._pill_layout.setContentsMargins(4, 2, 4, 2)
        self._pill_layout.setSpacing(6)
        self._pill_layout.addStretch(1)
        self._pill_scroll.setWidget(pill_inner)
        self._pill_scroll.setVisible(False)
        root.addWidget(self._pill_scroll)

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

        self._cursor_label = _lbl("", color=_MUTED, size=10)
        self._cursor_label.setFixedHeight(15)
        root.addWidget(self._cursor_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        root.addWidget(sep)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.setContentsMargins(4, 2, 4, 0)

        # left: parcel table
        parcel_w = QWidget()
        parcel_w.setAutoFillBackground(False)
        pg = QGridLayout(parcel_w)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setHorizontalSpacing(10)
        pg.setVerticalSpacing(1)

        # parcel table header row
        pg.addWidget(_lbl("", color=_HDR_CLR, size=9, bold=True), 0, 0)
        for c, hdr in enumerate(["CAPE", "CIN", "LCL", "LFC", "EL"]):
            pg.addWidget(_lbl(hdr, color=_HDR_CLR, size=9, bold=True, align=_A), 0, c + 1)

        # parcel table data rows
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

        # vertical divider between tables
        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.Shape.VLine)
        vdiv.setFixedWidth(1)
        bottom.addWidget(vdiv)

        # right: kinematics table
        kin_w = QWidget()
        kin_w.setAutoFillBackground(False)
        kg = QGridLayout(kin_w)
        kg.setContentsMargins(0, 0, 0, 0)
        kg.setHorizontalSpacing(10)
        kg.setVerticalSpacing(1)

        kg.addWidget(_lbl("", color=_HDR_CLR, size=9, bold=True), 0, 0)
        for c, hdr in enumerate(["Shear", "SRH", "SRH obs", "SRW"]):
            header = _lbl(hdr, color=_HDR_CLR, size=9, bold=True, align=_A)
            if hdr == "SRH":
                header.setToolTip("SRH using the Bunkers right-mover motion")
            elif hdr == "SRH obs":
                header.setToolTip("SRH using the observed storm motion from the archive storm track")
            kg.addWidget(header, 0, c + 1)

        _kin_rows = [
            ("0-500m", "shear_500", "srh_500", "srh_500_obs", "srw_500"),
            ("0-1km",  "shear01",   "srh01",   "srh01_obs",   "srw01"),
            ("0-3km",  "shear03",   "srh03",   "srh03_obs",   "srw03"),
            ("0-6km",  "shear06",   None,      None,          "srw06"),
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

        # horizontal separator between tables and scalar row
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        root.addWidget(sep2)

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

        # comparison source parameter strips (one per non-primary active source)
        self._comp_strip_widget = QWidget()
        self._comp_strip_widget.setAutoFillBackground(False)
        comp_vl = QVBoxLayout(self._comp_strip_widget)
        comp_vl.setContentsMargins(4, 2, 4, 2)
        comp_vl.setSpacing(2)
        self._comp_strip_layout = comp_vl
        self._comp_strip_widget.setVisible(False)
        root.addWidget(self._comp_strip_widget)

    def _save_png(self):
        save_widget_png(self, "storm_sounding", "Save Sounding PNG")

    def _copy_png(self):
        copy_widget_png(self, "storm_sounding")


    # ── comparison strip helpers ─────────────────────────────────────────────────

    def _clear_comp_strip(self, src: str):
        if src in self._comp_row_labels:
            self._comp_row_labels.pop(src).deleteLater()
        if not self._comp_row_labels:
            self._comp_strip_widget.setVisible(False)

    def _clear_comp_strips(self):
        for lbl in list(self._comp_row_labels.values()):
            lbl.deleteLater()
        self._comp_row_labels.clear()
        self._comp_strip_widget.setVisible(False)

    def _update_comp_strip(self, src: str, params: dict):
        _A = Qt.AlignmentFlag.AlignLeft
        color = _SOURCE_COLORS[src]
        glyph = _SOURCE_GLYPHS[src]
        label_name = _SOURCE_LABELS[src]

        def _fmt(key, fmt="{:.0f}"):
            v = params.get(key)
            return fmt.format(v) if v is not None else "—"

        text = (
            f"{glyph} {label_name}  "
            f"SB {_fmt('sbcape')}  ML {_fmt('mlcape')}  MU {_fmt('mucape')} J/kg  │  "
            f"SRH01 {_fmt('srh01')}  SRH03 {_fmt('srh03')} m²/s²  │  "
            f"SHR06 {_fmt('shear06')} kt  │  "
            f"STP {_fmt('stp', '{:.2f}')}  SCP {_fmt('scp', '{:.2f}')}"
        )

        if src not in self._comp_row_labels:
            lbl = _lbl(text, color=color, size=9)
            self._comp_strip_layout.addWidget(lbl)
            self._comp_row_labels[src] = lbl
        else:
            self._comp_row_labels[src].setText(text)

        self._comp_strip_widget.setVisible(True)

    # ── scrubber ─────────────────────────────────────────────────────────────────

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

        # for clock-time sets: show the day number when they span two days.
        time_based = self._sset.is_time_series
        needs_day = time_based and len({s.valid_time.date() for s in self._sset.soundings}) > 1

        # a retrieval (NSSL: TROPoe) comes every few minutes and the row is narrow:
        # past _MAX_TICK_LABELS only spaced labels are built (the header has
        # the exact time)
        if count <= _MAX_TICK_LABELS:
            self._tick_indices = list(range(count))
        else:
            step = -(-(count - 1) // 4)
            self._tick_indices = list(range(0, count - 1, step)) + [count - 1]

        self._tick_row.addStretch(1)
        for n, i in enumerate(self._tick_indices):
            snd = self._sset.soundings[i]
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
            if n < len(self._tick_indices) - 1:
                self._tick_row.addStretch(2)
        self._tick_row.addStretch(1)

        self._update_header()

    def _on_scrubber_changed(self, idx: int):
        self._cur_idx = idx
        self._source_cur_idx[self._primary_source] = idx
        self._update_header()
        self._draw()

    def _update_header(self):
        if not self._sset or not self._sset.soundings:
            return
        snd = self._sset.soundings[self._cur_idx]
        if not snd:
            return

        if self._sset.is_provider:
            valid_str = snd.valid_time.strftime("%H%MZ %d %b %Y")
            # a launch placed (or found unplaceable) keeps its own position
            own = snd.location_source is not None or snd.lat != 0.0 or snd.lon != 0.0
            lat = snd.lat if own else self._sset.lat
            lon = snd.lon if own else self._sset.lon
            self._header_line1.setText(f"{_SOURCE_LABELS['provider']}  ·  {self._sset.station_id}"
                                       if self._sset.station_id else _SOURCE_LABELS["provider"])
            location = ""
            if lat != 0.0 or lon != 0.0:
                location = (
                    f"  ·  {abs(lat):.3f}°{'N' if lat >= 0 else 'S'}  "
                    f"{abs(lon):.3f}°{'E' if lon >= 0 else 'W'}"
                )
            elif snd.location_source == "unknown":
                location = "  ·  launch position unknown"
            self._header_line2.setText(
                f"Valid {valid_str}{location}  ·  {self._sset.elevation:.0f} m MSL"
            )
        elif self._sset.is_retrieval:
            valid_str = snd.valid_time.strftime("%H%MZ %d %b %Y")
            self._header_line1.setText(self._sset.station_name or self._sset.label or "Retrieval")
            self._header_line2.setText(
                f"Valid {valid_str}  ·  {self._sset.elevation:.0f} m MSL  ·  "
                "retrieval, no wind"
            )
        elif self._sset.is_uas:
            valid_str = snd.valid_time.strftime("%H%MZ %d %b %Y")
            self._header_line1.setText(f"{self._sset.label or 'UAS'}  ·  {self._sset.station_id}")
            self._header_line2.setText(
                f"Valid {valid_str}  ·  "
                f"{abs(snd.lat):.3f}°{'N' if snd.lat >= 0 else 'S'}  "
                f"{abs(snd.lon):.3f}°{'E' if snd.lon >= 0 else 'W'}  ·  "
                f"{float(snd.height[0]) if snd.height.size else 0.0:.0f} m MSL"
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

        for i, lbl in zip(self._tick_indices, self._tick_labels):
            active = (i == self._cur_idx)
            lbl.setStyleSheet(
                "background-color: transparent; font-size: 10px; "
                + (f"color: {_STORM_CYAN}; font-weight: bold;" if active
                   else f"color: {_MUTED};")
            )


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

        # gather secondary sondes (all enabled non-primary sources)
        secondary_snds: list[tuple] = []
        for src in COMPARISON_SOURCES:
            if src == self._primary_source or src not in self._enabled_sources:
                continue
            sset = self._sources.get(src)
            if sset and sset.soundings:
                idx = self._source_cur_idx.get(src, 0)
                sec_snd = sset.soundings[min(idx, len(sset.soundings) - 1)]
                secondary_snds.append((sec_snd, src))

        self._fig.clear()
        self._cursor_hline = None
        self._skewt_ax     = None

        self._draw_skewt(snd, secondary_snds)
        self._update_params(snd, secondary_snds)
        self._canvas.draw_idle()


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


    def _update_params(self, snd: Sounding, secondary_snds: list | None = None):
        def _set(key, value, fmt="{:.0f}"):
            lbl = self._param_labels.get(key)
            if lbl is None:
                return
            value = _finite_float(value)
            lbl.setText(fmt.format(value) if value is not None else "—")
            thr = _threshold_color(_THRESHOLDS, key, value) if value is not None else None
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
        wind_valid = (
            np.isfinite(snd.pressure)
            & np.isfinite(snd.height)
            & np.isfinite(snd.u_wind)
            & np.isfinite(snd.v_wind)
        )
        wind_pres = snd.pressure[wind_valid] * units.hPa
        wind_u_ms = snd.u_wind[wind_valid] * units("m/s")
        wind_v_ms = snd.v_wind[wind_valid] * units("m/s")
        wind_hgt = snd.height[wind_valid] * units.m
        wind_hgt_agl = (snd.height[wind_valid] - snd.height[0]) * units.m

        sb_parcel = sbcape_val = None
        try:
            sb_parcel = mpcalc.parcel_profile(pres, temp[0], dewp[0])
            sbcape_val, sbcin_val = _vt_cape_cin(pres, temp, dewp, sb_parcel,
                                                  pres[0:1], temp[0:1], dewp[0:1])
            _set("sbcape", sbcape_val)
            _set("sbcin",  sbcin_val)
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

        ml_cape_val = ml_cin_val = None
        ml_p = ml_t = ml_td = ml_parcel = None
        try:
            ml_p, ml_t, ml_td = mpcalc.mixed_parcel(
                pres, temp, dewp, depth=100 * units.hPa
            )
            ml_parcel = mpcalc.parcel_profile(pres, ml_t, ml_td)
            ml_cape_val, ml_cin_val = _vt_cape_cin(pres, temp, dewp, ml_parcel,
                                                    ml_p.reshape(1) if hasattr(ml_p, "reshape") else ml_p,
                                                    ml_t.reshape(1) if hasattr(ml_t, "reshape") else ml_t,
                                                    ml_td.reshape(1) if hasattr(ml_td, "reshape") else ml_td)
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

        mucape_val = None
        mu_p = mu_t = mu_td = mu_parcel = None
        try:
            mu_result = mpcalc.most_unstable_parcel(
                pres, temp, dewp, depth=300 * units.hPa
            )
            mu_p, mu_t, mu_td = mu_result[0], mu_result[1], mu_result[2]
            mu_parcel = mpcalc.parcel_profile(pres, mu_t, mu_td)
            mucape_val, mucin_val = _vt_cape_cin(pres, temp, dewp, mu_parcel,
                                                  mu_p.reshape(1) if hasattr(mu_p, "reshape") else mu_p,
                                                  mu_t.reshape(1) if hasattr(mu_t, "reshape") else mu_t,
                                                  mu_td.reshape(1) if hasattr(mu_td, "reshape") else mu_td)
            _set("mucape", mucape_val)
            _set("mucin",  mucin_val)
        except Exception as e:
            log.debug("MU parcel failed: %s", e)
            # fallback for mucape only
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

        try:
            the = mpcalc.equivalent_potential_temperature(pres[0], temp[0], dewp[0])
            _set("sfc_the", float(the.to("K").m), fmt="{:.0f}")
        except Exception as e:
            log.debug("SFC θe failed: %s", e)
            _set("sfc_the", None)

        try:
            _set("pw", float(mpcalc.precipitable_water(pres, dewp).to("mm").m))
        except Exception as e:
            log.debug("precipitable water failed: %s", e)
            _set("pw", None)

        # temperature the surface must reach for parcels to initiate convection.
        try:
            conv_f = _convective_temperature_f(pres, temp, dewp, hgt)
            _set("conv_t", None if conv_f is None else display_units.temp((conv_f - 32) * 5 / 9), fmt="{:.1f}")
        except Exception as e:
            log.debug("convective temperature failed: %s", e)
            _set("conv_t", None)

        srh01_val = None
        rm_u_ms_f = rm_v_ms_f = None
        try:
            if wind_valid.sum() < 4:
                raise ValueError("not enough finite wind levels for storm motion")
            rm, lm, _ = mpcalc.bunkers_storm_motion(
                wind_pres, wind_u_ms, wind_v_ms, wind_hgt
            )
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
                wind_hgt_agl, wind_u_ms, wind_v_ms, depth=1 * units.km,
                storm_u=rm_u, storm_v=rm_v,
            )
            srh03, _, _ = mpcalc.storm_relative_helicity(
                wind_hgt_agl, wind_u_ms, wind_v_ms, depth=3 * units.km,
                storm_u=rm_u, storm_v=rm_v,
            )
            srh_500, _, _ = mpcalc.storm_relative_helicity(
                wind_hgt_agl, wind_u_ms, wind_v_ms, depth=500 * units.m,
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

        # the same helicities relative to the observed (storm track) motion
        for k in ("srh_500_obs", "srh01_obs", "srh03_obs"):
            _set(k, None)
        if self._observed_motion is not None:
            try:
                if wind_valid.sum() < 4:
                    raise ValueError("not enough finite wind levels")
                obs_u = self._observed_motion[0] * units("m/s")
                obs_v = self._observed_motion[1] * units("m/s")
                for key, depth in (("srh_500_obs", 500 * units.m), ("srh01_obs", 1 * units.km),
                                   ("srh03_obs", 3 * units.km)):
                    srh, _, _ = mpcalc.storm_relative_helicity(
                        wind_hgt_agl, wind_u_ms, wind_v_ms, depth=depth, storm_u=obs_u, storm_v=obs_v)
                    _set(key, float(srh.to("m**2/s**2").m))
            except Exception as e:
                log.debug("observed-motion SRH failed: %s", e)

        shear06_kt = None
        for depth, key in [
            (500  * units.m,  "shear_500"),
            (1    * units.km, "shear01"),
            (3    * units.km, "shear03"),
            (6    * units.km, "shear06"),
        ]:
            try:
                if wind_valid.sum() < 2:
                    raise ValueError("not enough finite wind levels for bulk shear")
                us, vs = mpcalc.bulk_shear(
                    wind_pres, wind_u_ms, wind_v_ms, height=wind_hgt_agl, depth=depth,
                )
                val = float(np.sqrt(us**2 + vs**2).to("knots").m)
                _set(key, val)
                if key == "shear06":
                    shear06_kt = val
            except Exception as e:
                log.debug("bulk shear %s failed: %s", key, e)
                _set(key, None)

        hgt_agl_m = snd.height - snd.height[0]
        if rm_u_ms_f is not None:
            for depth_m, key in [
                (500,  "srw_500"),
                (1000, "srw01"),
                (3000, "srw03"),
                (6000, "srw06"),
            ]:
                try:
                    mask = wind_valid & (hgt_agl_m <= depth_m)
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

        try:
            z_sfc = snd.height[0]
            cap_3km = z_sfc + 3000.0
            mask_3 = snd.height <= cap_3km
            if sb_parcel is not None and mask_3.sum() >= 2:
                p3  = pres[mask_3]
                t3  = temp[mask_3]
                d3  = dewp[mask_3]
                sb3 = sb_parcel[mask_3]
                cape3, _ = mpcalc.cape_cin(p3, t3, d3, sb3)
                _set("cape3km", float(cape3.to("J/kg").m))
            else:
                _set("cape3km", None)
        except Exception as e:
            log.debug("0-3km CAPE failed: %s", e)
            _set("cape3km", None)

        # update comparison strips for any active secondary sources
        active_sec_keys = {s for _, s in (secondary_snds or [])}
        # remove strips for sources no longer active
        for src in list(self._comp_row_labels.keys()):
            if src not in active_sec_keys:
                self._clear_comp_strip(src)
        # add/update strips for active secondary sources
        for sec_snd, src_key in (secondary_snds or []):
            try:
                key_params = _key_params(sec_snd)
                self._update_comp_strip(src_key, key_params)
            except Exception as e:
                log.debug("comp strip update failed for %s: %s", src_key, e)
