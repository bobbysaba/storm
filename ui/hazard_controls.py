# ui/hazard_controls.py
# Collapsible toolbar drawer for SPC/NWS hazard overlays.

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QFrame, QToolButton
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer, Qt


class HazardControls(QWidget):
    """
    Floating drawer for hazard layer toggles.

    OUTLOOK / TOR / WIND / HAIL are mutually exclusive SPC overlay modes.
    WATCHES, MDs, and NWS WARNINGS are independent additive overlays.
    All seven are QToolButton (checkable) for visual consistency.
    """

    spc_mode_changed     = pyqtSignal(str)   # "", "outlook", "tor", "wind", "hail"
    spc_watches_toggled  = pyqtSignal(bool)
    spc_mds_toggled      = pyqtSignal(bool)
    nws_warnings_toggled = pyqtSignal(bool)
    fetch_requested      = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._expanded_height = 0
        self._updating_spc_mode = False
        self._setup_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # "hazardDrawer" picks up the blue-active CSS block in theme.py and
        # the existing radarDrawer transparency rules via a parallel selector.
        self._drawer = QWidget()
        self._drawer.setObjectName("hazardDrawer")
        row = QHBoxLayout(self._drawer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        # ── Mutually-exclusive SPC overlay modes ──────────────────────────────
        self._btn_outlook = self._btn("OUTLOOK")
        self._btn_tor     = self._btn("TOR")
        self._btn_wind    = self._btn("WIND")
        self._btn_hail    = self._btn("HAIL")

        self._btn_outlook.toggled.connect(lambda on: self._on_spc_mode_toggled("outlook", on))
        self._btn_tor.toggled.connect(    lambda on: self._on_spc_mode_toggled("tor",     on))
        self._btn_wind.toggled.connect(   lambda on: self._on_spc_mode_toggled("wind",    on))
        self._btn_hail.toggled.connect(   lambda on: self._on_spc_mode_toggled("hail",    on))

        for b in (self._btn_outlook, self._btn_tor, self._btn_wind, self._btn_hail):
            row.addWidget(b)

        row.addWidget(self._vdiv())

        # ── Additive SPC layers ───────────────────────────────────────────────
        self._btn_watches = self._btn("WATCHES")
        self._btn_mds     = self._btn("MDs")
        self._btn_watches.toggled.connect(self._on_spc_watches_toggled)
        self._btn_mds.toggled.connect(self._on_spc_mds_toggled)
        row.addWidget(self._btn_watches)
        row.addWidget(self._btn_mds)

        row.addWidget(self._vdiv())

        # ── NWS ───────────────────────────────────────────────────────────────
        self._btn_nws_warnings = self._btn("NWS WARNINGS")
        self._btn_nws_warnings.toggled.connect(self._on_nws_warnings_toggled)
        row.addWidget(self._btn_nws_warnings)

        row.addStretch(1)
        layout.addWidget(self._drawer)
        QTimer.singleShot(0, self._measure_expanded_height)

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

    # ── Collapse / expand ──────────────────────────────────────────────────────

    def _measure_expanded_height(self):
        self.setMaximumHeight(16777215)
        self._expanded_height = self.sizeHint().height()
        self.setMaximumHeight(0)

    def toggle_drawer(self, checked: bool):
        target = self._expanded_height if checked else 0
        if checked:
            current = self.maximumHeight()
            if target == 0:
                self.setMaximumHeight(16777215)
                target = self.sizeHint().height()
                self.setMaximumHeight(current)
        else:
            current = self.height()
            self.setMaximumHeight(current)

        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(self._expanded_height))
        anim.start()
        self._animation = anim

    # ── Public API ─────────────────────────────────────────────────────────────

    def deactivate_all(self):
        self._set_spc_mode("")
        self.spc_mode_changed.emit("")
        for btn in (self._btn_watches, self._btn_mds, self._btn_nws_warnings):
            btn.setChecked(False)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _set_spc_mode(self, mode: str):
        self._updating_spc_mode = True
        try:
            self._btn_outlook.setChecked(mode == "outlook")
            self._btn_tor.setChecked(    mode == "tor")
            self._btn_wind.setChecked(   mode == "wind")
            self._btn_hail.setChecked(   mode == "hail")
        finally:
            self._updating_spc_mode = False

    def _on_spc_mode_toggled(self, mode: str, checked: bool):
        if self._updating_spc_mode:
            return
        if checked:
            self._set_spc_mode(mode)
            self.spc_mode_changed.emit(mode)
            self.fetch_requested.emit()
        else:
            if not any((
                self._btn_outlook.isChecked(),
                self._btn_tor.isChecked(),
                self._btn_wind.isChecked(),
                self._btn_hail.isChecked(),
            )):
                self.spc_mode_changed.emit("")

    def _on_spc_watches_toggled(self, checked: bool):
        self.spc_watches_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_spc_mds_toggled(self, checked: bool):
        self.spc_mds_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_nws_warnings_toggled(self, checked: bool):
        self.nws_warnings_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()
