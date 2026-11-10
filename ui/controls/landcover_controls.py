from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QGridLayout, QHBoxLayout, QSlider, QWidget, QVBoxLayout
from ui import palette as P
from ui.widgets.drawer import Drawer


_NLCD_LEGEND: tuple[tuple[str, str], ...] = (
    ("#466B9F", "Water"),
    ("#D1DEF8", "Ice"),
    ("#DEC5C5", "Dev Open"),
    ("#D99282", "Dev Low"),
    ("#EB0000", "Dev Med"),
    ("#AB0000", "Dev High"),
    ("#B3AC9F", "Barren"),
    ("#68AB5F", "Decid Forest"),
    ("#1C5F2C", "Ever Forest"),
    ("#B5C58F", "Mixed Forest"),
    ("#CCB879", "Shrub"),
    ("#DFDFC2", "Grass"),
    ("#DCD939", "Pasture"),
    ("#AB6C28", "Crops"),
    ("#B8D9EB", "Woody Wet"),
    ("#6C9FB8", "Herb Wet"),
)


class LandcoverControls(Drawer):
    """Small drawer for the optional offline NLCD land-cover raster."""

    opacity_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("landcoverDrawer")
        self._drawer.setStyleSheet("background: transparent; background-color: transparent;")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        row = QWidget()
        row.setStyleSheet("background: transparent; background-color: transparent;")
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(6)

        lbl = QLabel("OPACITY")
        lbl.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px; letter-spacing: 0.5px;")
        lbl.setFixedHeight(22)
        r.addWidget(lbl)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(55)
        self._opacity_slider.setFixedHeight(22)
        self._opacity_slider.setFixedWidth(130)
        self._opacity_slider.setToolTip("NLCD land-cover opacity")
        self._opacity_slider.valueChanged.connect(
            lambda value: self.opacity_changed.emit(value / 100.0)
        )
        r.addWidget(self._opacity_slider)
        r.addStretch(1)
        col.addWidget(row)

        legend = QWidget()
        legend.setObjectName("landcoverLegend")
        legend.setStyleSheet("background: transparent; background-color: transparent;")
        grid = QGridLayout(legend)
        grid.setContentsMargins(0, 1, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)

        rows = 4
        for idx, (color, label) in enumerate(_NLCD_LEGEND):
            col_idx = idx // rows
            row_idx = idx % rows
            item = QWidget()
            item.setStyleSheet("background: transparent; background-color: transparent;")
            item_row = QHBoxLayout(item)
            item_row.setContentsMargins(0, 0, 0, 0)
            item_row.setSpacing(4)

            swatch = QLabel()
            swatch.setFixedSize(12, 8)
            swatch.setStyleSheet(
                f"background: {color}; border: 1px solid {P.alpha(P.WHITE, 0.35)};"
            )
            item_row.addWidget(swatch)

            text = QLabel(label)
            text.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px;")
            text.setFixedHeight(14)
            item_row.addWidget(text)
            grid.addWidget(item, row_idx, col_idx)

        col.addWidget(legend)

        outer.addWidget(self._drawer)

    def opacity(self) -> float:
        return self._opacity_slider.value() / 100.0
