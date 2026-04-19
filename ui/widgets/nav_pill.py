
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QToolButton,
)
from PyQt6.QtCore import pyqtSignal, Qt


class NavPill(QWidget):
    """Floating navigation pill.

    Signals:
        open_drawer_requested  — expand the full ROUTE drawer
        clear_requested        — dismiss / clear the route
    """

    open_drawer_requested = pyqtSignal()
    clear_requested       = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)

        self._step_label = QLabel("Navigating…")
        self._step_label.setObjectName("navPillStep")
        self._step_label.setWordWrap(True)

        self._summary_label = QLabel("")
        self._summary_label.setObjectName("navPillSummary")

        col.addWidget(self._step_label)
        col.addWidget(self._summary_label)

        self._btn_expand = QToolButton()
        self._btn_expand.setText("▾")
        self._btn_expand.setObjectName("navPillExpand")
        self._btn_expand.setToolTip("Show full directions")
        self._btn_expand.setFixedSize(32, 32)
        self._btn_expand.clicked.connect(self.open_drawer_requested)

        self._btn_clear = QToolButton()
        self._btn_clear.setText("✕")
        self._btn_clear.setObjectName("navPillClear")
        self._btn_clear.setToolTip("Clear route")
        self._btn_clear.setFixedSize(32, 32)
        self._btn_clear.clicked.connect(self.clear_requested)

        layout.addLayout(col, 1)
        layout.addWidget(self._btn_expand)
        layout.addWidget(self._btn_clear)


    def set_expanded(self, expanded: bool):
        """Toggle the expand button icon to reflect open/closed state."""
        self._btn_expand.setText("▴" if expanded else "▾")

    def update_nav(self, step_text: str, summary: str):
        """Refresh displayed step and summary, then show the pill."""
        self._step_label.setText(step_text)
        self._summary_label.setText(summary)
        self._summary_label.setVisible(bool(summary))
        self.show()
        self.adjustSize()

    def nav_clear(self):
        """Hide the pill (called when the route is cleared)."""
        self.hide()
