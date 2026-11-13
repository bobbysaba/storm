
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QToolButton, QComboBox, QFrame, QSlider,
    QDialog, QPushButton, QDateTimeEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter

try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False

from archive.time_controller import TimeController, SPEED_OPTIONS
from ui import palette as P

log = logging.getLogger(__name__)

_CT_TZ = ZoneInfo("America/Chicago") if _HAS_ZONEINFO else None
_MT_TZ = ZoneInfo("America/Denver")  if _HAS_ZONEINFO else None

# the slider's groove starts and ends half a handle (theme: 12px) from its edges
_GROOVE_INSET = 6


def _window_x(controller, t: datetime, width: int) -> float:
    """x of time t over a widget as wide as the slider, aligned with its groove."""
    start, end = controller.window
    total = (end - start).total_seconds()
    span = max(1, width - 2 * _GROOVE_INSET)
    frac = (t - start).total_seconds() / total if total > 0 else 0.0
    return _GROOVE_INSET + span * min(1.0, max(0.0, frac))


class _CoverageStrip(QWidget):
    """A thin strip under the time slider marking when something (e.g. a
    lidar) has data: colored spans over the session window."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._tc = controller
        self._spans: list = []
        self._color = P.ACCENT
        self.setFixedHeight(5)
        self.setVisible(False)

    def set_spans(self, spans, color: str, tooltip: str) -> None:
        self._spans, self._color = list(spans), color
        self.setToolTip(tooltip)
        self.setVisible(bool(self._spans))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(P.BORDER_SUBTLE))
        for a, b in self._spans:
            x0 = _window_x(self._tc, a, self.width())
            x1 = _window_x(self._tc, b, self.width())
            painter.fillRect(int(x0), 0, max(2, int(x1 - x0)), self.height(), QColor(self._color))


class _HourAxis(QWidget):
    """Hour ticks and labels under the time slider, plus radar scan ticks.

    A tick every hour and a label every 1, 2, 3 or 6 hours, whichever fits.
    Minor ticks every 10, 15 or 30 minutes, whichever stays MINOR_MIN_PX apart.
    Radar scans (set_scan_times) are short ticks along the top edge.
    """

    MINOR_MIN_PX = 6

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._tc = controller
        self._scan_times: list[datetime] = []
        self.setFixedHeight(19)

    def set_scan_times(self, times: list[datetime]) -> None:
        self._scan_times = times
        self.update()

    def paintEvent(self, _event) -> None:
        start, end = self._tc.window
        total = (end - start).total_seconds()
        if total <= 0:
            return
        painter = QPainter(self)
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        w = self.width()
        x_of = lambda t: _window_x(self._tc, t, w)

        painter.setPen(QColor(P.TEXT_DISABLED))
        for t in self._scan_times:
            if start <= t <= end:
                x = int(x_of(t))
                painter.drawLine(x, 0, x, 1)

        per_hour = (w - 2 * _GROOVE_INSET) * 3600 / total
        room = metrics.horizontalAdvance("00Z") + 12
        step = next((n for n in (1, 2, 3, 6) if per_hour * n >= room), 6)
        minor = next((m for m in (10, 15, 30) if per_hour * m / 60 >= self.MINOR_MIN_PX), None)
        top = 3
        first = start.replace(minute=0, second=0, microsecond=0)
        if minor:
            painter.setPen(QColor(P.BORDER_2))
            t = first
            while t <= end:
                if t >= start and t.minute:
                    x = int(x_of(t))
                    painter.drawLine(x, top, x, top + 2)
                t += timedelta(minutes=minor)
        t = first if first >= start else first + timedelta(hours=1)
        while t <= end:
            x = x_of(t)
            labeled = t.hour % step == 0
            painter.setPen(QColor(P.BORDER_SOFT if labeled else P.BORDER_STRONG))
            painter.drawLine(int(x), top, int(x), top + (4 if labeled else 3))
            if labeled:
                label = f"{t:%H}Z"
                lw = metrics.horizontalAdvance(label)
                # 00Z marks the next UTC day, so it stands out a little
                painter.setPen(QColor(P.TEXT_LABEL if t.hour == 0 else P.TEXT_MUTED))
                painter.drawText(int(min(max(0, x - lw / 2), w - lw)), top + 15, label)
            t += timedelta(hours=1)


class ArchiveControls(QWidget):
    """
    Floating bottom bar providing time navigation controls for archive mode.

    Connects to a TimeController and exposes:
      • Play / pause
      • Normal mode: ±10-second and ±1-minute steps
      • Precision mode: ±1-second and ±10-second steps
      • Previous / next radar scan
      • Time slider over the session window, with hour labels, radar scan
        ticks and an optional coverage strip
      • Speed selector
      • Jump-to-time dialog

    Signals
    -------
    tilt_changed(int)   — user changed tilt index
    product_changed(str) — user changed Level-2 product
    """

    tilt_changed    = pyqtSignal(int)
    product_changed = pyqtSignal(str)

    def __init__(self, time_controller: TimeController, parent=None):
        super().__init__(parent)
        self._tc = time_controller
        self._session_date: Optional[datetime] = None
        self._scan_times: list[datetime] = []

        self.setObjectName("archiveControls")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build_ui()
        self._connect_controller()


    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 5, 10, 5)
        root.setSpacing(2)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.setContentsMargins(0, 0, 0, 0)

        archive_badge = QLabel("ARCHIVE")
        archive_badge.setStyleSheet(
            f"color: {P.MODE_ARCHIVE}; font-size: 11px; font-weight: 700; letter-spacing: 1px;"
        )
        row1.addWidget(archive_badge)

        row1.addWidget(self._vdiv())

        self._date_label = QLabel("---- -- --")
        self._date_label.setStyleSheet(
            f"color: {P.TEXT_SECONDARY}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._date_label)

        self._utc_label = QLabel("--:--:-- UTC")
        self._utc_label.setStyleSheet(
            f"color: {P.TEXT}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._utc_label)

        row1.addWidget(self._vdiv())

        self._local_label = QLabel("--:-- CT / --:-- MT")
        self._local_label.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 11px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._local_label)

        row1.addStretch()

        self._radar_status = QLabel("Radar: --")
        self._radar_status.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._radar_status)

        row1.addWidget(self._vdiv())

        self._sat_status = QLabel("Sat: --")
        self._sat_status.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._sat_status)

        row1.addWidget(self._vdiv())

        self._obs_status = QLabel("OBS: MQTT")
        self._obs_status.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._obs_status)

        root.addLayout(row1)

        # seconds since the session start, to the window's end (which can be
        # the next UTC morning). Dragging only previews the time; the seek,
        # which every fetcher acts on, happens on release.
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setObjectName("archiveTimeSlider")
        self._slider.setRange(0, max(0, self._tc.window_seconds() - 1))
        self._slider.setTracking(False)
        self._slider.setFixedHeight(16)
        self._slider.setToolTip("Drag to move through the session; it loads on release")
        self._slider.sliderMoved.connect(self._on_slider_preview)
        self._slider.valueChanged.connect(self._on_slider_committed)
        root.addWidget(self._slider)
        self._coverage = _CoverageStrip(self._tc, self)
        root.addWidget(self._coverage)
        self._hours = _HourAxis(self._tc, self)
        root.addWidget(self._hours)

        row2 = QHBoxLayout()
        row2.setSpacing(5)
        row2.setContentsMargins(0, 0, 0, 0)

        self._btn_start = self._ctrl_btn("-1m", "Step back 1 minute")
        self._btn_back  = self._ctrl_btn("-10", "Step back 10 seconds (Left or A)")
        self._btn_play  = self._ctrl_btn("▶",  "Play / pause (Space)")
        self._btn_play.setCheckable(True)
        self._btn_fwd   = self._ctrl_btn("+10", "Step forward 10 seconds (Right or D)")
        self._btn_end   = self._ctrl_btn("+1m", "Step forward 1 minute")

        for btn in (self._btn_start, self._btn_back, self._btn_play,
                    self._btn_fwd, self._btn_end):
            row2.addWidget(btn)

        row2.addWidget(self._vdiv())

        self._btn_prev_scan = self._ctrl_btn("⏮", "Previous radar scan")
        self._btn_next_scan = self._ctrl_btn("⏭", "Next radar scan")
        row2.addWidget(self._btn_prev_scan)
        row2.addWidget(self._btn_next_scan)
        self._set_scan_buttons_enabled()

        row2.addWidget(self._vdiv())

        self._speed_label = QLabel("SPEED")
        self._speed_label.setStyleSheet(
            f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.addWidget(self._speed_label)

        self._speed_combo = QComboBox()
        self._speed_combo.setObjectName("archiveSpeedCombo")
        for s in SPEED_OPTIONS:
            self._speed_combo.addItem(f"{s}×")
        self._speed_combo.setCurrentIndex(SPEED_OPTIONS.index(self._tc.speed))
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setFixedWidth(54)
        row2.addWidget(self._speed_combo)

        row2.addWidget(self._vdiv())

        jump_btn = self._ctrl_btn("JUMP", "Jump to a specific time")
        jump_btn.setFixedWidth(44)
        jump_btn.setStyleSheet(jump_btn.styleSheet() or "")
        jump_btn.clicked.connect(self._show_jump_dialog)
        row2.addWidget(jump_btn)
        row2.addStretch()

        root.addLayout(row2)
        self._row2 = row2
        self._precision_mode = False
        self._on_time_changed(self._tc.current_time)

    def _ctrl_btn(self, text: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 22)
        return btn

    def _vdiv(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.VLine)
        d.setStyleSheet(f"color: {P.DIVIDER}; margin: 3px 0;")
        return d

    def set_radar_status(self, text: str, error: bool = False) -> None:
        self._radar_status.setText(text)
        color = P.ERROR_SOFT_2 if error else P.TEXT_LABEL
        self._radar_status.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_satellite_status(self, text: str, error: bool = False, tooltip: str = "") -> None:
        self._sat_status.setText(text)
        self._sat_status.setToolTip(tooltip)
        color = P.ERROR_SOFT_2 if error else P.TEXT_LABEL
        self._sat_status.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_obs_status(self, text: str, active: bool = False) -> None:
        self._obs_status.setText(text)
        color = P.OK if active else P.TEXT_LABEL
        self._obs_status.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_available_scan_times(self, iso_times: list[str]) -> None:
        """The radar station's scan index (ISO UTC strings, chronological;
        ArchiveRadarFetcher.index_loaded), for the scan buttons and ticks.
        Replaces the previous list when the station changes."""
        self._scan_times = [
            datetime.fromisoformat(t.replace("Z", "+00:00")) for t in iso_times
        ]
        self._hours.set_scan_times(self._scan_times)
        self._set_scan_buttons_enabled()

    def set_coverage(self, spans, color: str = P.ACCENT, tooltip: str = "") -> None:
        """Mark (start, end) spans under the slider, e.g. when a lidar was
        scanning; an empty list hides the strip."""
        self._coverage.set_spans(spans, color, tooltip)

    def set_precision_mode(self, enabled: bool) -> None:
        """Use one-second playback navigation when dense observations are available."""
        self._precision_mode = enabled
        self._btn_start.setText("-10")
        self._btn_back.setText("-1")
        self._btn_fwd.setText("+1")
        self._btn_end.setText("+10")
        self._btn_start.setToolTip("Step back 10 seconds")
        self._btn_back.setToolTip("Step back 1 second (Left or A)")
        self._btn_fwd.setToolTip("Step forward 1 second (Right or D)")
        self._btn_end.setToolTip("Step forward 10 seconds")
        self._speed_label.setVisible(not enabled)
        self._speed_combo.setVisible(not enabled)
        self._tc.set_precision_playback(enabled)

    def _connect_controller(self):
        self._tc.time_changed.connect(self._on_time_changed)
        self._tc.window_changed.connect(self._on_window_changed)
        self._tc.playing_changed.connect(self._on_playing_changed)
        self._btn_prev_scan.clicked.connect(self.prev_scan)
        self._btn_next_scan.clicked.connect(self.next_scan)

        self._btn_start.clicked.connect(self.skip_start)
        self._btn_back.clicked.connect(self.step_back)
        self._btn_play.clicked.connect(self._tc.toggle_play)
        self._btn_fwd.clicked.connect(self.step_forward)
        self._btn_end.clicked.connect(self.skip_end)

    def _on_time_changed(self, t: datetime) -> None:
        self._update_time_display(t)
        # follow the clock without seeking again
        if not self._slider.isSliderDown():
            self._slider.blockSignals(True)
            self._slider.setValue(self._tc.seconds_since_start())
            self._slider.blockSignals(False)

    def _on_window_changed(self, _start: datetime, _end: datetime) -> None:
        self._coverage.update()
        self._hours.update()
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(0, self._tc.window_seconds() - 1))
        self._slider.setValue(self._tc.seconds_since_start())
        self._slider.blockSignals(False)

    def _on_slider_preview(self, value: int) -> None:
        """Dragging: update the clock text only, no fetches until release."""
        start, _ = self._tc.window
        self._update_time_display(start + timedelta(seconds=value))

    def _on_slider_committed(self, value: int) -> None:
        self._tc.pause()
        self._tc.set_seconds_since_start(value)

    def _on_playing_changed(self, playing: bool) -> None:
        self._btn_play.blockSignals(True)
        self._btn_play.setChecked(playing)
        self._btn_play.setText("⏸" if playing else "▶")
        self._btn_play.blockSignals(False)

    def _on_speed_changed(self, idx: int) -> None:
        self._tc.set_speed_by_index(idx)

    # also bound to keys by MainWindow (ui/shortcuts.py)
    def toggle_play(self) -> None:
        self._tc.toggle_play()

    def step_back(self) -> None:
        self._tc.step(-1 if self._precision_mode else -10)

    def step_forward(self) -> None:
        self._tc.step(1 if self._precision_mode else 10)

    def skip_start(self) -> None:
        self._tc.step(-10 if self._precision_mode else -60)

    def skip_end(self) -> None:
        self._tc.step(10 if self._precision_mode else 60)

    def prev_scan(self) -> None:
        self._jump_to_scan(-1)

    def next_scan(self) -> None:
        self._jump_to_scan(1)

    def local_time_text(self) -> str:
        """The clock in Central / Mountain time, as the bar shows it."""
        return self._local_label.text()

    def _jump_to_scan(self, direction: int) -> None:
        """Pause on the nearest radar scan before (-1) or after (+1) the
        clock. Does nothing until the scan index has loaded."""
        current = self._tc.current_time
        if direction < 0:
            target = next((t for t in reversed(self._scan_times) if t < current), None)
        else:
            target = next((t for t in self._scan_times if t > current), None)
        if target is None:
            return
        self._tc.pause()
        self._tc.set_time(target)

    def _set_scan_buttons_enabled(self) -> None:
        for btn in (self._btn_prev_scan, self._btn_next_scan):
            btn.setEnabled(bool(self._scan_times))

    def _show_jump_dialog(self) -> None:
        dlg = _JumpToTimeDialog(self._tc.current_time, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._tc.set_time(dlg.chosen_time())


    def _update_time_display(self, t: datetime) -> None:
        self._date_label.setText(t.strftime("%Y-%m-%d"))
        self._utc_label.setText(t.strftime("%H:%M:%S UTC"))

        if _CT_TZ:
            ct = t.astimezone(_CT_TZ)
        if _MT_TZ:
            mt = t.astimezone(_MT_TZ)
        if _CT_TZ and _MT_TZ:
            self._local_label.setText(
                f"{ct.strftime('%H:%M %Z')} / {mt.strftime('%H:%M %Z')}"
            )


    def add_radar_selectors(
        self,
        products: list[str],      # list of (pyart_field, display_label)
        tilts: list[float],
    ) -> None:
        """
        Dynamically insert product and tilt combo boxes into the control row.
        Called by MainWindow once the first Level-2 scan arrives.
        """
        # already added.
        if hasattr(self, "_product_combo"):
            return

        row2 = self._row2   # inserted before JUMP and the trailing stretch

        div = self._vdiv()
        row2.insertWidget(row2.count() - 2, div)

        prod_lbl = QLabel("PROD")
        prod_lbl.setStyleSheet(
            f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.insertWidget(row2.count() - 2, prod_lbl)

        self._product_combo = QComboBox()
        self._product_combo.setObjectName("archiveProductCombo")
        self._product_combo.setFixedWidth(120)
        for field, label in products:
            self._product_combo.addItem(label, userData=field)
        self._product_combo.currentIndexChanged.connect(
            lambda i: self.product_changed.emit(
                self._product_combo.itemData(i) or self._product_combo.itemText(i)
            )
        )
        row2.insertWidget(row2.count() - 2, self._product_combo)

        tilt_lbl = QLabel("TILT")
        tilt_lbl.setStyleSheet(
            f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.insertWidget(row2.count() - 2, tilt_lbl)

        self._tilt_combo = QComboBox()
        self._tilt_combo.setObjectName("archiveTiltCombo")
        self._tilt_combo.setFixedWidth(70)
        for deg in tilts:
            self._tilt_combo.addItem(f"{deg:.1f}°")
        self._tilt_combo.currentIndexChanged.connect(
            lambda i: self.tilt_changed.emit(i)
        )
        row2.insertWidget(row2.count() - 2, self._tilt_combo)

    def update_tilt_list(self, tilts: list[float]) -> None:
        """Refresh the tilt combo when a new scan arrives with different tilts."""
        if not hasattr(self, "_tilt_combo"):
            return
        current = self._tilt_combo.currentIndex()
        self._tilt_combo.blockSignals(True)
        self._tilt_combo.clear()
        for deg in tilts:
            self._tilt_combo.addItem(f"{deg:.1f}°")
        self._tilt_combo.setCurrentIndex(min(current, self._tilt_combo.count() - 1))
        self._tilt_combo.blockSignals(False)



class _JumpToTimeDialog(QDialog):
    def __init__(self, current_time: datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Jump to Time")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setFixedWidth(280)
        self.setStyleSheet(P.qss("""
            QDialog { background-color: $bg_base; }
            QLabel  { color: $text_label; font-size: 11px; background: transparent; }
            QDateTimeEdit {
                background-color: $bg_elevated;
                border: 1px solid $border_subtle;
                border-radius: 6px;
                color: $text;
                font-size: 13px;
                padding: 6px 10px;
            }
            QPushButton {
                background-color: $accent;
                border: none;
                border-radius: 6px;
                color: $bg_base;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 20px;
            }
            QPushButton:hover { background-color: $accent_hover; }
        """))

        from PyQt6.QtCore import QDateTime
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Enter UTC time:"))

        self._dt_edit = QDateTimeEdit()
        self._dt_edit.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self._dt_edit.setCalendarPopup(True)
        qt_dt = QDateTime(
            current_time.year, current_time.month, current_time.day,
            current_time.hour, current_time.minute, current_time.second,
        )
        self._dt_edit.setDateTime(qt_dt)
        layout.addWidget(self._dt_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("JUMP")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def chosen_time(self) -> datetime:
        qt_dt = self._dt_edit.dateTime()
        return datetime(
            qt_dt.date().year(),
            qt_dt.date().month(),
            qt_dt.date().day(),
            qt_dt.time().hour(),
            qt_dt.time().minute(),
            qt_dt.time().second(),
            tzinfo=timezone.utc,
        )
