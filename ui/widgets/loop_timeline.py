"""Loop timeline: one bar, bottom centre, for the live radar / satellite loop.

One bar for the live loop: step back, play / pause, step forward, a time strip,
the loop time, Live and speed.
SYNC and VEH live in the settings pill (gear). It holds no loop state: it shows what ``set_state`` / ``set_ticks`` give it and
emits what the user asked for; MainWindow routes that to the driving drawer
(radar, or satellite when radar is off), as phase 2's ``LoopSync`` does.

The strip draws radar scan times on its upper edge and satellite frame times on
its lower edge. A satellite frame with no radar scan within the sector's
tolerance after it is never shown by the synced loop, so it is drawn hollow.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QActionGroup, QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QWidget

from ui import palette as P

TIMELINE_HEIGHT = 40
TICK_PX = 7


def _has_radar_after(t: datetime, radar: list[datetime], tolerance_s: float) -> bool:
    return any(timedelta(0) <= r - t <= timedelta(seconds=tolerance_s) for r in radar)


class TimeStrip(QWidget):
    """Ticks and a cursor across the loop's time span; click or drag to scrub."""

    scrubbed = pyqtSignal(object)   # datetime under the mouse at release

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radar: list[datetime] = []
        self.satellite: list[datetime] = []
        self.tolerance_s = 600.0
        self.cursor_time: datetime | None = None
        self._drag_time: datetime | None = None

    def set_ticks(self, radar, satellite, tolerance_s: float) -> None:
        self.radar = sorted(radar)
        self.satellite = sorted(satellite)
        self.tolerance_s = tolerance_s
        self.update()

    def set_cursor_time(self, t: datetime | None) -> None:
        self.cursor_time = t
        self.update()

    def span(self) -> tuple[datetime, datetime] | None:
        times = self.radar + self.satellite
        if not times:
            return None
        lo, hi = min(times), max(times)
        return (lo, hi) if hi > lo else (lo - timedelta(minutes=1), hi)

    def x_of(self, t: datetime) -> float:
        lo, hi = self.span()
        frac = (t - lo).total_seconds() / (hi - lo).total_seconds()
        return 4 + max(0.0, min(1.0, frac)) * (self.width() - 8)

    def time_at(self, x: float) -> datetime | None:
        span = self.span()
        if span is None:
            return None
        lo, hi = span
        frac = max(0.0, min(1.0, (x - 4) / max(1, self.width() - 8)))
        return lo + (hi - lo) * frac

    def satellite_hollow(self, t: datetime) -> bool:
        return bool(self.radar) and not _has_radar_after(t, self.radar, self.tolerance_s)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        mid = h / 2
        p.setPen(QPen(QColor(P.BORDER_SOFT), 1))
        p.drawLine(QPointF(4, mid), QPointF(self.width() - 4, mid))
        if self.span() is None:
            return
        p.setPen(QPen(QColor(P.TEXT_SECONDARY), 1.5))
        for t in self.radar:
            x = self.x_of(t)
            p.drawLine(QPointF(x, mid - TICK_PX), QPointF(x, mid - 1))
        for t in self.satellite:
            x = self.x_of(t)
            rect = QRectF(x - 2, mid + 2, 4, TICK_PX - 2)
            p.setPen(QPen(QColor(P.INFO), 1))
            p.setBrush(Qt.BrushStyle.NoBrush if self.satellite_hollow(t) else QColor(P.INFO))
            p.drawRect(rect)
        cursor = self._drag_time or self.cursor_time
        if cursor is not None:
            x = self.x_of(cursor)
            p.setPen(QPen(QColor(P.ACCENT), 2))
            p.drawLine(QPointF(x, 1), QPointF(x, h - 1))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_time = self.time_at(event.position().x())
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag_time is not None:
            self._drag_time = self.time_at(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event):
        # like the drawers' sliders: render once, on release
        if self._drag_time is not None and event.button() == Qt.MouseButton.LeftButton:
            t, self._drag_time = self._drag_time, None
            self.update()
            self.scrubbed.emit(t)


class LoopTimeline(QFrame):
    play_clicked = pyqtSignal()
    step_requested = pyqtSignal(int)
    scrub_requested = pyqtSignal(object)
    live_clicked = pyqtSignal()
    speed_chosen = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loopTimeline")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(TIMELINE_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)

        def button(text, tip, slot=None, checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            if slot is not None:
                b.clicked.connect(slot)
            row.addWidget(b)
            return b

        self.btn_back = button("⏪", "Step back one frame", lambda: self.step_requested.emit(-1))
        self.btn_play = button("▶", "Play / pause the loop", lambda: self.play_clicked.emit())
        self.btn_fwd = button("⏩", "Step forward one frame", lambda: self.step_requested.emit(1))
        self.strip = TimeStrip()
        self.strip.scrubbed.connect(self.scrub_requested)
        row.addWidget(self.strip, 1)
        self.time_label = QLabel("--:--Z")
        self.time_label.setObjectName("loopTimelineTime")
        row.addWidget(self.time_label)
        self.btn_live = button("LIVE", "Back to the latest frame", lambda: self.live_clicked.emit(),
                               checkable=True)
        self.btn_live.setObjectName("loopTimelineLive")

        self.btn_speed = button("1×", "Playback speed")
        self.btn_speed.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._speed_menu = QMenu(self.btn_speed)
        self.btn_speed.setMenu(self._speed_menu)
        self._speed_group = QActionGroup(self)
        self._speed_labels: list[str] = []

        self.btn_speed.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")

    # ── state from the window ─────────────────────────────
    def set_state(self, *, frame_time: datetime | None, now: datetime, looping: bool,
                  live: bool, can_step: bool, speed_labels: list[str], speed_index: int) -> None:
        self.btn_play.setText("⏸" if looping else "▶")
        for b in (self.btn_back, self.btn_play, self.btn_fwd, self.strip):
            b.setEnabled(can_step)
        _quiet(self.btn_live, live)
        if frame_time is None:
            text = "--:--Z"
        elif live:
            text = frame_time.strftime("%H:%MZ")
        else:
            minutes = int((now - frame_time).total_seconds() // 60)
            text = f"{frame_time.strftime('%H:%MZ')} · −{minutes}m"
        self.time_label.setText(text)
        self.time_label.setProperty("looping", not live and frame_time is not None)
        self.time_label.style().unpolish(self.time_label)
        self.time_label.style().polish(self.time_label)
        self.strip.set_cursor_time(None if live else frame_time)
        self._set_speeds(speed_labels, speed_index)

    def set_ticks(self, radar, satellite, tolerance_s: float = 600) -> None:
        self.strip.set_ticks(radar, satellite, tolerance_s)

    def _set_speeds(self, labels: list[str], index: int) -> None:
        if labels != self._speed_labels:
            self._speed_labels = list(labels)
            self._speed_menu.clear()
            for i, label in enumerate(labels):
                act = self._speed_menu.addAction(label)
                act.setCheckable(True)
                self._speed_group.addAction(act)
                act.triggered.connect(lambda _=False, i=i: self.speed_chosen.emit(i))
        actions = self._speed_menu.actions()
        if 0 <= index < len(actions):
            actions[index].setChecked(True)
            self.btn_speed.setText(labels[index])


def _quiet(button: QToolButton, checked: bool) -> None:
    button.blockSignals(True)
    button.setChecked(checked)
    button.blockSignals(False)
