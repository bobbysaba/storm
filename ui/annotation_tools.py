# ui/annotation_tools.py
# Collapsible toolbar drawer for road-condition annotation tool selection.
# Mirrors the RadarControls pattern: animated maximumWidth, same toggle_drawer API.

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QFrame
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer

from config import ACCENT_COLOR
from core.annotation import ANNOTATION_TYPES


class AnnotationButton(QToolButton):
    """Single annotation-type icon button with active-state highlight."""

    def __init__(self, type_key: str, symbol: str, label: str, color: str, parent=None):
        super().__init__(parent)
        self._type_key = type_key
        self._color = color
        # two-line text: big symbol on top, short label below
        self.setText(f"{symbol}\n{label}")
        self.setToolTip(label)
        self.setCheckable(False)   # active state managed manually
        self.setFixedHeight(38)
        self.setMinimumWidth(64)
        self._set_style(False)

    @property
    def type_key(self) -> str:
        return self._type_key

    def set_active(self, active: bool):
        self._set_style(active)

    def _set_style(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: {self._color};
                    border: 1px solid {self._color};
                    border-radius: 6px;
                    color: #0A0A0F;
                    font-size: 9px;
                    font-weight: 700;
                    padding: 2px 6px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: transparent;
                    border: 1px solid #2E2E4E;
                    border-radius: 6px;
                    color: {self._color};
                    font-size: 9px;
                    font-weight: 600;
                    padding: 2px 6px;
                }}
                QToolButton:hover {{
                    background-color: #1A1A2E;
                    border-color: {self._color};
                }}
            """)


class AnnotationTools(QWidget):
    """
    Collapsible toolbar drawer for annotation type selection.

    Signals:
        tool_selected(str) — type_key when a button is activated;
                             empty string when deactivated.
    """

    tool_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_type: str = ""
        self._buttons: list[AnnotationButton] = []
        self._animation = None
        self._expanded_width = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.setMaximumWidth(0)   # starts collapsed

        self._drawer = QWidget()
        drawer_layout = QHBoxLayout(self._drawer)
        drawer_layout.setContentsMargins(4, 0, 4, 0)
        drawer_layout.setSpacing(4)

        for type_def in ANNOTATION_TYPES:
            btn = AnnotationButton(
                type_key=type_def["key"],
                symbol=type_def["symbol"],
                label=type_def["label"],
                color=type_def["color"],
            )
            btn.clicked.connect(lambda checked, b=btn: self._on_button_clicked(b))
            self._buttons.append(btn)
            drawer_layout.addWidget(btn)

        # ── separator + storm motion ───────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        drawer_layout.addWidget(sep)

        btn_storm = AnnotationButton(
            type_key="storm_motion",
            symbol="⟶",
            label="Storm Motion",
            color=ACCENT_COLOR,
        )
        btn_storm.clicked.connect(lambda checked, b=btn_storm: self._on_button_clicked(b))
        self._buttons.append(btn_storm)
        drawer_layout.addWidget(btn_storm)

        layout.addWidget(self._drawer)
        QTimer.singleShot(0, self._measure_expanded_width)

    def _measure_expanded_width(self):
        self.setMaximumWidth(16777215)
        self._expanded_width = self.sizeHint().width()
        self.setMaximumWidth(0)

    # ── Public API ────────────────────────────────────────────────────────────

    def active_type(self) -> str:
        return self._active_type

    def deactivate_tool(self):
        """Clear the active tool without emitting tool_selected."""
        self._active_type = ""
        for btn in self._buttons:
            btn.set_active(False)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def toggle_drawer(self, checked: bool):
        """Animate open or closed — called by the toolbar ANNOTATE button."""
        if not checked:
            self.deactivate_tool()
            self.tool_selected.emit("")

        target = self._expanded_width if checked else 0
        if checked:
            current = self.maximumWidth()
            if target == 0:
                self.setMaximumWidth(16777215)
                target = self.sizeHint().width()
                self.setMaximumWidth(current)
        else:
            current = self.width()
            self.setMaximumWidth(current)

        anim = QPropertyAnimation(self, b"maximumWidth")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumWidth(16777215))
        anim.start()
        self._animation = anim   # keep ref alive for animation duration

    def _on_button_clicked(self, btn: AnnotationButton):
        if self._active_type == btn.type_key:
            # clicking the active tool deactivates it
            self._active_type = ""
            btn.set_active(False)
            self.tool_selected.emit("")
        else:
            for b in self._buttons:
                b.set_active(False)
            self._active_type = btn.type_key
            btn.set_active(True)
            self.tool_selected.emit(btn.type_key)
