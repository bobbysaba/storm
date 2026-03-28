# ui/archive_controls.py
# ArchiveControls — floating bottom bar for archive time navigation.
#
# Layout
# ------
#   [ARCHIVE]  [2023-05-15]  [20:35:47 UTC  /  15:35 CDT  /  14:35 MDT]
#   [⏮] [◀]  [▶ / ⏸]  [▶]  [⏭]   Speed [60x ▾]   [━━━━●━━━━━━━━━━━]  [JUMP]
#
# The widget is positioned at the bottom of the map container by
# MainWindow._layout_overlays(), matching the style of the floating toolbar.

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSlider,
    QToolButton, QComboBox, QFrame, QSizePolicy,
    QDialog, QPushButton, QDateTimeEdit,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False

from archive.time_controller import TimeController, SPEED_OPTIONS, STEP_SECONDS

log = logging.getLogger(__name__)

_CT_TZ = ZoneInfo("America/Chicago") if _HAS_ZONEINFO else None
_MT_TZ = ZoneInfo("America/Denver")  if _HAS_ZONEINFO else None


class ArchiveControls(QWidget):
    """
    Floating bottom bar providing time navigation controls for archive mode.

    Connects to a TimeController and exposes:
      • Play / pause
      • Step forward / backward (STEP_SECONDS each)
      • Skip to start / end of day
      • Scrubber slider (seconds since midnight UTC of the session date)
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
        self._scrubbing = False    # True while the user is dragging the slider

        self.setObjectName("archiveControls")
        self._build_ui()
        self._connect_controller()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(4)

        # ── Row 1: time display ───────────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(0)
        row1.setContentsMargins(0, 0, 0, 0)

        archive_badge = QLabel("● ARCHIVE")
        archive_badge.setStyleSheet(
            "color: #FF9F1C; font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
        )
        row1.addWidget(archive_badge)

        row1.addWidget(self._vdiv())

        self._date_label = QLabel("----  --  --")
        self._date_label.setStyleSheet(
            "color: #C8D0DE; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._date_label)

        row1.addWidget(self._vdiv())

        self._utc_label = QLabel("--:--:-- UTC")
        self._utc_label.setStyleSheet(
            "color: #E8EAF0; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._utc_label)

        row1.addWidget(self._vdiv())

        self._ct_label = QLabel("--:-- CDT")
        self._ct_label.setStyleSheet(
            "color: #8E97AB; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._ct_label)

        row1.addWidget(self._vdiv())

        self._mt_label = QLabel("--:-- MDT")
        self._mt_label.setStyleSheet(
            "color: #8E97AB; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._mt_label)

        row1.addStretch()

        self._radar_status = QLabel("Radar: --")
        self._radar_status.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._radar_status)

        row1.addWidget(self._vdiv())

        self._sat_status = QLabel("Sat: --")
        self._sat_status.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._sat_status)

        root.addLayout(row1)

        # ── Row 2: playback controls + scrubber ───────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        row2.setContentsMargins(0, 0, 0, 0)

        self._btn_start = self._ctrl_btn("⏮", "Skip to start of day")
        self._btn_back  = self._ctrl_btn("◀",  f"Step back {STEP_SECONDS//60} min (Left or A)")
        self._btn_play  = self._ctrl_btn("▶",  "Play / pause (Space)")
        self._btn_play.setCheckable(True)
        self._btn_fwd   = self._ctrl_btn("▶",  f"Step forward {STEP_SECONDS//60} min (Right or D)")
        self._btn_fwd.setText("▶")
        self._btn_end   = self._ctrl_btn("⏭", "Skip to end of day")

        # Use slightly different icon to distinguish step-fwd from play.
        self._btn_fwd.setText("▶›")
        self._btn_back.setText("‹◀")

        for btn in (self._btn_start, self._btn_back, self._btn_play,
                    self._btn_fwd, self._btn_end):
            row2.addWidget(btn)

        row2.addWidget(self._vdiv())

        speed_lbl = QLabel("SPEED")
        speed_lbl.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.addWidget(speed_lbl)

        self._speed_combo = QComboBox()
        self._speed_combo.setObjectName("archiveSpeedCombo")
        for s in SPEED_OPTIONS:
            self._speed_combo.addItem(f"{s}×")
        self._speed_combo.setCurrentIndex(SPEED_OPTIONS.index(self._tc.speed))
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setFixedWidth(60)
        row2.addWidget(self._speed_combo)

        row2.addWidget(self._vdiv())

        # Scrubber: range 0–86399 (seconds since midnight UTC).
        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.setRange(0, 86399)
        self._scrubber.setValue(0)
        self._scrubber.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._scrubber.sliderPressed.connect(self._on_scrub_pressed)
        self._scrubber.sliderReleased.connect(self._on_scrub_released)
        self._scrubber.sliderMoved.connect(self._on_scrub_moved)
        row2.addWidget(self._scrubber)

        row2.addWidget(self._vdiv())

        self._shortcut_hint = QLabel("A/D STEP")
        self._shortcut_hint.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.addWidget(self._shortcut_hint)

        row2.addWidget(self._vdiv())

        jump_btn = self._ctrl_btn("JUMP", "Jump to a specific time")
        jump_btn.setFixedWidth(46)
        jump_btn.setStyleSheet(jump_btn.styleSheet() or "")
        jump_btn.clicked.connect(self._show_jump_dialog)
        row2.addWidget(jump_btn)

        root.addLayout(row2)

    def _ctrl_btn(self, text: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(30, 24)
        return btn

    def _vdiv(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.VLine)
        d.setStyleSheet("color: #394056; margin: 4px 4px;")
        return d

    def set_radar_status(self, text: str, error: bool = False) -> None:
        self._radar_status.setText(text)
        color = "#FF8F8F" if error else "#8E97AB"
        self._radar_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )

    def set_satellite_status(self, text: str, error: bool = False) -> None:
        self._sat_status.setText(text)
        color = "#FF8F8F" if error else "#8E97AB"
        self._sat_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )

    # ── Wiring ────────────────────────────────────────────────────────────────

    def _connect_controller(self):
        self._tc.time_changed.connect(self._on_time_changed)
        self._tc.playing_changed.connect(self._on_playing_changed)

        self._btn_start.clicked.connect(self._on_skip_start)
        self._btn_back.clicked.connect(self._tc.step_backward)
        self._btn_play.clicked.connect(self._tc.toggle_play)
        self._btn_fwd.clicked.connect(self._tc.step_forward)
        self._btn_end.clicked.connect(self._on_skip_end)

        # Keyboard shortcuts — these require a parent window to be set.
        # We install them lazily once we have a top-level window.
        self._shortcuts_installed = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shortcuts_installed:
            self._install_shortcuts()

    def _install_shortcuts(self):
        win = self.window()
        if win is None:
            return
        self._shortcuts_installed = True
        QShortcut(QKeySequence(Qt.Key.Key_Space),  win).activated.connect(self._tc.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left),   win).activated.connect(self._tc.step_backward)
        QShortcut(QKeySequence(Qt.Key.Key_Right),  win).activated.connect(self._tc.step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_A),      win).activated.connect(self._tc.step_backward)
        QShortcut(QKeySequence(Qt.Key.Key_D),      win).activated.connect(self._tc.step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_Home),   win).activated.connect(self._on_skip_start)
        QShortcut(QKeySequence(Qt.Key.Key_End),    win).activated.connect(self._on_skip_end)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_time_changed(self, t: datetime) -> None:
        self._update_time_display(t)
        if not self._scrubbing:
            self._scrubber.blockSignals(True)
            self._scrubber.setValue(self._tc.seconds_since_midnight())
            self._scrubber.blockSignals(False)

    def _on_playing_changed(self, playing: bool) -> None:
        self._btn_play.blockSignals(True)
        self._btn_play.setChecked(playing)
        self._btn_play.setText("⏸" if playing else "▶")
        self._btn_play.blockSignals(False)

    def _on_speed_changed(self, idx: int) -> None:
        self._tc.set_speed_by_index(idx)

    def _on_scrub_pressed(self) -> None:
        self._scrubbing = True
        self._tc.pause()

    def _on_scrub_released(self) -> None:
        secs = self._scrubber.value()
        self._tc.set_seconds_since_midnight(secs)
        self._scrubbing = False

    def _on_scrub_moved(self, secs: int) -> None:
        # Update the time display while dragging without emitting time_changed.
        if self._tc.current_time is None:
            return
        midnight = self._tc.current_time.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        preview = midnight + timedelta(seconds=secs)
        self._update_time_display(preview)

    def _on_skip_start(self) -> None:
        t = self._tc.current_time
        midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
        self._tc.set_time(midnight)

    def _on_skip_end(self) -> None:
        t = self._tc.current_time
        end_of_day = t.replace(hour=23, minute=59, second=59, microsecond=0)
        self._tc.set_time(end_of_day)

    def _show_jump_dialog(self) -> None:
        dlg = _JumpToTimeDialog(self._tc.current_time, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._tc.set_time(dlg.chosen_time())

    # ── Time display ──────────────────────────────────────────────────────────

    def _update_time_display(self, t: datetime) -> None:
        self._date_label.setText(t.strftime("%Y-%m-%d"))
        self._utc_label.setText(t.strftime("%H:%M:%S UTC"))

        if _CT_TZ:
            ct = t.astimezone(_CT_TZ)
            self._ct_label.setText(ct.strftime(f"%H:%M {ct.strftime('%Z')}"))
        if _MT_TZ:
            mt = t.astimezone(_MT_TZ)
            self._mt_label.setText(mt.strftime(f"%H:%M {mt.strftime('%Z')}"))

    # ── Level-2 product/tilt selectors (shown when archive radar is active) ───

    def add_radar_selectors(
        self,
        products: list[str],      # list of (pyart_field, display_label)
        tilts: list[float],
    ) -> None:
        """
        Dynamically insert product and tilt combo boxes into the control row.
        Called by MainWindow once the first Level-2 scan arrives.
        """
        # Already added.
        if hasattr(self, "_product_combo"):
            return

        row2 = self.layout().itemAt(1).layout()

        div = self._vdiv()
        row2.insertWidget(row2.count() - 1, div)

        prod_lbl = QLabel("PROD")
        prod_lbl.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.insertWidget(row2.count() - 1, prod_lbl)

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
        row2.insertWidget(row2.count() - 1, self._product_combo)

        tilt_lbl = QLabel("TILT")
        tilt_lbl.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row2.insertWidget(row2.count() - 1, tilt_lbl)

        self._tilt_combo = QComboBox()
        self._tilt_combo.setObjectName("archiveTiltCombo")
        self._tilt_combo.setFixedWidth(70)
        for deg in tilts:
            self._tilt_combo.addItem(f"{deg:.1f}°")
        self._tilt_combo.currentIndexChanged.connect(
            lambda i: self.tilt_changed.emit(i)
        )
        row2.insertWidget(row2.count() - 1, self._tilt_combo)

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


# ── Jump-to-time dialog ───────────────────────────────────────────────────────

class _JumpToTimeDialog(QDialog):
    def __init__(self, current_time: datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Jump to Time")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setFixedWidth(280)
        self.setStyleSheet("""
            QDialog { background-color: #0A0A0F; }
            QLabel  { color: #8E97AB; font-size: 11px; background: transparent; }
            QDateTimeEdit {
                background-color: #1A1A2E;
                border: 1px solid #1E1E2E;
                border-radius: 6px;
                color: #E8EAF0;
                font-size: 13px;
                padding: 6px 10px;
            }
            QPushButton {
                background-color: #00CFFF;
                border: none;
                border-radius: 6px;
                color: #0A0A0F;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 20px;
            }
            QPushButton:hover { background-color: #33D9FF; }
        """)

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
