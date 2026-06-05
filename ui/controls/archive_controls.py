
import logging
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QToolButton, QComboBox, QFrame,
    QDialog, QPushButton, QDateTimeEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut

try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False

from archive.time_controller import TimeController, SPEED_OPTIONS

log = logging.getLogger(__name__)

_CT_TZ = ZoneInfo("America/Chicago") if _HAS_ZONEINFO else None
_MT_TZ = ZoneInfo("America/Denver")  if _HAS_ZONEINFO else None


class ArchiveControls(QWidget):
    """
    Floating bottom bar providing time navigation controls for archive mode.

    Connects to a TimeController and exposes:
      • Play / pause
      • Normal mode: ±10-second and ±1-minute steps
      • Precision mode: ±1-second and ±10-second steps
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
            "color: #FF9F1C; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        row1.addWidget(archive_badge)

        row1.addWidget(self._vdiv())

        self._date_label = QLabel("---- -- --")
        self._date_label.setStyleSheet(
            "color: #C8D0DE; font-size: 10px; font-weight: 600; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._date_label)

        self._utc_label = QLabel("--:--:-- UTC")
        self._utc_label.setStyleSheet(
            "color: #E8EAF0; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._utc_label)

        row1.addWidget(self._vdiv())

        self._local_label = QLabel("--:-- CT / --:-- MT")
        self._local_label.setStyleSheet(
            "color: #8E97AB; font-size: 10px; font-weight: 500; letter-spacing: 0.5px;"
        )
        row1.addWidget(self._local_label)

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

        row1.addWidget(self._vdiv())

        self._obs_status = QLabel("OBS: MQTT")
        self._obs_status.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )
        row1.addWidget(self._obs_status)

        root.addLayout(row1)

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

        self._speed_label = QLabel("SPEED")
        self._speed_label.setStyleSheet(
            "color: #6E7A8F; font-size: 9px; font-weight: 600; letter-spacing: 0.5px;"
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

        root.addLayout(row2)
        self._precision_mode = False

    def _ctrl_btn(self, text: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 22)
        return btn

    def _vdiv(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.VLine)
        d.setStyleSheet("color: #394056; margin: 3px 0;")
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

    def set_obs_status(self, text: str, active: bool = False) -> None:
        self._obs_status.setText(text)
        color = "#39D98A" if active else "#8E97AB"
        self._obs_status.setStyleSheet(
            f"color: {color}; font-size: 9px; font-weight: 600; letter-spacing: 0.4px;"
        )

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
        self._tc.playing_changed.connect(self._on_playing_changed)

        self._btn_start.clicked.connect(self._on_skip_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_play.clicked.connect(self._tc.toggle_play)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_end.clicked.connect(self._on_skip_end)

        # keyboard shortcuts — these require a parent window to be set.
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
        QShortcut(QKeySequence(Qt.Key.Key_Left),   win).activated.connect(self._on_step_back)
        QShortcut(QKeySequence(Qt.Key.Key_Right),  win).activated.connect(self._on_step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_A),      win).activated.connect(self._on_step_back)
        QShortcut(QKeySequence(Qt.Key.Key_D),      win).activated.connect(self._on_step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_Home),   win).activated.connect(self._on_skip_start)
        QShortcut(QKeySequence(Qt.Key.Key_End),    win).activated.connect(self._on_skip_end)


    def _on_time_changed(self, t: datetime) -> None:
        self._update_time_display(t)

    def _on_playing_changed(self, playing: bool) -> None:
        self._btn_play.blockSignals(True)
        self._btn_play.setChecked(playing)
        self._btn_play.setText("⏸" if playing else "▶")
        self._btn_play.blockSignals(False)

    def _on_speed_changed(self, idx: int) -> None:
        self._tc.set_speed_by_index(idx)

    def _on_step_back(self) -> None:
        self._tc.step(-1 if self._precision_mode else -10)

    def _on_step_forward(self) -> None:
        self._tc.step(1 if self._precision_mode else 10)

    def _on_skip_start(self) -> None:
        self._tc.step(-10 if self._precision_mode else -60)

    def _on_skip_end(self) -> None:
        self._tc.step(10 if self._precision_mode else 60)

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
