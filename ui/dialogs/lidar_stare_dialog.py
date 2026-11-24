"""A lidar's fixed-point (vertical) stares in their own window: vertical
velocity (or intensity, or backscatter) against time and height for the
session, with the same intensity threshold, colour range, readout and zoom
as the scan window (lidar_scan_dialog.py), the cloud base the files record,
and a dashed yellow line at the archive clock (the cloud base in green).

Rays are averaged into time columns as wide as the view needs (at most
MAX_COLUMNS across it, never narrower than the lidar's own ray spacing), so
a day of one-second rays draws quickly and zooming in brings the single
rays back. Gates under the threshold are left out before averaging. Time
with no rays stays blank: on a CSM day the lidar looks straight up for one
ray between conical scans, which shows as thin columns.

The data comes from the provider (NSSL: providers/nssl/archive/
lidar_stare_archive_fetcher.py); the main window loads a lidar when it is
picked here (ui/app/main_window_lidar.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from matplotlib import dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QSlider,
                             QToolButton, QVBoxLayout)

from ui.dialogs.lidar_scan_dialog import _AX_BG, _DIM, _FIG_BG, _GRID, _TEXT, INTENSITY_MAX_STEPS, INTENSITY_STEP

MAX_COLUMNS = 1500
_CHUNK_RAYS = 5000
DEFAULT_THRESHOLD_STEPS = 10           # 1.010: noise above the boundary layer hides most of a stare
_EPOCH_NUM = mdates.date2num(datetime(1970, 1, 1, tzinfo=timezone.utc))


def to_num(epoch):
    """Epoch seconds -> matplotlib date numbers."""
    return _EPOCH_NUM + np.asarray(epoch, dtype=float) / 86400.0


def to_epoch(num):
    return (np.asarray(num, dtype=float) - _EPOCH_NUM) * 86400.0


def ray_spacing(times: np.ndarray) -> float:
    """The lidar's usual time between rays (s)."""
    steps = np.diff(times)
    steps = steps[steps > 0]
    return float(np.median(steps)) if steps.size else 1.0


def column_width(times: np.ndarray, t0: float, t1: float) -> float:
    """Twice the ray spacing at least: a stare's rays come every 1-2 s, and
    narrower columns would leave empty ones between them."""
    return max(2 * ray_spacing(times), (t1 - t0) / MAX_COLUMNS)


def masked_field(stare, field: str, min_intensity: float | None, rays=slice(None)) -> np.ma.MaskedArray:
    """The field for these rays, masked where missing and where the
    intensity is below min_intensity."""
    data = np.ma.masked_invalid(np.asarray(stare.fields[field]["data"][rays], dtype=np.float32))
    if min_intensity is not None and "intensity" in stare.fields:
        intensity = np.asarray(stare.fields["intensity"]["data"][rays])
        with np.errstate(invalid="ignore"):
            data = np.ma.masked_where(~(intensity >= min_intensity), data)
    return data


