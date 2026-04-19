
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
    nws_filter_changed   = pyqtSignal(object)
    cwa_toggled          = pyqtSignal(bool)
    fetch_requested      = pyqtSignal()
    content_resized      = pyqtSignal()      # triggers layout pulse in main_window

    # colored-swatch entries per product layer.  Each entry is (hex_color, short_label).
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
        "spc-watches": [("#FF0000", "TOR Watch"), ("#4169E1", "SVR Tstm Watch")],
        "spc-mds":     [("#FF66CC", "MDs")],
    }

    # per-phenom color + short label for NWS warnings legend.
    NWS_PHENOM_LEGEND: list[tuple[str, str, str]] = [
        # (phenom_code, hex_color, short_label)
        ("TO", "#FF0000",  "TOR Warn"),
        ("SV", "#FFD700",  "SVR Warn"),
        ("FF", "#00FF00",  "FFD Warn"),
        ("FL", "#00FF7F",  "FL Warn"),
        ("MA", "#87CEEB",  "Marine Warn"),
        ("WS", "#FF69B4",  "Win Storm"),
        ("BZ", "#FF4500",  "Blizzard"),
        ("WW", "#FF69B4",  "Win Wx"),
        ("HU", "#DA70D6",  "Hurricane"),
        ("TS", "#DA70D6",  "Trop Storm"),
        ("HF", "#DA70D6",  "HF Wind"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating_spc_mode = False
        # new members for the toggleable legend (Step 1)
        self._nws_filter_set: set[str] | None = None      # None = show all phenoms; set = show only these
        self._last_nws_phenoms: set[str] = set()
        self._setup_ui()

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

        # nws County Warning Areas (CWA) overlay toggle
        self._btn_cwa = self._btn("NWS CWA")
        self._btn_cwa.toggled.connect(self._on_cwa_toggled)
        row.addWidget(self._btn_cwa)

        row.addStretch(1)
        col.addWidget(btn_row)

        self._legend_widget = QWidget()
        self._legend_layout = QHBoxLayout(self._legend_widget)
        self._legend_layout.setContentsMargins(4, 1, 4, 2)
        self._legend_layout.setSpacing(6)
        self._legend_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._legend_widget.setVisible(False)
        col.addWidget(self._legend_widget)

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

    def update_legend(self, active_products: list[str], nws_phenoms: set[str] | None = None):
        """Rebuild the legend. SPC entries are static; NWS warning entries are now toggleable buttons."""
        # clear all existing widgets
        while self._legend_layout.count():
            child = self._legend_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        phenom_set = nws_phenoms or set()
        self._last_nws_phenoms = phenom_set.copy()

        # 1. add static SPC entries
        for product in active_products:
            if product != "nws-warnings":
                for color, label in self.PRODUCT_LEGENDS.get(product, []):
                    lbl = QLabel()
                    lbl.setText(
                        f'<span style="color:{color}; font-size:11px;">■</span>'
                        f'&thinsp;<span style="font-size:9px;">{label}</span>'
                    )
                    lbl.setFont(QFont("Helvetica Neue", 9))
                    lbl.setStyleSheet(
                        "color: #B5BDCC; background: transparent; padding: 1px 4px 2px 4px;"
                    )
                    self._legend_layout.addWidget(lbl)

        # 2. add toggleable NWS entries
        if "nws-warnings" in active_products and phenom_set:
            if self._legend_layout.count() > 0:
                # add subtle separator only if something was already added (SPC)
                sep = QLabel("│")
                sep.setStyleSheet("color: #4A4A5E; padding: 0 6px;")
                self._legend_layout.addWidget(sep)

            for code, color, label in self.NWS_PHENOM_LEGEND:
                if code in phenom_set:
                    btn = self._create_nws_legend_item(code, color, label)
                    self._legend_layout.addWidget(btn)
        
        self._legend_layout.addStretch(1)
        # show legend only if we added at least one item
        self._legend_widget.setVisible(self._legend_layout.count() > 0)

        # auto-resize animation if the drawer is open
        if self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
            target = self.sizeHint().height()
            current = self.height()
            if target != current:
                if self._animation:
                    self._animation.stop()
                anim = QPropertyAnimation(self, b"maximumHeight")
                anim.setDuration(120)
                anim.setStartValue(current)
                anim.setEndValue(target)
                anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
                anim.finished.connect(lambda: self.setMaximumHeight(16777215))
                anim.start()
                self._animation = anim
                self.content_resized.emit()


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

    def _on_cwa_toggled(self, checked: bool):
        """Emit cwa_toggled when the CWA button changes. No fetch required."""
        self.cwa_toggled.emit(checked)

    def _create_nws_legend_item(self, code: str, color: str, label: str) -> QToolButton:
        """Create a toggleable legend entry for one NWS phenom type."""
        btn = QToolButton()
        btn.setCheckable(True)
        btn.setText(f"■ {label}")
        btn.setToolTip(f"Toggle {label} (click to show/hide)")
        btn.setStyleSheet(f"""
            QToolButton {{
                color: {color};
                font-size: 9px;
                padding: 2px 6px;
                background: transparent;
                border: none;
                border-radius: 3px;
            }}
            QToolButton:checked {{
                background: rgba(255, 255, 255, 0.12);
                font-weight: 600;
            }}
            QToolButton:hover {{
                background: rgba(255, 255, 255, 0.06);
            }}
        """)

        # set initial state from current filter (no signal emitted)
        is_checked = (self._nws_filter_set is None) or (code in self._nws_filter_set)
        btn.blockSignals(True)
        btn.setChecked(is_checked)
        btn.blockSignals(False)

        btn.toggled.connect(lambda checked, c=code: self._on_nws_legend_toggled(c, checked))
        return btn

    def _on_nws_legend_toggled(self, code: str, checked: bool):
        # initialize the set if it's currently 'Show All'
        if self._nws_filter_set is None:
            self._nws_filter_set = self._last_nws_phenoms.copy()

        if checked:
            self._nws_filter_set.add(code)
        else:
            self._nws_filter_set.discard(code)

        # only reset to None if it actually matches the full set again
        if self._nws_filter_set == self._last_nws_phenoms:
            self._nws_filter_set = None

        self.nws_filter_changed.emit(self._nws_filter_set)

    def set_nws_filter_selected(self, codes: set[str] | None, emit: bool = True):
        """Programmatically set which NWS phenom codes are enabled in the legend.

        This is called from outside (e.g. main window restoring state).
        The legend buttons will reflect the new state the next time update_legend() runs.
        """
        # normalize input
        if codes:
            self._nws_filter_set = {str(c).strip().upper() for c in codes}
        else:
            self._nws_filter_set = None   # None means "show all"

        if emit:
            self.nws_filter_changed.emit(self._nws_filter_set)
