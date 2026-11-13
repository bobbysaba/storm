"""Small accent dot in the top-left corner of a toolbar button whose drawer is
pinned open. The top-right corner is the layer status dot's."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractButton, QLabel


class PinDot(QLabel):
    SIZE = 6
    INSET = 3

    def __init__(self, button: QAbstractButton):
        super().__init__(button)
        self.setObjectName("drawerPinDot")
        self.setFixedSize(self.SIZE, self.SIZE)
        # clicks and right-clicks go to the button underneath
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.move(self.INSET, self.INSET)
        self.hide()
