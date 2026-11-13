"""Settings pill: opened by the gear button at the top right of the map.

Text size, the live loop's VEH, the keyboard shortcut list and reset
layout. It holds no settings of its own: MainWindow sets what is current with
the ``set_*`` methods and acts on the signals, as with the toolbar drawers.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ui import palette as P
from ui.controls.map_controls import _MAP_BUTTON_STYLE
from ui.widgets.drawer import Drawer


class SettingsPill(Drawer):
    text_scale_chosen = pyqtSignal(int)
    vehicles_toggled = pyqtSignal(bool)
    shortcuts_clicked = pyqtSignal()
    reset_clicked = pyqtSignal()

    def __init__(self, scales, parent=None):
        super().__init__(parent)
        self.setObjectName("floatingToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        row = self._row(col, "TEXT SIZE", "Size of all interface text")
        self._scale_group = QButtonGroup(self)
        self._scale_buttons: dict[int, QToolButton] = {}
        for pct in scales:
            btn = self._btn(f"{pct}%", f"Interface text at {pct}%")
            self._scale_group.addButton(btn)
            self._scale_buttons[pct] = btn
            btn.toggled.connect(lambda on, p=pct: on and self.text_scale_chosen.emit(p))
            row.addWidget(btn)
        row.addStretch(1)

        self._loop_row = QWidget()
        row = self._row(col, "LOOP", "What follows the radar loop", self._loop_row)
        self.btn_vehicles = self._btn("VEH", "Vehicles follow the loop, shown where they were")
        self.btn_vehicles.toggled.connect(self.vehicles_toggled)
        row.addWidget(self.btn_vehicles)
        row.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_shortcuts = self._btn("KEYBOARD SHORTCUTS", "Show the list of keys (?)")
        self.btn_shortcuts.setCheckable(False)
        self.btn_shortcuts.clicked.connect(self.shortcuts_clicked)
        row.addWidget(self.btn_shortcuts)
        self.btn_reset = self._btn("RESET LAYOUT", "Close all drawers, default pins and 100% text")
        self.btn_reset.setCheckable(False)
        self.btn_reset.clicked.connect(self.reset_clicked)
        row.addWidget(self.btn_reset)
        row.addStretch(1)
        col.addLayout(row)
        outer.addWidget(body)

    # ── state from the window ─────────────────────────────
    def set_text_scale(self, pct: int) -> None:
        btn = self._scale_buttons.get(pct)
        if btn is not None:
            _quiet(btn, True)

    def choose_text_scale(self, pct: int) -> None:
        """Pick a text size through its button, as clicking it does."""
        btn = self._scale_buttons.get(pct)
        if btn is not None:
            btn.setChecked(True)

    def set_vehicles(self, vehicles: bool) -> None:
        _quiet(self.btn_vehicles, vehicles)

    def set_loop_available(self, available: bool) -> None:
        """No live loop (ARCHIVE, radar disabled): the LOOP row is hidden."""
        self._loop_row.setVisible(available)
        self.content_changed()

    def _row(self, col, title, tip, container=None) -> QHBoxLayout:
        container = container or QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        label = QLabel(title)
        label.setFixedWidth(72)
        label.setToolTip(tip)
        label.setStyleSheet(
            f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;")
        row.addWidget(label)
        col.addWidget(container)
        return row

    @staticmethod
    def _btn(label: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setStyleSheet(_MAP_BUTTON_STYLE)
        return btn


def _quiet(button: QToolButton, checked: bool) -> None:
    button.blockSignals(True)
    button.setChecked(checked)
    button.blockSignals(False)
