from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QToolButton, QWidget, QVBoxLayout
from ui import palette as P
from ui import text_scale
from ui.widgets.drawer import Drawer

_MAP_BUTTON_STYLE = P.qss("""
QToolButton {
    background: transparent;
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}
QToolButton:hover {
    background: $info@0.08;
    background-color: $info@0.08;
    border-color: $info;
    color: $text_bright;
}
QToolButton:checked,
QToolButton:pressed {
    background: $info@0.18;
    background-color: $info@0.18;
    border-color: $info;
    color: $info;
    font-weight: 600;
}
""")


_DRAWER_MODES = (
    ("auto", "AUTO", "One drawer at a time on short windows, stacked otherwise"),
    ("one", "ONE", "Opening a drawer closes the others (pinned drawers stay open)"),
    ("stack", "STACK", "Drawers stack; the oldest close when they run out of room"),
)


class MapControls(Drawer):
    """Drawer for map-context tools: routing, measuring, and land cover."""

    drawer_mode_changed = pyqtSignal(str)
    text_scale_changed = pyqtSignal(int)
    reset_layout_requested = pyqtSignal()

    def __init__(
        self,
        route_available: bool = True,
        landcover_available: bool = False,
        satellite_available: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._route_available = bool(route_available)
        self._landcover_available = bool(landcover_available)
        self._satellite_available = bool(satellite_available)
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("mapDrawer")
        row = QHBoxLayout(self._drawer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.btn_route = self._btn("ROUTE", "Get turn-by-turn directions")
        self.btn_route.setVisible(self._route_available)
        row.addWidget(self.btn_route)

        self.btn_measure = self._btn("MEASURE", "Click two points to measure distance")
        row.addWidget(self.btn_measure)

        self.btn_landcover = self._btn(
            "LANDCOVER", "Show/hide offline NLCD land-cover overlay"
        )
        self.btn_landcover.setVisible(self._landcover_available)
        row.addWidget(self.btn_landcover)

        self.btn_satellite_basemap = self._btn(
            "SAT", "Show/hide satellite basemap imagery"
        )
        self.btn_satellite_basemap.setVisible(self._satellite_available)
        row.addWidget(self.btn_satellite_basemap)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        row.addWidget(sep)

        mode_label = QLabel("DRAWERS")
        mode_label.setStyleSheet(
            f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        mode_label.setToolTip("How toolbar drawers share the screen. Right-click a toolbar button to pin it.")
        row.addWidget(mode_label)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QToolButton] = {}
        for mode, label, tip in _DRAWER_MODES:
            btn = self._btn(label, tip)
            self._mode_group.addButton(btn)
            self._mode_buttons[mode] = btn
            btn.toggled.connect(lambda on, m=mode: on and self.drawer_mode_changed.emit(m))
            row.addWidget(btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        row.addWidget(sep)

        scale_label = QLabel("TEXT")
        scale_label.setStyleSheet(
            f"color: {P.TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        scale_label.setToolTip("Size of all interface text")
        row.addWidget(scale_label)

        self._scale_group = QButtonGroup(self)
        self._scale_group.setExclusive(True)
        self._scale_buttons: dict[int, QToolButton] = {}
        for pct in text_scale.SCALES:
            btn = self._btn(f"{pct}%", f"Interface text at {pct}%")
            self._scale_group.addButton(btn)
            self._scale_buttons[pct] = btn
            btn.toggled.connect(lambda on, p=pct: on and self.text_scale_changed.emit(p))
            row.addWidget(btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        row.addWidget(sep)

        self.btn_reset_layout = self._btn(
            "RESET LAYOUT", "Close all drawers, default pins, AUTO drawers and 100% text"
        )
        self.btn_reset_layout.setCheckable(False)
        self.btn_reset_layout.clicked.connect(self.reset_layout_requested.emit)
        row.addWidget(self.btn_reset_layout)

        outer.addWidget(self._drawer)

    def set_drawer_mode(self, mode: str) -> None:
        btn = self._mode_buttons.get(mode)
        if btn is not None and not btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)

    def set_text_scale(self, pct: int) -> None:
        btn = self._scale_buttons.get(pct)
        if btn is not None and not btn.isChecked():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)

    def _btn(self, label: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setProperty("mapSubButton", True)
        btn.setStyleSheet(_MAP_BUTTON_STYLE)
        return btn

