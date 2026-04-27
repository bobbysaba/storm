
import logging

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.transforms import blended_transform_factory
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QWidget, QGridLayout, QSizePolicy, QSlider, QFrame,
    QToolButton,
)
from PyQt6.QtCore import Qt

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

import metpy.calc as mpcalc
from metpy.plots import SkewT, Hodograph
from metpy.units import units

from config import ACCENT_COLOR as _STORM_CYAN
from core.sounding import Sounding, SoundingSet
from ui.export_tools import copy_widget_png, save_widget_png

log = logging.getLogger(__name__)

from ui.sounding.params import (
    _SCALAR_PARAMS, _convective_temperature_f, _finite_float, _p_at_t,
    _thin_to_mandatory, _threshold_color, _vt_cape_cin,
)
from ui.sounding.theme import (
    _ACCENT, _AX_BG, _BARB_CLR, _BORDER, _DEWP_CLR, _EIL_CLR, _EXPORT_BTN_QSS,
    _FIG_BG, _HDR_CLR, _HODO_LAYERS, _LM_CLR, _MUTED, _PARCEL_CLR, _RM_CLR,
    _SLIDER_QSS, _TEMP_CLR, _TEXT, _VPARCEL_CLR, _VTEMP_CLR, _force_bg, _lbl,
)

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


    def load(self, sset: SoundingSet):
        self._sset = sset
        
        # check if this is an observed or NSSL sounding set
        if sset.is_observed or sset.is_nssl:
            # default to the most recent (last) sounding
            self._cur_idx = len(sset.soundings) - 1
            self.setWindowTitle("NSSL Observed Sounding" if sset.is_nssl else "Observed Sounding")
        else:
            # existing logic for HRRR/Model soundings: default to F0 (Analysis)
            self._cur_idx = next(
                (i for i, s in enumerate(sset.soundings) if s.slot_offset == 0), 0
            )
            self.setWindowTitle("HRRR Point Sounding")

        self._rebuild_scrubber()
        self._draw()
        if not self.isVisible():
            self.show()
        self.raise_()


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

    def _save_png(self):
        save_widget_png(self, "storm_sounding", "Save Sounding PNG")

    def _copy_png(self):
        copy_widget_png(self, "storm_sounding")


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

        # for obs/nssl: show the day number if any two soundings share the same hour.
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
            lat = snd.lat if (snd.lat != 0.0 or snd.lon != 0.0) else self._sset.lat
            lon = snd.lon if (snd.lat != 0.0 or snd.lon != 0.0) else self._sset.lon
            self._header_line1.setText("NSSL  ·  DL Truck")
            location = ""
            if lat != 0.0 or lon != 0.0:
                location = (
                    f"  ·  {abs(lat):.3f}°{'N' if lat >= 0 else 'S'}  "
                    f"{abs(lon):.3f}°{'E' if lon >= 0 else 'W'}"
                )
            self._header_line2.setText(
                f"Valid {valid_str}{location}  ·  {self._sset.elevation:.0f} m MSL"
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

        # index set for plotted traces.  High-resolution soundings (>200 levels,
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

        # dgz shading
        p_neg10 = _p_at_t(snd, -10.0)
        p_neg20 = _p_at_t(snd, -20.0)
        if p_neg10 is not None and p_neg20 is not None:
            ax.axhspan(p_neg10, p_neg20, alpha=0.07, color="#4fc3f7", zorder=1)

        # main traces — thinned for high-res soundings, full-res otherwise
        skewt.plot(pres[_pt], temp[_pt], color=_TEMP_CLR, linewidth=2, zorder=5)
        skewt.plot(pres[_pt], dewp[_pt], color=_DEWP_CLR, linewidth=2, zorder=5)

        # environment virtual temperature (dashed, warm tint).
        mr_env = tv_env = None
        try:
            mr_env = mpcalc.saturation_mixing_ratio(pres, dewp)
            tv_env = mpcalc.virtual_temperature(temp, mr_env).to("degC")
            skewt.plot(pres[_pt], tv_env[_pt], color=_VTEMP_CLR, linewidth=1.1,
                       linestyle="--", alpha=0.6, zorder=5)
        except Exception as e:
            log.debug("virtual temperature plot failed: %s", e)

        # wind barbs at mandatory pressure levels only
        thin = _thin_to_mandatory(snd.pressure)
        skewt.plot_barbs(pres[thin], u_kt[thin], v_kt[thin], color=_BARB_CLR,
                         linewidth=0.8, length=6)

        # parcel profile (white dashed) + virtual-temperature parcel (red dashed).
        sb_parcel = None
        try:
            sb_parcel = mpcalc.parcel_profile(pres, temp[0], dewp[0])
            skewt.plot(pres[_pt], sb_parcel[_pt], color=_PARCEL_CLR, linewidth=1,
                       linestyle="--", alpha=0.4, zorder=5)
        except Exception as e:
            log.debug("parcel profile failed: %s", e)

        if sb_parcel is not None:
            _vt_shaded = False
            try:
                # virtual parcel: below LCL mixing ratio is constant (= surface value);
                _r0      = mpcalc.saturation_mixing_ratio(pres[0:1], dewp[0:1])
                _r_sat_p = mpcalc.saturation_mixing_ratio(pres, sb_parcel.to("degC"))
                _lcl_p, _ = mpcalc.lcl(pres[0], temp[0], dewp[0])
                _below = pres.m >= float(_lcl_p.to("hPa").m)
                _r_prc = (np.where(_below, float(_r0.to("kg/kg").m),
                                   _r_sat_p.to("kg/kg").m)) * units("kg/kg")
                sb_parcel_vt = mpcalc.virtual_temperature(sb_parcel, _r_prc).to("degC")
                skewt.plot(pres[_pt], sb_parcel_vt[_pt], color=_VPARCEL_CLR,
                           linewidth=1.1, linestyle="--", alpha=0.75, zorder=5)
                if tv_env is not None:
                    skewt.shade_cape(pres, tv_env, sb_parcel_vt, alpha=0.18, color=_TEMP_CLR)
                    skewt.shade_cin(pres, tv_env, sb_parcel_vt, alpha=0.18, color="#4fc3f7")
                    _vt_shaded = True
            except Exception as e:
                log.debug("virtual parcel profile failed: %s", e)
            if not _vt_shaded:
                # fall back to non-virtual shading
                try:
                    skewt.shade_cape(pres, temp, sb_parcel, alpha=0.18, color=_TEMP_CLR)
                    skewt.shade_cin(pres, temp, sb_parcel, alpha=0.18, color="#4fc3f7")
                except Exception as e:
                    log.debug("CAPE-CIN shading failed: %s", e)

        # lcl marker
        try:
            lcl_p, lcl_t = mpcalc.lcl(pres[0], temp[0], dewp[0])
            skewt.plot(lcl_p, lcl_t, "o", color="cyan", markersize=5,
                       markerfacecolor="none", markeredgewidth=1.5, zorder=6)
        except Exception as e:
            log.debug("LCL marker failed: %s", e)

        # surface T/Td labels (SHARPpy style) — positioned just below trace endpoints
        sfc_t = float(snd.temperature[0])
        sfc_td = float(snd.dewpoint[0])
        sfc_p = float(snd.pressure[0])

        # define t/td in F
        sfc_t_F = sfc_t * 1.8 + 32
        sfc_td_F = sfc_td * 1.8 + 32

        ax.text(sfc_t, sfc_p + 18, f"{sfc_t_F:.0f}",
                color=_TEMP_CLR, fontsize=8, fontweight="bold",
                ha="center", va="top", zorder=7)
        ax.text(sfc_td, sfc_p + 18, f"{sfc_td_F:.0f}",
                color=_DEWP_CLR, fontsize=8, fontweight="bold",
                ha="center", va="top", zorder=7)

        ax.set_ylim(1050, 100)
        ax.set_xlim(-40, 50)

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

        # eil bracket on left spine
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


    def _draw_hodograph_inset(self, snd: Sounding, ax):
        ax.set_facecolor(_AX_BG + "dd")
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.tick_params(left=False, bottom=False,
                       labelleft=False, labelbottom=False)

        h = Hodograph(ax, component_range=60)
        h.add_grid(increment=20, color=_BORDER, alpha=0.7, linewidth=0.6)

        for spd in (20, 40, 60):
            ax.text(spd * -0.72, spd * -0.72, f"{spd}", fontsize=5,
                    color=_MUTED, ha="center", va="center", alpha=0.7)

        # use only levels that have actual wind observations — skipping T/Td-only
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

        # gapless height-colored trace via LineCollection
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
                LineCollection(segs, colors=seg_colors, linewidth=2.7, zorder=4)
            )

            # eil highlight
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

        # height labels
        for km in (1, 3, 6):
            i = int(np.argmin(np.abs(hgt_agl_km - km)))
            if i < len(u_kt):
                ax.annotate(f"{km}", xy=(u_kt[i], v_kt[i]),
                            fontsize=5, color=_TEXT, alpha=0.85,
                            xytext=(3, 3), textcoords="offset points")

        # bunkers storm motion — colored dot markers + corner text
        try:
            wind_valid = (
                np.isfinite(snd.pressure)
                & np.isfinite(snd.height)
                & np.isfinite(snd.u_wind)
                & np.isfinite(snd.v_wind)
            )
            if wind_valid.sum() < 4:
                raise ValueError("not enough finite wind levels for Bunkers motion")
            pres = snd.pressure[wind_valid] * units.hPa
            u_ms = snd.u_wind[wind_valid]   * units("m/s")
            v_ms = snd.v_wind[wind_valid]   * units("m/s")
            hgt  = snd.height[wind_valid]   * units.m
            rm, lm, _ = mpcalc.bunkers_storm_motion(pres, u_ms, v_ms, hgt)

            rm_u_kt = float(rm[0].to("knots").m)
            rm_v_kt = float(rm[1].to("knots").m)
            lm_u_kt = float(lm[0].to("knots").m)
            lm_v_kt = float(lm[1].to("knots").m)
            if not all(np.isfinite(x) for x in (rm_u_kt, rm_v_kt, lm_u_kt, lm_v_kt)):
                raise ValueError("Bunkers motion returned non-finite values")

            rm_spd = float(np.sqrt(rm_u_kt**2 + rm_v_kt**2))
            rm_dir = float((np.degrees(np.arctan2(rm[0].to("m/s").m, rm[1].to("m/s").m)) + 180) % 360)
            lm_spd = float(np.sqrt(lm_u_kt**2 + lm_v_kt**2))
            lm_dir = float((np.degrees(np.arctan2(lm[0].to("m/s").m, lm[1].to("m/s").m)) + 180) % 360)

            ax.plot(rm_u_kt, rm_v_kt, "o", color=_RM_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)
            ax.plot(lm_u_kt, lm_v_kt, "o", color=_LM_CLR, markersize=5,
                    markeredgecolor=_FIG_BG, markeredgewidth=0.8, zorder=7)

            # dir/spd text in top-left corner of hodograph
            ax.text(0.03, 0.97, f"RM  {rm_dir:.0f}°/{rm_spd:.0f}kt",
                    transform=ax.transAxes, color=_RM_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)
            ax.text(0.03, 0.90, f"LM  {lm_dir:.0f}°/{lm_spd:.0f}kt",
                    transform=ax.transAxes, color=_LM_CLR,
                    fontsize=5.5, fontweight="bold", va="top", ha="left", zorder=8)
        except Exception as e:
            log.debug("Bunkers storm motion failed: %s", e)


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


    def _update_params(self, snd: Sounding):
        def _set(key, value, fmt="{:.0f}"):
            lbl = self._param_labels.get(key)
            if lbl is None:
                return
            value = _finite_float(value)
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
            _set("conv_t", _convective_temperature_f(pres, temp, dewp, hgt), fmt="{:.1f}")
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
