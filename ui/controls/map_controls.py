from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget, QVBoxLayout
from ui import palette as P
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




class MapControls(Drawer):
    """Drawer for map-context tools: routing, measuring, annotations and land cover."""


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

        self.btn_annotations = self._btn(
            "ANNOTATIONS",
            "Show or hide annotations: fronts, boundaries and other drawings, "
            "annotation markers, and storm-motion cones",
        )
        self.btn_annotations.setChecked(True)
        row.addWidget(self.btn_annotations)

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

        outer.addWidget(self._drawer)

    def _btn(self, label: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setProperty("mapSubButton", True)
        btn.setStyleSheet(_MAP_BUTTON_STYLE)
        return btn

