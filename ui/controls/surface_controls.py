from datetime import datetime, timezone
from html import escape

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QLabel, QCheckBox
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, Qt

from config import ACCENT_COLOR


class SurfaceControls(QWidget):
    """Collapsible drawer for surface observation overlays."""

    ok_toggled   = pyqtSignal(bool)
    wtm_toggled  = pyqtSignal(bool)
    asos_toggled = pyqtSignal(bool)
    asos_bbox_requested = pyqtSignal()
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
        self._btn_asos = self._btn("ASOS")
        self._btn_ok.toggled.connect(self.ok_toggled.emit)
        self._btn_wtm.toggled.connect(self.wtm_toggled.emit)
        self._btn_asos.toggled.connect(self.asos_toggled.emit)
        self._btn_asos.toggled.connect(self._on_asos_button_toggled)
        r1.addWidget(self._btn_ok)
        r1.addWidget(self._btn_wtm)
        r1.addWidget(self._btn_asos)

        r1.addWidget(self._vdiv())

        controls_col = QVBoxLayout()
        controls_col.setContentsMargins(0, 0, 0, 0)
        controls_col.setSpacing(2)

        self._chk_show_plots = QCheckBox("show plots")
        self._chk_show_plots.setChecked(True)
        self._chk_show_plots.toggled.connect(self.plots_toggled.emit)
        controls_col.addWidget(self._chk_show_plots, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._asos_bbox_link = QLabel(
            f'<a href="new-asos-bbox" style="color:{ACCENT_COLOR}; text-decoration:none;">new ASOS bbox</a>'
        )
        self._asos_bbox_link.setVisible(False)
        self._asos_bbox_link.setTextFormat(Qt.TextFormat.RichText)
        self._asos_bbox_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._asos_bbox_link.setOpenExternalLinks(False)
        self._asos_bbox_link.setStyleSheet(
            "font-size: 9px; font-weight: 500; letter-spacing: 0.2px; padding: 0;"
        )
        self._asos_bbox_link.linkActivated.connect(
            lambda link: self.asos_bbox_requested.emit() if link == "new-asos-bbox" else None
        )
        controls_col.addWidget(self._asos_bbox_link, alignment=Qt.AlignmentFlag.AlignHCenter)
        r1.addLayout(controls_col)

        r1.addStretch(1)
        col.addWidget(row1)

        self._status = QLabel("Surface obs idle")
        self._status.setStyleSheet(
            "color: #B5BDCC; font-size: 10px; letter-spacing: 0.4px; padding: 1px 4px 2px 4px;"
        )
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self._status)

        self._status_div = QFrame()
        self._status_div.setFrameShape(QFrame.Shape.NoFrame)
        self._status_div.setFixedHeight(1)
        self._status_div.setStyleSheet("background-color: #8E97AB; margin: 2px 4px 2px 4px;")
        self._status_div.setVisible(False)
        col.addWidget(self._status_div)

        self._detail = QLabel("")
        self._detail.setStyleSheet(
            "color: #8E97AB; font-size: 9px; font-weight: 600; letter-spacing: 0.35px; padding: 0 4px 1px 4px;"
        )
        self._detail.setTextFormat(Qt.TextFormat.RichText)
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._detail.setVisible(False)
        col.addWidget(self._detail)

        self._legend_div = QFrame()
        self._legend_div.setFrameShape(QFrame.Shape.NoFrame)
        self._legend_div.setFixedHeight(1)
        self._legend_div.setStyleSheet("background-color: #8E97AB; margin: 2px 4px 2px 4px;")
        self._legend_div.setVisible(False)
        col.addWidget(self._legend_div)

        self._legend = QLabel(self._legend_html())
        self._legend.setStyleSheet(
            "color: #7E879A; font-size: 9px; font-weight: 600; letter-spacing: 0.35px; padding: 0 4px 2px 4px;"
        )
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setWordWrap(True)
        self._legend.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._legend.setVisible(False)
        col.addWidget(self._legend)

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
        self._status.setText(self._status_html(text))
        if self.maximumHeight() > 0:
            self.content_resized.emit()

    def set_diagnostics(self, diagnostics: dict) -> None:
        detail = self._diagnostics_html(diagnostics or {})
        active = bool(diagnostics)
        self._status_div.setVisible(active)
        self._detail.setVisible(active)
        self._legend_div.setVisible(active)
        self._legend.setVisible(active)
        self._detail.setText(detail)
        if self.maximumHeight() > 0:
            self.content_resized.emit()

    def _diagnostics_html(self, diagnostics: dict) -> str:
        parts = []
        for key in ("ok", "wtm", "asos"):
            info = diagnostics.get(key)
            if not info:
                continue
            label = info.get("label", key.upper())
            valid = self._fmt_dt(info.get("valid_time"))
            attempted = self._fmt_dt(info.get("last_attempt"))
            state = "stale" if info.get("stale") else (info.get("note") or "live")
            parts.append(
                f"<b>{label}</b> valid {valid} · fetched {attempted} · {state}"
            )
        return "<br/>".join(parts)

    def _legend_html(self) -> str:
        green = '<span style="color:#39D98A;">●</span>'
        yellow = '<span style="color:#FFD166;">●</span>'
        red = '<span style="color:#E53935;">●</span>'
        return (
            f"Mesonet age: {green} ≤ 5 min  {yellow} ≤ 10 min  {red} > 10 min<br/>"
            f"ASOS age: {green} ≤ 70 min  {yellow} ≤ 90 min  {red} > 90 min"
        )

    @staticmethod
    def _status_html(text: str) -> str:
        if not text:
            return ""
        parts = [escape(part.strip()) for part in text.split("  |  ") if part.strip()]
        return " <span style=\"color:#8E97AB;\">|</span> ".join(parts)

    @staticmethod
    def _fmt_dt(value) -> str:
        if not isinstance(value, datetime):
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.strftime("%H:%M:%SZ")

    def plots_visible(self) -> bool:
        return self._chk_show_plots.isChecked()

    def asos_enabled(self) -> bool:
        return self._btn_asos.isChecked()

    def set_asos_enabled(self, enabled: bool) -> None:
        self._btn_asos.setChecked(enabled)
        self._asos_bbox_link.setVisible(enabled)

    def _on_asos_button_toggled(self, enabled: bool) -> None:
        self._asos_bbox_link.setVisible(enabled)
