"""One lidar's PPI / sector scans in their own window, for looking closely
and quality-checking: the scan in km east and north of the lidar, north up,
with the field, an intensity threshold, the colour range and the scan
picked here, a readout under the cursor, and box zoom, scroll zoom, pan and
double-click to reset. Drawing on the map is an option (off by default):
the main window then draws the scan shown here with this field and
threshold (ui/app/main_window_lidar.py).

Each ray is placed from its own range, azimuth (true north; the truck's
were turned by its heading when read) and elevation, so a scan taken while
the truck moved a little is drawn about where it was measured from.
"""
from __future__ import annotations

import math

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QSlider,
                             QToolButton, QVBoxLayout)

_FIG_BG = "#0a0a0f"
_AX_BG = "#0f0f1a"
_GRID = "#2a2a3a"
_TEXT = "#e8eaf0"
_DIM = "#9a9ab8"
INTENSITY_STEP = 0.001       # the slider's step (the files' SNR + 1)
INTENSITY_MAX_STEPS = 60     # 1.000 .. 1.060


def scan_xy(rays, indices) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(x km east, y km north, height km) of each gate of the scan's rays,
    shaped (ray, gate), relative to the lidar's first position in the scan."""
    idx = np.asarray(indices, dtype=int)
    ranges = np.asarray(rays.distance_m, dtype=float)
    ranges = np.broadcast_to(ranges, (len(idx), ranges.shape[-1])) if ranges.ndim == 1 else ranges[idx]
    az = np.radians(np.asarray(rays.azimuth_deg, dtype=float)[idx])[:, None]
    el = np.radians(np.asarray(rays.elevation_deg, dtype=float)[idx])[:, None]
    ground = ranges * np.cos(el) / 1000.0
    x, y = ground * np.sin(az), ground * np.cos(az)
    # the truck moves: each ray from where it was, against the scan's first fix
    lat = np.asarray(rays.latitude, dtype=float)[idx]
    lon = np.asarray(rays.longitude, dtype=float)[idx]
    if np.isfinite(lat).all() and np.isfinite(lon).all() and len(idx):
        dy = (lat - lat[0]) * 111.32
        dx = (lon - lon[0]) * 111.32 * math.cos(math.radians(float(lat[0])))
        x, y = x + dx[:, None], y + dy[:, None]
    return x, y, ranges * np.sin(el) / 1000.0


def scan_edges(rays, indices) -> tuple[np.ndarray, np.ndarray]:
    """(x, y) km of the scan's cell corners, shaped (ray + 1, gate + 1):
    cells as wide as the azimuth step between rays and as deep as a gate,
    for pcolormesh."""
    idx = np.asarray(indices, dtype=int)
    ranges = np.asarray(rays.distance_m, dtype=float)
    ranges = np.broadcast_to(ranges, (len(idx), ranges.shape[-1])) if ranges.ndim == 1 else ranges[idx]
    r = np.concatenate((np.maximum(0, ranges[:, :1] - np.diff(ranges[:, :2], axis=1) / 2),
                        (ranges[:, 1:] + ranges[:, :-1]) / 2,
                        ranges[:, -1:] + np.diff(ranges[:, -2:], axis=1) / 2), axis=1)
    r = np.vstack((r, r[-1:]))                                 # the last ray's far side
    az = np.unwrap(np.radians(np.asarray(rays.azimuth_deg, dtype=float)[idx]))
    step = np.diff(az)
    first = step[0] if step.size else np.radians(1.0)
    last = step[-1] if step.size else np.radians(1.0)
    a = np.concatenate(([az[0] - first / 2], (az[1:] + az[:-1]) / 2, [az[-1] + last / 2]))[:, None]
    el = np.radians(np.asarray(rays.elevation_deg, dtype=float)[idx])
    el = np.concatenate((el, el[-1:]))[:, None]
    ground = r * np.cos(el) / 1000.0
    x, y = ground * np.sin(a), ground * np.cos(a)
    lat = np.asarray(rays.latitude, dtype=float)[idx]
    lon = np.asarray(rays.longitude, dtype=float)[idx]
    if np.isfinite(lat).all() and np.isfinite(lon).all() and len(idx):
        dy = (lat - lat[0]) * 111.32
        dx = (lon - lon[0]) * 111.32 * math.cos(math.radians(float(lat[0])))
        dx, dy = np.concatenate((dx, dx[-1:])), np.concatenate((dy, dy[-1:]))
        x, y = x + dx[:, None], y + dy[:, None]
    return x, y


