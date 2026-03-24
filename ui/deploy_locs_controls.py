# ui/deploy_locs_controls.py
# Collapsible toolbar drawer for previous deployment location color-coding.

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QToolButton, QLabel
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt
from PyQt6.QtGui import QFont


# Best → worst color ramp (rank 1 = green, rank 5 = red)
_RANK_COLORS = ["#2DC653", "#A8C538", "#FFD166", "#FF8C42", "#EF233C"]
_NULL_COLOR  = "#888888"

METRICS = [
    ("rank_abi", "RANK ABI"),
    ("rank_aoi", "RANK AOI"),
    ("rqi",      "RQI"),
]

_LEGENDS = {
    "rank_abi": [
        ("#2DC653", "1"), ("#A8C538", "2"), ("#FFD166", "3"),
        ("#FF8C42", "4"), ("#EF233C", "5"), ("#888888", "N/A"),
    ],
    "rank_aoi": [
        ("#2DC653", "1"), ("#A8C538", "2"), ("#FFD166", "3"),
        ("#FF8C42", "4"), ("#EF233C", "5"), ("#888888", "N/A"),
    ],
    "rqi": [
        ("#2DC653", "0.8–1.0"), ("#A8C538", "0.6–0.8"), ("#FFD166", "0.4–0.6"),
        ("#FF8C42", "0.2–0.4"), ("#EF233C", "0–0.2"),   ("#888888", "N/A"),
    ],
}


class DeployLocsControls(QWidget):
    """
    Floating drawer for previous deployment location color-coding.

    Three mutually exclusive metrics: RANK ABI, RANK AOI, RQI.
    Emits metric_changed(key) whenever the active metric changes.
    """

    metric_changed   = pyqtSignal(str)   # "rank_abi" | "rank_aoi" | "rqi"
    content_resized  = pyqtSignal()      # triggers layout pulse in main_window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation  = None
        self._updating   = False
        self._setup_ui()

    # ── Build ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("deployLocsDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        # ── Metric button row ──────────────────────────────────────────────
        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._btns: dict[str, QToolButton] = {}
        for key, label in METRICS:
            b = self._btn(label)
            b.toggled.connect(lambda on, k=key: self._on_metric_toggled(k, on))
            row.addWidget(b)
            self._btns[key] = b

        row.addStretch(1)
        col.addWidget(btn_row)

        # ── Legend row ────────────────────────────────────────────────────
        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setFont(QFont("Helvetica Neue", 9))
        self._legend.setStyleSheet(
            "color: #B5BDCC; background: transparent; padding: 1px 4px 2px 4px;"
        )
        self._legend.setVisible(False)
        col.addWidget(self._legend)

        outer.addWidget(self._drawer)

        # Default to rank_abi without firing the signal yet
        self._updating = True
        self._btns["rank_abi"].setChecked(True)
        self._updating = False

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b

    # ── Collapse / expand ──────────────────────────────────────────────────

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

    # ── Public API ─────────────────────────────────────────────────────────

    def current_metric(self) -> str:
        for key, b in self._btns.items():
            if b.isChecked():
                return key
        return "rank_abi"

    # ── Internal ───────────────────────────────────────────────────────────

    def _on_metric_toggled(self, key: str, checked: bool):
        if self._updating:
            return
        if checked:
            # Deselect siblings
            self._updating = True
            try:
                for k, b in self._btns.items():
                    if k != key:
                        b.setChecked(False)
            finally:
                self._updating = False
            self.metric_changed.emit(key)
            self._update_legend(key)
        else:
            # Prevent deselecting the last active button
            if not any(b.isChecked() for b in self._btns.values()):
                self._updating = True
                try:
                    self._btns[key].setChecked(True)
                finally:
                    self._updating = False

    def _update_legend(self, metric: str):
        entries = _LEGENDS.get(metric, [])
        parts = [
            f'<span style="color:{c}; font-size:11px;">■</span>'
            f'&thinsp;<span style="font-size:9px;">{lbl}</span>'
            for c, lbl in entries
        ]
        self._legend.setText("&nbsp;&nbsp;".join(parts))
        self._legend.setVisible(True)

        if self.maximumHeight() > 0:
            self.setMaximumHeight(16777215)
            target  = self.sizeHint().height()
            current = self.height()
            if target == current:
                return
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
