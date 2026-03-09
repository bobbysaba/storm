# ui/hazard_controls.py
# Collapsible toolbar drawer for SPC/NWS hazard overlays.

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QLabel
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt
from PyQt6.QtGui import QFont


class HazardControls(QWidget):
    """
    Floating drawer for hazard layer toggles with an inline color legend.

    OUTLOOK / TOR / WIND / HAIL are mutually exclusive SPC overlay modes.
    WATCHES, MDs, and NWS WARNINGS are independent additive overlays.
    A compact legend row appears below the buttons when any product is active.
    """

    spc_mode_changed     = pyqtSignal(str)   # "", "outlook", "tor", "wind", "hail"
    spc_watches_toggled  = pyqtSignal(bool)
    spc_mds_toggled      = pyqtSignal(bool)
    nws_warnings_toggled = pyqtSignal(bool)
    fetch_requested      = pyqtSignal()

    # Colored-swatch entries per product layer.  Each entry is (hex_color, short_label).
    PRODUCT_LEGENDS: dict[str, list[tuple[str, str]]] = {
        "spc-cat": [
            ("#80C580", "MRGL"),
            ("#F6F67F", "SLGHT"),
            ("#E87038", "ENH"),
            ("#E84038", "MDT"),
            ("#930093", "HIGH"),
        ],
        "spc-tor": [
            ("#008B00", "2%"),
            ("#8B4726", "5%"),
            ("#FFA500", "10%"),
            ("#FF0000", "15%"),
            ("#FF00FF", "30%"),
            ("#912CEE", "45%"),
            ("#104E8B", "60%"),
        ],
        "spc-wind": [
            ("#C1A353", "5%"),
            ("#FFFF00", "15%"),
            ("#FF6600", "30%"),
            ("#FF0000", "45%"),
            ("#FF00FF", "60%"),
        ],
        "spc-hail": [
            ("#C1A353", "5%"),
            ("#FFFF00", "15%"),
            ("#FF6600", "30%"),
            ("#FF0000", "45%"),
            ("#FF00FF", "60%"),
        ],
        "spc-watches": [("#FF0000", "TOR Watch"), ("#FFD700", "SVR Tstm Watch")],
        "spc-mds":     [("#FF66CC", "MDs")],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating_spc_mode = False
        self._setup_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("hazardDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        # ── Button row ────────────────────────────────────────────────────────
        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

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

        self._btn_watches = self._btn("WATCHES")
        self._btn_mds     = self._btn("MDs")
        self._btn_watches.toggled.connect(self._on_spc_watches_toggled)
        self._btn_mds.toggled.connect(self._on_spc_mds_toggled)
        row.addWidget(self._btn_watches)
        row.addWidget(self._btn_mds)

        row.addWidget(self._vdiv())

        self._btn_nws_warnings = self._btn("NWS WARNINGS")
        self._btn_nws_warnings.toggled.connect(self._on_nws_warnings_toggled)
        row.addWidget(self._btn_nws_warnings)

        row.addStretch(1)
        col.addWidget(btn_row)

        # ── Legend row (hidden until a product is active) ─────────────────────
        self._legend_label = QLabel()
        self._legend_label.setTextFormat(Qt.TextFormat.RichText)
        self._legend_label.setFont(QFont("Helvetica Neue", 9))
        self._legend_label.setStyleSheet(
            "color: #B5BDCC; background: transparent; padding: 1px 4px 2px 4px;"
        )
        self._legend_label.setVisible(False)
        col.addWidget(self._legend_label)

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

    def update_legend(self, active_products: list[str]):
        """Show a compact color-swatch legend for the active hazard products."""
        entries: list[tuple[str, str]] = []
        for product in active_products:
            entries.extend(self.PRODUCT_LEGENDS.get(product, []))

        if entries:
            parts = [
                f'<span style="color:{color}; font-size:11px;">■</span>'
                f'&thinsp;<span style="font-size:9px;">{label}</span>'
                for color, label in entries
            ]
            self._legend_label.setText("&nbsp;&nbsp;".join(parts))
            self._legend_label.setVisible(True)
        else:
            self._legend_label.setVisible(False)

        # If the drawer is open, animate to the new natural height so the
        # parent layout doesn't squish the content into the old allocation.
        if self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            current = self.height()
            if target == current:
                return
            anim = QPropertyAnimation(self, b"maximumHeight")
            anim.setDuration(120)
            anim.setStartValue(current)
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
            anim.start()
            self._animation = anim

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