def field_data(rays, indices, field: str, min_intensity: float | None) -> np.ma.MaskedArray:
    """The scan's *field*, (ray, gate), masked where it's missing and, with
    *min_intensity*, where the intensity (SNR + 1) is below it."""
    idx = np.asarray(indices, dtype=int)
    data = np.ma.masked_invalid(np.asarray(rays.fields[field]["data"], dtype=float)[idx])
    if min_intensity is not None and "intensity" in rays.fields:
        intensity = np.asarray(rays.fields["intensity"]["data"], dtype=float)[idx]
        with np.errstate(invalid="ignore"):
            data = np.ma.masked_where(~(intensity >= min_intensity), data)
    return data


def default_range(field: str, data: np.ma.MaskedArray) -> tuple[float, float]:
    """The colour range the map uses: ±30 m/s for velocity, else the 2nd to
    98th percentile."""
    if "vel" in field.lower():
        return -30.0, 30.0
    finite = data.compressed()
    lo, hi = (np.percentile(finite, [2, 98]) if finite.size else (0.0, 1.0))
    return float(lo), float(hi if hi > lo else lo + 1)


class LidarScanDialog(QDialog):
    draw_on_map_toggled = pyqtSignal(bool)
    field_changed = pyqtSignal(str)
    min_intensity_changed = pyqtSignal(object)    # float, or None for every cell
    scan_changed = pyqtSignal(object)             # the LidarScan shown

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Lidar scans")
        self.resize(900, 820)
        self.setStyleSheet(f"QDialog {{ background-color: {_FIG_BG}; }} "
                           f"QLabel, QCheckBox {{ color: {_TEXT}; }}")
        self._scans: list = []
        self._index = 0
        self._field = "velocity"
        self._limits: tuple[float, float] | None = None     # None: the field's default
        self._xyz = None
        self._data = None
        self._build_ui()

    # ── layout ─────────────────────────────────────────────
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        self._title = QLabel("")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        v.addWidget(self._title)
        v.addWidget(self._subtitle)

        row = QHBoxLayout()
        self._field_combo = QComboBox()
        self._field_combo.setToolTip("The field shown, as the lidar recorded it")
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        row.addWidget(self._field_combo)
        row.addWidget(QLabel("min intensity"))
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._intensity_slider.setRange(0, INTENSITY_MAX_STEPS)
        self._intensity_slider.setFixedWidth(140)
        self._intensity_slider.setToolTip(
            "Hide gates whose intensity (the file's SNR + 1) is below this: the noise beyond the "
            "aerosol layer. Noise sits near 1.000; about 1.005–1.02 usually clears it.")
        self._intensity_slider.valueChanged.connect(self._on_intensity_moved)
        row.addWidget(self._intensity_slider)
        self._intensity_value = QLabel("off")
        self._intensity_value.setMinimumWidth(40)
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
        self._chk_map = QCheckBox("draw on map")
        self._chk_map.setToolTip("Draw the scan shown here on the map, with this field and threshold")
        self._chk_map.toggled.connect(self.draw_on_map_toggled.emit)
        row.addWidget(self._chk_map)
        v.addLayout(row)

        steps = QHBoxLayout()
        self._prev = QToolButton()
        self._prev.setText("◀")
        self._prev.setToolTip("The scan before")
        self._prev.clicked.connect(lambda: self._step(-1))
        self._next = QToolButton()
        self._next.setText("▶")
        self._next.setToolTip("The scan after")
        self._next.clicked.connect(lambda: self._step(1))
        self._scan_combo = QComboBox()
        self._scan_combo.setToolTip("This lidar's scans from here")
        self._scan_combo.currentIndexChanged.connect(self._on_scan_picked)
        self._chk_follow = QCheckBox("follow the clock")
        self._chk_follow.setChecked(True)
        self._chk_follow.setToolTip("Show the scan at the archive clock as it moves")
        for w in (self._prev, self._scan_combo, self._next, self._chk_follow):
            steps.addWidget(w)
        steps.addStretch(1)
        v.addLayout(steps)

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
        # redraw once the slider settles, not on every step of a drag
        self._intensity_timer = QTimer(self)
        self._intensity_timer.setSingleShot(True)
        self._intensity_timer.setInterval(150)
        self._intensity_timer.timeout.connect(self._on_intensity_settled)

    # ── what to show ───────────────────────────────────────
    def set_location(self, title: str, scans: list, heading_text: str = "", when=None) -> None:
        """A lidar location's scans (core/lidar_scans.LidarScan, in time
        order), showing the one at *when* (the clock), else the first."""
        self._title_text = title
        self._heading_text = heading_text
        self._scans = list(scans)
        fields: dict[str, str] = {}
        for rays in {id(s.source): s.source for s in self._scans}.values():
            for name, field in rays.fields.items():
                fields.setdefault(name, field.get("units", ""))
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for name, units in fields.items():
            label = name.replace("_", " ").capitalize()
            self._field_combo.addItem(f"{label} ({units})" if units else label, name)
        index = self._field_combo.findData(self._field)
        if index < 0:
            index = max(0, self._field_combo.findData("velocity"))
        self._field_combo.setCurrentIndex(index)
        self._field = self._field_combo.currentData() or "velocity"
        self._field_combo.blockSignals(False)
        has_intensity = "intensity" in fields
        self._intensity_slider.setEnabled(has_intensity)
        self._scan_combo.blockSignals(True)
        self._scan_combo.clear()
        for scan in self._scans:
            self._scan_combo.addItem(scan.describe())
        self._scan_combo.blockSignals(False)
        self._index = 0
        if when is not None:
            self._index = self._index_at(when)
        self._show(reset_view=True)

    def set_time(self, when) -> None:
        """The archive clock moved: follow it when asked to."""
        if self._chk_follow.isChecked() and self._scans:
            index = self._index_at(when)
            if index != self._index:
                self._index = index
                self._show()

    def current_scan(self):
        return self._scans[self._index] if self._scans else None

    def min_intensity(self) -> float | None:
        steps = self._intensity_slider.value()
        if not steps or not self._intensity_slider.isEnabled():
            return None
        return 1.0 + steps * INTENSITY_STEP

    def _index_at(self, when) -> int:
        """The last scan started by *when* (the first before any)."""
        index = 0
        for i, scan in enumerate(self._scans):
            if scan.start <= when:
                index = i
        return index

    # ── drawing ────────────────────────────────────────────
    def _show(self, reset_view: bool = False) -> None:
        scan = self.current_scan()
        if scan is None:
            return
        self._scan_combo.blockSignals(True)
        self._scan_combo.setCurrentIndex(self._index)
        self._scan_combo.blockSignals(False)
        self._prev.setEnabled(self._index > 0)
        self._next.setEnabled(self._index < len(self._scans) - 1)
        rays = scan.source
        field = self._field if self._field in rays.fields else next(iter(rays.fields))
        self._xyz = scan_xy(rays, scan.indices)
        self._data = field_data(rays, scan.indices, field, self.min_intensity())
        lo, hi = self._limits or default_range(field, self._data)
        for box, value in ((self._lo_box, lo), (self._hi_box, hi)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        keep = None if reset_view else (self._ax.get_xlim(), self._ax.get_ylim())
        self._ax.clear()
        self._ax.set_facecolor(_AX_BG)
        x, y, _ = self._xyz
        from matplotlib import colormaps
        from ui.map.radar_colormaps import radar_colormaps
        cmap = radar_colormaps()["nws_vel"] if "vel" in field.lower() else colormaps["viridis"]
        ex, ey = scan_edges(rays, scan.indices)
        self._mesh = self._ax.pcolormesh(ex, ey, self._data, cmap=cmap, vmin=lo, vmax=hi, shading="flat")
        if not hasattr(self, "_colorbar"):
            self._colorbar = self._fig.colorbar(self._mesh, ax=self._ax, fraction=0.04, pad=0.02)
            self._colorbar.ax.tick_params(colors=_TEXT, labelsize=8)
        else:
            self._colorbar.update_normal(self._mesh)
        units = rays.fields[field].get("units", "")
        self._colorbar.set_label(f"{field} ({units})" if units else field, color=_TEXT)
        self._rings(float(np.nanmax(np.hypot(x, y))) if x.size else 1.0)
        self._ax.set_aspect("equal")
        self._ax.set_xlabel("km east", color=_TEXT)
        self._ax.set_ylabel("km north", color=_TEXT)
        self._ax.tick_params(colors=_TEXT, labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color(_GRID)
        if keep is not None:
            self._ax.set_xlim(*keep[0])
            self._ax.set_ylim(*keep[1])
        else:
            self._toolbar.update()         # home is this view
        self._title.setText(f"{self._title_text}  ·  {scan.describe()}")
        heading = f"  ·  {self._heading_text}" if self._heading_text else ""
        self._subtitle.setText(f"{scan.start:%Y-%m-%d %H:%M:%S}–{scan.end:%H:%M:%SZ}{heading}")
        self._canvas.draw_idle()
        self.scan_changed.emit(scan)

    def _rings(self, outer_km: float) -> None:
        step = next((s for s in (0.5, 1, 2, 5, 10) if outer_km / s <= 6), 20)
        theta = np.linspace(0, 2 * np.pi, 181)
        r = step
        while r <= outer_km + 1e-9:
            self._ax.plot(r * np.sin(theta), r * np.cos(theta), color=_GRID, lw=0.6, zorder=3)
            self._ax.text(0, r, f"{r:g} km", color=_DIM, fontsize=7, ha="center", va="bottom", zorder=4)
            r += step
        for az in range(0, 360, 45):
            a = math.radians(az)
            self._ax.plot([0, outer_km * math.sin(a)], [0, outer_km * math.cos(a)], color=_GRID, lw=0.5, zorder=3)
        self._ax.plot(0, 0, "+", color=_TEXT, ms=8, zorder=5)

    # ── readout, zoom ──────────────────────────────────────
    def readout_at(self, x_km: float, y_km: float) -> str:
        """What's under (x, y): range, azimuth, height, the field's value and
        the gate's intensity; "" off the scan."""
        scan = self.current_scan()
        if scan is None or self._xyz is None:
            return ""
        x, y, h = self._xyz
        d2 = (x - x_km) ** 2 + (y - y_km) ** 2
        ray, gate = np.unravel_index(int(np.argmin(d2)), d2.shape)
        if d2[ray, gate] > max(0.05, (float(np.hypot(x_km, y_km)) * 0.03) ** 2):
            return ""
        rays = scan.source
        i = int(np.asarray(scan.indices)[ray])
        ranges = np.asarray(rays.distance_m, dtype=float)
        rng = float(ranges[gate] if ranges.ndim == 1 else ranges[i, gate]) / 1000.0
        value = self._data[ray, gate]
        text = (f"range {rng:.2f} km  az {float(rays.azimuth_deg[i]) % 360:.1f}°  "
                f"height {float(h[ray, gate]) * 1000:.0f} m  "
                f"{float(rays.latitude[i]):.4f}, {float(rays.longitude[i]):.4f}  ")
        text += "masked" if np.ma.is_masked(value) else f"{self._field} {float(value):.2f}"
        if "intensity" in rays.fields:
            text += f"  intensity {float(np.asarray(rays.fields['intensity']['data'])[i, gate]):.4f}"
        return text

    def _on_motion(self, event) -> None:
        if event.inaxes is self._ax and event.xdata is not None:
            self._readout.setText(self.readout_at(event.xdata, event.ydata))

    def _on_scroll(self, event) -> None:
        if event.inaxes is not self._ax or event.xdata is None:
            return
        factor = 0.8 if event.button == "up" else 1.25
        for get, set_, centre in ((self._ax.get_xlim, self._ax.set_xlim, event.xdata),
                                  (self._ax.get_ylim, self._ax.set_ylim, event.ydata)):
            lo, hi = get()
            set_(centre - (centre - lo) * factor, centre + (hi - centre) * factor)
        self._canvas.draw_idle()

    def _on_press(self, event) -> None:
        if event.dblclick and event.inaxes is self._ax:
            self._toolbar.home()

    # ── controls ───────────────────────────────────────────
    def _step(self, delta: int) -> None:
        index = min(max(self._index + delta, 0), len(self._scans) - 1)
        if index != self._index:
            self._chk_follow.setChecked(False)      # picking a scan stops following the clock
            self._index = index
            self._show()

    def _on_scan_picked(self, index: int) -> None:
        if 0 <= index < len(self._scans) and index != self._index:
            self._chk_follow.setChecked(False)
            self._index = index
            self._show()

    def _on_field_changed(self, _index) -> None:
        self._field = self._field_combo.currentData() or "velocity"
        self._limits = None
        self._show()
        self.field_changed.emit(self._field)

    def _on_intensity_moved(self, steps: int) -> None:
        self._intensity_value.setText(f"{1.0 + steps * INTENSITY_STEP:.3f}" if steps else "off")
        self._intensity_timer.start()

    def _on_intensity_settled(self) -> None:
        self._show()
        self.min_intensity_changed.emit(self.min_intensity())

    def set_colour_range(self, lo: float, hi: float) -> None:
        if hi > lo:
            self._limits = (float(lo), float(hi))
            self._show()

    def _on_colour_boxes(self) -> None:
        lo, hi = self._lo_box.value(), self._hi_box.value()
        if (lo, hi) != (self._limits or (None, None)) and hi > lo:
            self.set_colour_range(lo, hi)

    def _on_reset_colours(self) -> None:
        self._limits = None
        self._show()
