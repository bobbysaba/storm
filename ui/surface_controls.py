from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QLabel, QCheckBox
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt


class SurfaceControls(QWidget):
    """Collapsible drawer for surface observation overlays."""

    ok_toggled = pyqtSignal(bool)
    wtm_toggled = pyqtSignal(bool)
    metar_toggled = pyqtSignal(bool)
    plots_toggled = pyqtSignal(bool)
    content_resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("surfaceDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._btn_ok = self._btn("OK MESONET")
        self._btn_wtm = self._btn("WTM")
        self._btn_metar = self._btn("METAR/ASOS")
        self._btn_ok.toggled.connect(self.ok_toggled.emit)
        self._btn_wtm.toggled.connect(self.wtm_toggled.emit)
        self._btn_metar.toggled.connect(self.metar_toggled.emit)
        r1.addWidget(self._btn_ok)
        r1.addWidget(self._btn_wtm)
        r1.addWidget(self._btn_metar)

        r1.addWidget(self._vdiv())

        self._chk_show_plots = QCheckBox("show plots")
        self._chk_show_plots.setChecked(True)
        self._chk_show_plots.toggled.connect(self.plots_toggled.emit)
        r1.addWidget(self._chk_show_plots)

        r1.addStretch(1)
        col.addWidget(row1)

        self._status = QLabel("Surface obs idle")
        self._status.setStyleSheet(
            "color: #B5BDCC; font-size: 10px; letter-spacing: 0.4px; padding: 1px 4px 2px 4px;"
        )
        col.addWidget(self._status)

        outer.addWidget(self._drawer)

    def _btn(self, label: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setCheckable(True)
        btn.setChecked(False)
        return btn

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

    def set_status(self, text: str):
        self._status.setText(text)
        if self.maximumHeight() > 0:
            self.content_resized.emit()

    def plots_visible(self) -> bool:
        return self._chk_show_plots.isChecked()