def binned(stare, field: str, min_intensity: float | None, t0: float, t1: float, width: float):
    """(column edges in epoch s, (column, gate) masked means) for the rays
    in [t0, t1); a column with no unmasked gate is masked."""
    n = max(1, int(np.ceil((t1 - t0) / width)))
    edges = t0 + width * np.arange(n + 1)
    rays = np.flatnonzero((stare.time_epoch >= t0) & (stare.time_epoch < edges[-1]))
    sums = np.zeros((n, stare.height_m.size))
    counts = np.zeros_like(sums)
    for chunk in np.array_split(rays, max(1, rays.size // _CHUNK_RAYS)) if rays.size else ():
        # a chunk at a time: a day of one-second rays in float64 at once is
        # hundreds of MB
        data = masked_field(stare, field, min_intensity, chunk)
        column = np.minimum(((stare.time_epoch[chunk] - t0) // width).astype(int), n - 1)
        np.add.at(sums, column, data.filled(0.0))
        np.add.at(counts, column, (~np.ma.getmaskarray(data)).astype(float))
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums / counts
    return edges, np.ma.masked_where(counts == 0, means)


def default_range(field: str, data: np.ma.MaskedArray) -> tuple[float, float]:
    """±3 m/s for vertical velocity, else the 2nd to 98th percentile."""
    if field == "w":
        return -3.0, 3.0
    finite = np.ma.compressed(data)
    lo, hi = (np.percentile(finite, [2, 98]) if finite.size else (0.0, 1.0))
    return float(lo), float(hi if hi > lo else lo + 1)


def usable_top_m(stare, threshold: float = 1.01) -> float:
    """The height the view starts at: a little above the highest gate where
    at least 5% of rays clear the threshold (1 km at least)."""
    top = float(np.nanmax(stare.height_m)) if stare.height_m.size else 1000.0
    if "intensity" not in stare.fields or not len(stare):
        return top
    with np.errstate(invalid="ignore"):
        share = np.mean(np.asarray(stare.fields["intensity"]["data"]) >= threshold, axis=0)
    good = np.flatnonzero(share >= 0.05)
    if not good.size:
        return min(top, 3000.0)
    return float(min(top, max(1000.0, stare.height_m[good[-1]] * 1.2)))


class LidarStareDialog(QDialog):
    lidar_picked = pyqtSignal(str)          # instrument: the main window loads its stares

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Lidar stares")
        self.resize(1000, 640)
        self.setStyleSheet(f"QDialog {{ background-color: {_FIG_BG}; }} "
                           f"QLabel, QCheckBox {{ color: {_TEXT}; }}")
        self._stare = None
        self._instrument = ""
        self._field = "w"
        self._limits: tuple[float, float] | None = None
        self._span: tuple[float, float] | None = None    # the session, epoch s
        self._clock: float | None = None
        self._width = 1.0
        self._drawing = False
        self._colorbar = None
        self._build_ui()

    # ── layout ─────────────────────────────────────────────
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        self._title = QLabel("")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        self._subtitle.setWordWrap(True)
        v.addWidget(self._title)
        v.addWidget(self._subtitle)

        row = QHBoxLayout()
        self._lidar_combo = QComboBox()
        self._lidar_combo.setToolTip("The lidars with stares this session")
        self._lidar_combo.currentIndexChanged.connect(self._on_lidar_changed)
        row.addWidget(self._lidar_combo)
        self._field_combo = QComboBox()
        self._field_combo.setToolTip("The field shown")
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        row.addWidget(self._field_combo)
        row.addWidget(QLabel("min intensity"))
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._intensity_slider.setRange(0, INTENSITY_MAX_STEPS)
        self._intensity_slider.setFixedWidth(140)
        self._intensity_slider.setToolTip(
            "Hide gates whose intensity (SNR + 1) is below this: the noise above the aerosol layer. "
            "Noise sits near 1.000; about 1.005–1.02 usually clears it.")
        self._intensity_value = QLabel("")
        self._intensity_value.setMinimumWidth(40)
        self._intensity_slider.setValue(DEFAULT_THRESHOLD_STEPS)
        self._intensity_value.setText(f"{1.0 + DEFAULT_THRESHOLD_STEPS * INTENSITY_STEP:.3f}")
        self._intensity_slider.valueChanged.connect(self._on_intensity_moved)
        row.addWidget(self._intensity_slider)
        row.addWidget(self._intensity_value)
        row.addWidget(QLabel("colours"))
        self._lo_box, self._hi_box = QDoubleSpinBox(), QDoubleSpinBox()
        for box, tip in ((self._lo_box, "The colour scale's low end"), (self._hi_box, "Its high end")):
            box.setRange(-1e6, 1e6)
            box.setDecimals(2)
            box.setFixedWidth(80)
            box.setToolTip(tip)
            box.editingFinished.connect(self._on_colour_boxes)
            row.addWidget(box)
        self._reset_colours = QToolButton()
        self._reset_colours.setText("RESET COLOURS")
        self._reset_colours.setToolTip("Back to the field's usual colour range")
        self._reset_colours.clicked.connect(self._on_reset_colours)
        row.addWidget(self._reset_colours)
        row.addStretch(1)
        self._chk_cloud = QCheckBox("cloud base")
        self._chk_cloud.setChecked(True)
        self._chk_cloud.setToolTip("The cloud-base height the files record (from the backscatter)")
        self._chk_cloud.toggled.connect(lambda _on: self._draw())
        row.addWidget(self._chk_cloud)
        v.addLayout(row)

        self._fig = Figure(facecolor=_FIG_BG)
        self._canvas = FigureCanvas(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self._toolbar.setStyleSheet(f"background-color: {_FIG_BG}; color: {_TEXT};")
        v.addWidget(self._toolbar)
        v.addWidget(self._canvas, 1)
        self._readout = QLabel("")
        self._readout.setStyleSheet(f"color: {_DIM}; font-family: Menlo, monospace; font-size: 11px;")
        v.addWidget(self._readout)
        self._canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._canvas.mpl_connect("scroll_event", self._on_scroll)
        self._canvas.mpl_connect("button_press_event", self._on_press)
        self._intensity_timer = QTimer(self)
        self._intensity_timer.setSingleShot(True)
        self._intensity_timer.setInterval(150)
        self._intensity_timer.timeout.connect(self._draw)
        # zooming or panning re-averages the columns for the new view
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(200)
        self._view_timer.timeout.connect(self._draw)

    # ── what to show ───────────────────────────────────────
    def set_lidars(self, lidars: list[tuple[str, str]], current: str | None = None) -> None:
        """(instrument, name) of each lidar with stares; picks *current*
        (else the first) and asks for its stares with lidar_picked."""
        self._lidar_combo.blockSignals(True)
        self._lidar_combo.clear()
        for instrument, name in lidars:
            self._lidar_combo.addItem(name, instrument)
        index = max(0, self._lidar_combo.findData(current)) if current else 0
        self._lidar_combo.setCurrentIndex(index)
        self._lidar_combo.blockSignals(False)
        self._on_lidar_changed(index)

    def current_instrument(self) -> str:
        return self._lidar_combo.currentData() or ""

    def set_span(self, start: datetime, end: datetime) -> None:
        """The session's times: the view opens on them."""
        self._span = (start.timestamp(), end.timestamp())

    def set_loading(self, instrument: str, text: str = "Loading its stares…") -> None:
        """While the lidar's stares load; *text* says how far its download
        has got."""
        if instrument == self.current_instrument():
            self._show_message(f"{self._lidar_combo.currentText()} · stares", text)

    def set_failed(self, instrument: str, reason: str) -> None:
        if instrument == self.current_instrument():
            self._show_message(f"{self._lidar_combo.currentText()} · stares", f"No stares to show: {reason}")

    def set_stare(self, instrument: str, stare) -> None:
        """The lidar's stares (LidarStare), if it's still the one picked."""
        if instrument != self.current_instrument():
            return
        self._stare = stare
        fields = list(stare.fields)
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for name in fields:
            info = stare.fields[name]
            self._field_combo.addItem(f"{info.get('label', name)} ({info.get('units', '')})", name)
        self._field = self._field if self._field in fields else "w"
        self._field_combo.setCurrentIndex(max(0, self._field_combo.findData(self._field)))
        self._field_combo.blockSignals(False)
        self._intensity_slider.setEnabled("intensity" in fields)
        self._limits = None
        self._draw(reset_view=True)

    def set_time(self, when: datetime) -> None:
        """The archive clock: a dashed yellow line across the plot."""
        self._clock = when.timestamp()
        line = getattr(self, "_clock_line", None)
        if line is not None and line.axes is self._ax:
            line.set_xdata([to_num(self._clock)] * 2)
            self._canvas.draw_idle()

    def min_intensity(self) -> float | None:
        steps = self._intensity_slider.value()
        if not steps or not self._intensity_slider.isEnabled():
            return None
        return 1.0 + steps * INTENSITY_STEP

    def _show_message(self, title: str, text: str) -> None:
        self._stare = None
        self._title.setText(title)
        self._subtitle.setText("")
        self._ax.clear()
        self._ax.set_facecolor(_AX_BG)
        self._ax.set_xticks([])
        self._ax.set_yticks([])
        self._ax.text(0.5, 0.5, text, color=_DIM, ha="center", va="center", transform=self._ax.transAxes)
        self._canvas.draw_idle()

    # ── drawing ────────────────────────────────────────────
    def _view(self, reset: bool) -> tuple[float, float, float, float]:
        """(t0, t1, bottom m, top m) to draw."""
        stare = self._stare
        first, last = float(stare.time_epoch[0]), float(stare.time_epoch[-1]) + ray_spacing(stare.time_epoch)
        if not reset:
            x0, x1 = to_epoch(self._ax.get_xlim())
            y0, y1 = self._ax.get_ylim()
            return float(x0), float(x1), y0 * 1000, y1 * 1000
        t0, t1 = first, last
        if self._span is not None:
            inside = (stare.time_epoch >= self._span[0]) & (stare.time_epoch <= self._span[1])
            if inside.any():
                t0, t1 = max(first, self._span[0]), min(last, self._span[1])
        return t0, t1, 0.0, usable_top_m(stare)

    def _draw(self, reset_view: bool = False) -> None:
        stare = self._stare
        if stare is None or not len(stare) or self._drawing:
            return
        self._drawing = True
        try:
            t0, t1, bottom, top = self._view(reset_view)
            self._width = column_width(stare.time_epoch, t0, t1)
            edges, data = binned(stare, self._field, self.min_intensity(), t0, t1, self._width)
            lo, hi = self._limits or default_range(self._field, data)
            for box, value in ((self._lo_box, lo), (self._hi_box, hi)):
                box.blockSignals(True)
                box.setValue(value)
                box.blockSignals(False)
            self._ax.clear()
            self._ax.set_facecolor(_AX_BG)
            heights = stare.height_m / 1000.0
            half = (heights[1] - heights[0]) / 2 if heights.size > 1 else 0.015
            from matplotlib import colormaps
            cmap = colormaps["RdBu_r"] if self._field == "w" else colormaps["viridis"]
            self._image = self._ax.imshow(
                data.T, origin="lower", aspect="auto", interpolation="nearest", cmap=cmap, vmin=lo, vmax=hi,
                extent=(to_num(edges[0]), to_num(edges[-1]), heights[0] - half, heights[-1] + half))
            if self._colorbar is None:
                self._colorbar = self._fig.colorbar(self._image, ax=self._ax, fraction=0.04, pad=0.02)
                self._colorbar.ax.tick_params(colors=_TEXT, labelsize=8)
            else:
                self._colorbar.update_normal(self._image)
            info = stare.fields[self._field]
            self._colorbar.set_label(f"{info.get('label', self._field)} ({info.get('units', '')})", color=_TEXT)
            if self._chk_cloud.isChecked():
                rays = np.flatnonzero((stare.time_epoch >= t0) & (stare.time_epoch < t1)
                                      & np.isfinite(stare.cloud_base_m))
                rays = rays[::max(1, rays.size // 4000)]
                self._ax.plot(to_num(stare.time_epoch[rays]), stare.cloud_base_m[rays] / 1000.0, ".",
                              color="#39d353", ms=2.5, label="cloud base")
            clock = self._clock if self._clock is not None else t0
            from matplotlib import patheffects
            # outlined: the field around it is pale where w is near zero
            self._clock_line = self._ax.axvline(to_num(clock), color="#ffd166", lw=1.5, ls="--",
                                                path_effects=[patheffects.withStroke(linewidth=3.5,
                                                                                     foreground="#000000")])
            locator = mdates.AutoDateLocator(tz=timezone.utc)
            self._ax.xaxis.set_major_locator(locator)
            self._ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=timezone.utc))
            self._ax.set_xlabel("UTC", color=_TEXT)
            self._ax.set_ylabel("height (km AGL)", color=_TEXT)
            self._ax.tick_params(colors=_TEXT, labelsize=8)
            for spine in self._ax.spines.values():
                spine.set_color(_GRID)
            self._ax.set_xlim(to_num(t0), to_num(t1))
            self._ax.set_ylim(bottom / 1000.0, top / 1000.0)
            if reset_view:
                self._toolbar.update()          # home is this view
            self._ax.callbacks.connect("xlim_changed", self._on_view_changed)
            name = self._lidar_combo.currentText()
            self._title.setText(f"{name} · stares · {datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d}")
            columns = f"{self._width:.0f} s columns" if self._width >= 1.5 else "single rays"
            notes = "  ·  ".join([f"{len(stare)} vertical rays", columns, *stare.warnings])
            self._subtitle.setText(notes)
            self._canvas.draw_idle()
        finally:
            self._drawing = False

    def _on_view_changed(self, _ax) -> None:
        if not self._drawing:
            self._view_timer.start()

    # ── readout, zoom ──────────────────────────────────────
    def readout_at(self, x_num: float, y_km: float) -> str:
        """What the nearest ray recorded at that time and height; "" where
        the lidar took no vertical ray."""
        stare = self._stare
        if stare is None or not len(stare):
            return ""
        when = float(to_epoch(x_num))
        i = int(np.clip(np.searchsorted(stare.time_epoch, when), 1, len(stare) - 1))
        i = i - 1 if abs(stare.time_epoch[i - 1] - when) <= abs(stare.time_epoch[i] - when) else i
        if abs(stare.time_epoch[i] - when) > max(self._width, 2 * ray_spacing(stare.time_epoch)):
            return ""
        gate = int(np.argmin(np.abs(stare.height_m / 1000.0 - y_km)))
        stamp = datetime.fromtimestamp(float(stare.time_epoch[i]), timezone.utc)
        text = f"{stamp:%H:%M:%S}Z  height {stare.height_m[gate]:.0f} m  "
        value = masked_field(stare, self._field, self.min_intensity(), slice(i, i + 1))[0, gate]
        info = stare.fields[self._field]
        text += ("masked" if np.ma.is_masked(value)
                 else f"{info.get('label', self._field).lower()} {float(value):.2f} {info.get('units', '')}")
        if "intensity" in stare.fields:
            text += f"  intensity {float(stare.fields['intensity']['data'][i, gate]):.4f}"
        cloud = stare.cloud_base_m[i]
        text += f"  cloud base {cloud / 1000:.2f} km" if np.isfinite(cloud) else "  no cloud base"
        return text

    def _on_motion(self, event) -> None:
        if event.inaxes is self._ax and event.xdata is not None:
            self._readout.setText(self.readout_at(event.xdata, event.ydata))

    def _on_scroll(self, event) -> None:
        if event.inaxes is not self._ax or event.xdata is None or self._stare is None:
            return
        factor = 0.8 if event.button == "up" else 1.25
        for get, set_, centre in ((self._ax.get_xlim, self._ax.set_xlim, event.xdata),
                                  (self._ax.get_ylim, self._ax.set_ylim, event.ydata)):
            lo, hi = get()
            set_(centre - (centre - lo) * factor, centre + (hi - centre) * factor)
        self._canvas.draw_idle()

    def _on_press(self, event) -> None:
        if event.dblclick and event.inaxes is self._ax and self._stare is not None:
            self._draw(reset_view=True)

    # ── controls ───────────────────────────────────────────
    def _on_lidar_changed(self, _index) -> None:
        instrument = self.current_instrument()
        if instrument and instrument != self._instrument:
            self._instrument = instrument
            self.set_loading(instrument)
            self.lidar_picked.emit(instrument)

    def _on_field_changed(self, _index) -> None:
        self._field = self._field_combo.currentData() or "w"
        self._limits = None
        self._draw()

    def _on_intensity_moved(self, steps: int) -> None:
        self._intensity_value.setText(f"{1.0 + steps * INTENSITY_STEP:.3f}" if steps else "off")
        self._intensity_timer.start()

    def set_colour_range(self, lo: float, hi: float) -> None:
        if hi > lo:
            self._limits = (float(lo), float(hi))
            self._draw()

    def _on_colour_boxes(self) -> None:
        lo, hi = self._lo_box.value(), self._hi_box.value()
        if (lo, hi) != (self._limits or (None, None)) and hi > lo:
            self.set_colour_range(lo, hi)

    def _on_reset_colours(self) -> None:
        self._limits = None
        self._draw()
