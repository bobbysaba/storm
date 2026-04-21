from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QSlider,
    QToolButton, QPushButton, QFrame,
)


class HrrrControls(QWidget):
    """Compact drawer for server-rendered HRRR overlay products."""

    run_changed = pyqtSignal(str)
    field_changed = pyqtSignal(str)
    frame_requested = pyqtSignal(int)
    loop_toggled = pyqtSignal(bool)
    speed_changed = pyqtSignal(int)
    opacity_changed = pyqtSignal(float)
    refresh_requested = pyqtSignal()
    visible_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating = False
        self._hours: list[int] = []
        self._current_frame = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drawer = QWidget()
        self._drawer.setObjectName("hrrrDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row1 = QWidget()
        row1.setObjectName("hrrrControlRow")
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(5)

        self._visible_btn = QToolButton()
        self._visible_btn.setObjectName("hrrrShowButton")
        self._visible_btn.setText("SHOW")
        self._visible_btn.setCheckable(True)
        self._visible_btn.setToolTip("Show HRRR overlay")
        self._visible_btn.toggled.connect(self.visible_toggled.emit)
        r1.addWidget(self._visible_btn)

        self._run_combo = QComboBox()
        self._run_combo.setObjectName("hrrrRunCombo")
        self._run_combo.setMinimumWidth(88)
        self._run_combo.setToolTip("HRRR run")
        self._run_combo.currentIndexChanged.connect(lambda _idx: self._on_run_changed())
        r1.addWidget(self._run_combo)

        self._field_combo = QComboBox()
        self._field_combo.setObjectName("hrrrFieldCombo")
        self._field_combo.setMinimumWidth(124)
        self._field_combo.currentIndexChanged.connect(lambda _idx: self._on_field_changed())
        r1.addWidget(self._field_combo)

        self._refresh_btn = QPushButton("REFRESH")
        self._refresh_btn.setObjectName("hrrrRefreshButton")
        self._refresh_btn.setFixedHeight(26)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        r1.addWidget(self._refresh_btn)

        r1.addWidget(self._vdiv())

        lbl = QLabel("OPACITY")
        lbl.setStyleSheet("color: #B5BDCC; font-size: 10px;")
        r1.addWidget(lbl)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setObjectName("hrrrOpacitySlider")
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(65)
        self._opacity_slider.setFixedWidth(105)
        self._opacity_slider.valueChanged.connect(
            lambda v: self.opacity_changed.emit(v / 100.0)
        )
        r1.addWidget(self._opacity_slider)
        r1.addStretch(1)
        col.addWidget(row1)

        row2 = QWidget()
        row2.setObjectName("hrrrStatusRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        self._btn_jump_start = self._pbtn("⏮", "First forecast hour")
        self._btn_back = self._pbtn("⏪", "Step back one forecast hour")
        self._btn_play = self._pbtn("▶", "Play / Pause loop", checkable=True)
        self._btn_fwd = self._pbtn("⏩", "Step forward one forecast hour")
        self._btn_jump_end = self._pbtn("⏭", "Last forecast hour")

        self._btn_jump_start.clicked.connect(self._on_jump_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_play.toggled.connect(self._on_play_toggled)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_jump_end.clicked.connect(self._on_jump_end)

        for btn in (
            self._btn_jump_start,
            self._btn_back,
            self._btn_play,
            self._btn_fwd,
            self._btn_jump_end,
        ):
            btn.setEnabled(False)
            r2.addWidget(btn)

        self._status = QLabel("HRRR idle")
        self._status.setStyleSheet("color: #8E97AB; font-size: 10px;")
        self._status.setMinimumWidth(120)
        r2.addWidget(self._status)

        self._speed_combo = QComboBox()
        self._speed_combo.setFixedSize(46, 26)
        self._speed_combo.setMaxVisibleItems(4)
        self._speed_combo.setToolTip("Playback speed")
        for label, ms in [("0.5×", 1200), ("1×", 600), ("2×", 300), ("3×", 200)]:
            self._speed_combo.addItem(label, userData=ms)
        self._speed_combo.setCurrentIndex(1)
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_combo)
        col.addWidget(row2)

        outer.addWidget(self._drawer)

    def toggle_drawer(self, checked: bool):
        if checked:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            self.setMaximumHeight(0)
            current = 0
        else:
            current = self.height()
            self.setMaximumHeight(current)
            target = 0

        if self._animation:
            self._animation.stop()
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim

    def set_fields(self, fields) -> None:
        current = self.current_field()
        self._updating = True
        try:
            self._field_combo.clear()
            for field in fields:
                self._field_combo.addItem(field.label, field.field_id)
            idx = self._field_combo.findData(current)
            if idx >= 0:
                self._field_combo.setCurrentIndex(idx)
        finally:
            self._updating = False
        if self._field_combo.count() and self.current_field():
            self.field_changed.emit(self.current_field())

    def set_runs(self, runs) -> None:
        current = self.current_run()
        self._updating = True
        try:
            self._run_combo.clear()
            for run in runs:
                self._run_combo.addItem(run.label, run.run_id)
            idx = self._run_combo.findData(current)
            if idx >= 0:
                self._run_combo.setCurrentIndex(idx)
        finally:
            self._updating = False

    def set_hours(self, hours: list[int]) -> None:
        current = self.current_hour()
        self._hours = [int(hour) for hour in hours]
        idx = self._hours.index(current) if current in self._hours else 0
        self.set_cache_size(len(self._hours))
        self.set_frame(idx)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_visible_checked(self, checked: bool) -> None:
        self._visible_btn.blockSignals(True)
        self._visible_btn.setChecked(checked)
        self._visible_btn.blockSignals(False)

    def current_field(self) -> str:
        data = self._field_combo.currentData()
        return str(data or "")

    def current_run(self) -> str:
        data = self._run_combo.currentData()
        return str(data or "")

    def current_hour(self) -> int:
        if 0 <= self._current_frame < len(self._hours):
            return int(self._hours[self._current_frame])
        return 0

    def set_cache_size(self, n: int) -> None:
        n = max(0, int(n))
        if n == 0:
            self._current_frame = 0
        else:
            self._current_frame = min(self._current_frame, n - 1)
        has_history = n > 1
        for w in (
            self._btn_jump_start,
            self._btn_back,
            self._btn_play,
            self._btn_fwd,
            self._btn_jump_end,
        ):
            w.setEnabled(has_history)

    def set_frame(self, idx: int) -> None:
        if not self._hours:
            self._current_frame = 0
            return
        self._current_frame = max(0, min(len(self._hours) - 1, int(idx)))

    def current_frame(self) -> int:
        return self._current_frame

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def stop_loop(self) -> None:
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)

    def _on_run_changed(self) -> None:
        if not self._updating:
            self.run_changed.emit(self.current_run())

    def _on_field_changed(self) -> None:
        if not self._updating:
            self.field_changed.emit(self.current_field())

    def _on_play_toggled(self, checked: bool) -> None:
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_jump_start(self) -> None:
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self) -> None:
        idx = max(0, len(self._hours) - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_back(self) -> None:
        idx = max(0, self._current_frame - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_forward(self) -> None:
        idx = min(max(0, len(self._hours) - 1), self._current_frame + 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_speed_changed(self, index: int) -> None:
        ms = self._speed_combo.itemData(index)
        if ms is not None:
            self.speed_changed.emit(int(ms))

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div

    def _pbtn(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(32, 26)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        return btn
