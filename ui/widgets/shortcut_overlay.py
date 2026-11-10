"""The ``?`` panel: every shortcut for the current mode, built from ui/shortcuts.py."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import shortcuts


class ShortcutOverlay(QFrame):
    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self.setObjectName("shortcutOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(10)

        title = QLabel("KEYBOARD SHORTCUTS")
        title.setObjectName("shortcutOverlayTitle")
        outer.addWidget(title)

        columns = QHBoxLayout()
        columns.setSpacing(28)
        by_group: dict[str, list[shortcuts.Shortcut]] = {}
        for s in shortcuts.for_mode(mode):
            by_group.setdefault(s.group, []).append(s)
        for group in shortcuts.GROUPS:
            if group in by_group:
                columns.addWidget(self._group(group, by_group[group]), 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(columns)

        hint = QLabel("Keys are off while typing in a text box · ? or Esc to close")
        hint.setObjectName("shortcutOverlayHint")
        outer.addWidget(hint)
        self.hide()

    @staticmethod
    def _group(name: str, items: list) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        header = QLabel(name.upper())
        header.setObjectName("shortcutOverlayGroup")
        grid.addWidget(header, 0, 0, 1, 2)
        for row, s in enumerate(items, start=1):
            keys = QLabel("  ".join(shortcuts.display_key(k) for k in s.keys))
            keys.setObjectName("shortcutKey")
            keys.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(keys, row, 0)
            grid.addWidget(QLabel(s.label), row, 1)
        return box

    def toggle(self) -> None:
        self.setVisible(self.isHidden())
        if self.isVisible():
            self.raise_()

    def mousePressEvent(self, event):
        self.hide()
        super().mousePressEvent(event)
