"""Observation trails (archive): the bottom of the VEHICLES panel. A "show
trails" switch, then which quantity colors the trails (core/derived.py), how
far back they reach, time-to-space, wind barbs, and the color bar.

Storm-relative quantities, storm-relative barbs and time-to-space need the
storm motion from a track (TRACK); without one they are grayed out and a link
opens TRACK. The trails themselves are built in ui/app/main_window_trails.py.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.derived import QUANTITIES
from ui import palette as P

_GROUPS = (
    ("Measured", ("temperature", "dewpoint", "rh", "pressure", "wind_speed")),
    ("Thermodynamic", ("theta", "theta_v", "theta_e", "theta_w", "mixing_ratio")),
    ("Kinematic", ("u", "v", "sr_wind", "radial_wind", "tangential_wind")),
)
WINDOWS_MIN = (5, 10, 15, 30, 60)
DEFAULT_QUANTITY = "theta_e"
NEEDS_TRACK_TEXT = "Storm-relative needs a track ▸"
NEEDS_TRACK_TIP = ("Storm-relative winds, barbs and time-to-space use the storm's motion, taken "
                   "from a storm track. Click to open TRACK, then place the storm's center at "
                   "two or more times.")
_NEEDS_TRACK_SHORT = "Needs a storm track: place at least two TRACK points at different times"


class _ColorBar(QWidget):
    """The trail scale: its color bands, with each band edge labeled below."""

    BAR_H = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stops: list = []
        self._edges: list = []
        self.setFixedHeight(self.BAR_H + 14)
        self.setMinimumWidth(160)

    def set_stops(self, stops: list) -> None:
        from core.trails import band_edges
        self._stops = stops
        self._edges = band_edges(stops) if len(stops) >= 4 else []
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        bar = self.rect().adjusted(0, 0, 0, -(self.height() - self.BAR_H))
        if len(self._stops) < 4:
            painter.fillRect(bar, QColor(P.BG_ELEVATED))
            return
        values, colors = self._stops[0::2], self._stops[1::2]
        lo, hi = values[0], values[-1]
        gradient = QLinearGradient(0, 0, self.width(), 0)
        for value, color in zip(values, colors):
            gradient.setColorAt((value - lo) / (hi - lo) if hi > lo else 0, QColor(color))
        painter.fillRect(bar, gradient)
        # edge labels: as many as fit without touching (always the two ends)
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        step = abs(self._edges[1] - self._edges[0]) if len(self._edges) > 1 else 1.0
        decimals = 0 if step >= 1 else (1 if step >= 0.1 else 2)
        texts = [f"{e:.{decimals}f}" for e in self._edges]
        width = self.width()
        xs = [(e - lo) / (hi - lo) * width if hi > lo else 0 for e in self._edges]
        widest = max(metrics.horizontalAdvance(t) for t in texts) + 6
        every = 1
        while every < len(texts) and width / max(1, (len(texts) - 1) / every) < widest:
            every += 1
        painter.setPen(QColor(P.TEXT_LABEL))
        for i, (x, text) in enumerate(zip(xs, texts)):
            if i % every and i != len(texts) - 1:
                continue
            w = metrics.horizontalAdvance(text)
            painter.drawText(int(min(max(0, x - w / 2), width - w)), self.height() - 2, text)
            painter.drawLine(int(min(x, width - 1)), self.BAR_H, int(min(x, width - 1)), self.BAR_H + 2)


class TrailControls(QWidget):
    """settings_changed() -- any option changed; read them with trails_on() / settings()."""

    settings_changed = pyqtSignal()
    track_requested = pyqtSignal()       # the "needs a track" link: open TRACK

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("trailControls")
        self._track_available = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 2, 0, 0)
        col.setSpacing(4)
        small = f"color: {P.TEXT_LABEL}; font-size: 11px;"

        # ── row 1: switch, quantity, window ──
        row = QHBoxLayout()
        row.setSpacing(6)
        self._chk_show = QCheckBox("show trails")
        self._chk_show.setObjectName("vehiclePillToggle")
        self._chk_show.setToolTip("Each vehicle's recent path, colored by the chosen quantity")
        self._chk_show.toggled.connect(self._on_show_toggled)
        row.addWidget(self._chk_show)

        self._quantity = QComboBox()
        self._quantity.setToolTip("What colors the trails (measured, or derived from each "
                                  "observation's own pressure: nothing is assumed)")
        for group, keys in _GROUPS:
            self._quantity.addItem(f"— {group} —")
            self._quantity.model().item(self._quantity.count() - 1).setEnabled(False)
            for key in keys:
                self._quantity.addItem(QUANTITIES[key].label, key)
        self._quantity.setCurrentIndex(self._quantity.findData(DEFAULT_QUANTITY))
        # long names elide in the box (the list shows them whole), so the panel keeps its width
        self._quantity.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._quantity.setMinimumContentsLength(16)
        self._quantity.currentIndexChanged.connect(self.settings_changed.emit)
        row.addWidget(self._quantity, 1)

        self._window = QComboBox()
        self._window.setToolTip("How far back the trails reach from the archive clock")
        for minutes in WINDOWS_MIN:
            self._window.addItem(f"last {minutes} min", minutes)
        self._window.setCurrentIndex(WINDOWS_MIN.index(15))
        self._window.currentIndexChanged.connect(self.settings_changed.emit)
        row.addWidget(self._window)
        col.addLayout(row)

        # ── the rest only while trails are on ──
        self._options = QWidget()
        options = QVBoxLayout(self._options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(4)

        checks = QHBoxLayout()
        checks.setSpacing(10)
        self._time_to_space = QCheckBox("time-to-space")
        self._wind_barbs = QCheckBox("wind barbs")
        self._wind_barbs.setToolTip("Measured wind along each trail (knots; half barb 5, full 10, flag 50)")
        self._sr_barbs = QCheckBox("storm-relative")
        for chk in (self._time_to_space, self._wind_barbs, self._sr_barbs):
            chk.setObjectName("vehiclePillToggle")
            chk.toggled.connect(self.settings_changed.emit)
            checks.addWidget(chk)
        self._wind_barbs.toggled.connect(self._update_sr_barbs)
        checks.addStretch(1)
        options.addLayout(checks)

        self._needs_track = QPushButton(NEEDS_TRACK_TEXT)
        self._needs_track.setFlat(True)
        self._needs_track.setCursor(Qt.CursorShape.PointingHandCursor)
        self._needs_track.setToolTip(NEEDS_TRACK_TIP)
        self._needs_track.setStyleSheet(
            f"QPushButton {{ color: {P.ACCENT}; font-size: 11px; border: none; padding: 0; text-align: left; }}"
            f"QPushButton:hover {{ color: {P.ACCENT_HOVER}; }}")
        self._needs_track.clicked.connect(self.track_requested.emit)
        options.addWidget(self._needs_track, 0, Qt.AlignmentFlag.AlignLeft)

        scale = QHBoxLayout()
        scale.setSpacing(6)
        self._bar = _ColorBar()
        scale.addWidget(self._bar, 1)
        self._units = QLabel("")
        self._units.setStyleSheet(small)
        scale.addWidget(self._units)
        options.addLayout(scale)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {P.TEXT_MUTED}; font-size: 11px;")
        self._status.setWordWrap(True)
        self._status.hide()
        options.addWidget(self._status)
        col.addWidget(self._options)

        self._on_show_toggled(False)
        self.set_track_available(False)

    # ── state ─────────────────────────────────────────────
    def trails_on(self) -> bool:
        return self._chk_show.isChecked()

    def settings(self) -> tuple[str, int, bool, bool, bool]:
        """(quantity key, window minutes, time-to-space, wind barbs, storm-relative barbs)"""
        return (self._quantity.currentData(), self._window.currentData(),
                self._time_to_space.isChecked() and self._time_to_space.isEnabled(),
                self._wind_barbs.isChecked(),
                self._sr_barbs.isChecked() and self._sr_barbs.isEnabled())

    def _on_show_toggled(self, on: bool) -> None:
        self._quantity.setVisible(on)
        self._window.setVisible(on)
        self._options.setVisible(on)
        self.settings_changed.emit()

    def _update_sr_barbs(self, *_args) -> None:
        """Storm-relative barbs need barbs on and a storm track."""
        self._sr_barbs.setEnabled(self._wind_barbs.isChecked() and self._track_available)
        self._sr_barbs.setToolTip(
            "Barbs show the wind minus the storm motion (the track's mean), where the track covers the time"
            if self._track_available else _NEEDS_TRACK_SHORT)

    def set_track_available(self, available: bool) -> None:
        """Storm-relative quantities and time-to-space need a storm track."""
        self._track_available = available
        self._needs_track.setVisible(not available)
        self._update_sr_barbs()
        self._time_to_space.setEnabled(available)
        self._time_to_space.setToolTip(
            "Place each observation at its offset from the storm center at its own time"
            if available else _NEEDS_TRACK_SHORT)
        model = self._quantity.model()
        for i in range(self._quantity.count()):
            key = self._quantity.itemData(i)
            if key and QUANTITIES[key].needs_track:
                model.item(i).setEnabled(available)
        if not available and QUANTITIES[self._quantity.currentData()].needs_track:
            self._quantity.setCurrentIndex(self._quantity.findData(DEFAULT_QUANTITY))

    def set_scale(self, stops: list, units: str) -> None:
        """The bar labels its own band edges; the units go after it."""
        self._bar.set_stops(stops)
        self._units.setText(units if stops else "")

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))
