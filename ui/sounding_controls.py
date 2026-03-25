# ui/sounding_controls.py
# Collapsible toolbar drawer for selecting HRRR model vs observed sounding source.

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt


class SoundingControls(QWidget):
    """Floating drawer that appears when the SOUNDING toolbar button is active.

    Presents two mutually-exclusive source buttons:
        [HRRR]  — click anywhere on map to fetch HRRR point sounding (existing)
        [OBS]   — radiosonde station dots appear; click one to fetch observed data
    """

    mode_changed   = pyqtSignal(str)   # "hrrr" or "obs"
    content_resized = pyqtSignal()     # triggers layout pulse in main_window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation   = None
        self._active_mode = "hrrr"
        self._setup_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("soundingDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._btn_hrrr = self._btn("HRRR")
        self._btn_obs  = self._btn("OBS")

        self._btn_hrrr.setChecked(True)   # default source

        self._btn_hrrr.toggled.connect(lambda on: self._on_mode_toggled("hrrr", on))
        self._btn_obs.toggled.connect( lambda on: self._on_mode_toggled("obs",  on))

        row.addWidget(self._btn_hrrr)
        row.addWidget(self._vdiv())
        row.addWidget(self._btn_obs)
        row.addStretch(1)

        col.addWidget(btn_row)
        outer.addWidget(self._drawer)

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div

    # ── Mode toggle ────────────────────────────────────────────────────────────

    def _on_mode_toggled(self, mode: str, checked: bool):
        if not checked:
            # Prevent both buttons from being unchecked simultaneously
            if self._active_mode == mode:
                # Re-check the button that was just unchecked
                btn = self._btn_hrrr if mode == "hrrr" else self._btn_obs
                btn.blockSignals(True)
                btn.setChecked(True)
                btn.blockSignals(False)
            return

        self._active_mode = mode

        # Enforce mutual exclusivity
        other_btn = self._btn_obs if mode == "hrrr" else self._btn_hrrr
        other_btn.blockSignals(True)
        other_btn.setChecked(False)
        other_btn.blockSignals(False)

        self.mode_changed.emit(mode)

    # ── Collapse / expand ──────────────────────────────────────────────────────

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

    # ── Public API ─────────────────────────────────────────────────────────────

    def reset_to_hrrr(self):
        """Silently reset to HRRR mode (called when SOUNDING button is deactivated)."""
        self._active_mode = "hrrr"
        self._btn_hrrr.blockSignals(True)
        self._btn_obs.blockSignals(True)
        self._btn_hrrr.setChecked(True)
        self._btn_obs.setChecked(False)
        self._btn_hrrr.blockSignals(False)
        self._btn_obs.blockSignals(False)

    @property
    def active_mode(self) -> str:
        return self._active_mode
