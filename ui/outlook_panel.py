from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QTextEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QFont


class OutlookPanel(QWidget):
    closed = pyqtSignal()

    PANEL_WIDTH = 340

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("outlookPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMaximumWidth(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # Title row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        self._title_label = QLabel("DAY 1 CONVECTIVE OUTLOOK")
        self._title_label.setObjectName("outlookPanelTitle")
        title_row.addWidget(self._title_label)
        title_row.addStretch()

        self._close_btn = QToolButton()
        self._close_btn.setText("×")
        self._close_btn.setObjectName("outlookPanelClose")
        self._close_btn.clicked.connect(self.close_panel)
        title_row.addWidget(self._close_btn)

        layout.addLayout(title_row)

        # Text area
        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("outlookPanelText")
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Courier New", 9))
        self._text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._text_edit)

        self._animation = None

    def show_loading(self, title: str):
        self._title_label.setText(title)
        self._text_edit.setPlainText("Loading…")
        if self.maximumWidth() == 0:
            self._open_animated()

    def show_text(self, title: str, text: str):
        self._title_label.setText(title)
        self._text_edit.setPlainText(text)

    def close_panel(self):
        self._close_animated()

    def is_open(self) -> bool:
        return self.maximumWidth() != 0

    def _open_animated(self):
        self._animation = QPropertyAnimation(self, b"maximumWidth")
        self._animation.setDuration(200)
        self._animation.setStartValue(0)
        self._animation.setEndValue(self.PANEL_WIDTH)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.finished.connect(lambda: self.setMaximumWidth(16777215))
        self._animation.start()

    def _close_animated(self):
        self._animation = QPropertyAnimation(self, b"maximumWidth")
        self._animation.setDuration(200)
        self._animation.setStartValue(self.width())
        self._animation.setEndValue(0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.finished.connect(self.closed.emit)
        self._animation.start()
