from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QSlider,
    QToolButton, QPushButton, QFrame,
)


class HrrrControls(QWidget):
    """Compact drawer for server-rendered HRRR overlay products."""

    field_changed = pyqtSignal(str)
    hour_changed = pyqtSignal(int)
    opacity_changed = pyqtSignal(float)
    refresh_requested = pyqtSignal()
    visible_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating = False
        self._setup_ui()
        self._apply_local_style()

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

        self._field_combo = QComboBox()
        self._field_combo.setObjectName("hrrrFieldCombo")
        self._field_combo.setMinimumWidth(132)
        self._field_combo.currentIndexChanged.connect(lambda _idx: self._on_field_changed())
        r1.addWidget(self._field_combo)

        self._hour_combo = QComboBox()
        self._hour_combo.setObjectName("hrrrHourCombo")
        self._hour_combo.setMinimumWidth(70)
        self._hour_combo.currentIndexChanged.connect(lambda _idx: self._on_hour_changed())
        r1.addWidget(self._hour_combo)

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
        r2.setContentsMargins(2, 0, 2, 0)
        r2.setSpacing(8)
        self._status = QLabel("HRRR idle")
        self._status.setStyleSheet("color: #8E97AB; font-size: 10px;")
        r2.addWidget(self._status)
        r2.addStretch(1)
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

    def set_hours(self, hours: list[int]) -> None:
        current = self.current_hour()
        self._updating = True
        try:
            self._hour_combo.clear()
            for hour in hours:
                self._hour_combo.addItem(f"F{hour:02d}", int(hour))
            idx = self._hour_combo.findData(current)
            if idx >= 0:
                self._hour_combo.setCurrentIndex(idx)
        finally:
            self._updating = False

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_visible_checked(self, checked: bool) -> None:
        self._visible_btn.blockSignals(True)
        self._visible_btn.setChecked(checked)
        self._visible_btn.blockSignals(False)

    def current_field(self) -> str:
        data = self._field_combo.currentData()
        return str(data or "")

    def current_hour(self) -> int:
        data = self._hour_combo.currentData()
        try:
            return int(data)
        except (TypeError, ValueError):
            return 0

    def _on_field_changed(self) -> None:
        if not self._updating:
            self.field_changed.emit(self.current_field())

    def _on_hour_changed(self) -> None:
        if not self._updating:
            self.hour_changed.emit(self.current_hour())

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div

    def _apply_local_style(self) -> None:
        self.setStyleSheet("""
            HrrrControls {
                background: transparent;
                border: none;
            }
            QWidget#hrrrDrawer,
            QWidget#hrrrControlRow,
            QWidget#hrrrStatusRow {
                background: transparent;
                border: none;
            }
            QWidget#hrrrDrawer QToolButton {
                background-color: transparent;
                border: 1px solid rgba(74, 83, 108, 0.42);
                border-radius: 6px;
                color: #C8D0DE;
                padding: 3px 8px;
            }
            QWidget#hrrrDrawer QToolButton:hover {
                background-color: rgba(74, 158, 255, 0.08);
                border-color: rgba(74, 158, 255, 0.55);
            }
            QWidget#hrrrDrawer QToolButton:checked {
                background-color: rgba(74, 158, 255, 0.18);
                border-color: #4A9EFF;
                color: #EFF3FF;
            }
            QWidget#hrrrDrawer QComboBox {
                background-color: rgba(32, 37, 58, 0.62);
                border: 1px solid rgba(74, 83, 108, 0.48);
                border-radius: 6px;
                color: #D9E0EC;
                min-height: 24px;
                padding: 2px 7px;
            }
            QWidget#hrrrDrawer QPushButton {
                background: transparent;
                border: 1px solid rgba(74, 83, 108, 0.48);
                border-radius: 6px;
                color: #C8D0DE;
                font-size: 10px;
                font-weight: 600;
                padding: 2px 8px;
            }
            QWidget#hrrrDrawer QPushButton:hover {
                background-color: rgba(74, 158, 255, 0.08);
                border-color: rgba(74, 158, 255, 0.55);
                color: #EFF3FF;
            }
            QWidget#hrrrDrawer QLabel {
                background: transparent;
            }
            QWidget#hrrrDrawer QSlider {
                background: transparent;
            }
            QWidget#hrrrDrawer QSlider::groove:horizontal {
                background-color: rgba(184, 191, 205, 0.28);
                height: 2px;
                border-radius: 1px;
            }
        """)
