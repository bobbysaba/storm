from datetime import datetime, timezone
from html import escape

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QLabel, QCheckBox
from PyQt6.QtCore import pyqtSignal, Qt

from config import ACCENT_COLOR
from ui.layers.surface_plot_layer import ASOS_FRESH_MIN
from ui import palette as P
from ui.widgets.drawer import Drawer


class SurfaceControls(Drawer):
    """Collapsible drawer for surface observation overlays: a button for each
    station_obs source in storm.toml that sits in the drawer, then ASOS."""

    source_toggled = pyqtSignal(str, bool)   # source id, on
    asos_toggled = pyqtSignal(bool)
    asos_bbox_requested = pyqtSignal()
    plots_toggled = pyqtSignal(bool)

    def __init__(self, parent=None, sources=()):
        super().__init__(parent)
        # station_obs sources (data/sources.py) shown in the drawer
        self._sources = [s for s in sources if s.where == "drawer"]
        # every station_obs source, for the legend (toolbar ones too)
        self._all_sources = list(sources)
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

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

        self._source_btns: dict[str, QToolButton] = {}
        for source in self._sources:
            btn = self._btn(source.short)
            missing = source.missing_key()
            if missing:
                btn.setEnabled(False)
                btn.setToolTip(f"{source.name}: needs {missing} in .env")
            else:
                btn.setToolTip(source.name)
            btn.toggled.connect(lambda on, key=source.id: self.source_toggled.emit(key, on))
            r1.addWidget(btn)
            self._source_btns[source.id] = btn
        self._btn_asos = self._btn("ASOS")
        self._btn_asos.setToolTip("ASOS stations inside a box you draw on the map")
        self._btn_asos.toggled.connect(self.asos_toggled.emit)
        self._btn_asos.toggled.connect(self._on_asos_button_toggled)
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
            "font-size: 11px; font-weight: 500; letter-spacing: 0.2px; padding: 0;"
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
            f"color: {P.TEXT_SECONDARY}; font-size: 11px; letter-spacing: 0.4px; padding: 1px 4px 2px 4px;"
        )
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self._status)

        self._status_div = QFrame()
        self._status_div.setFrameShape(QFrame.Shape.NoFrame)
        self._status_div.setFixedHeight(1)
        self._status_div.setStyleSheet(f"background-color: {P.TEXT_LABEL}; margin: 2px 4px 2px 4px;")
        self._status_div.setVisible(False)
        col.addWidget(self._status_div)

        self._detail = QLabel("")
        self._detail.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 11px; font-weight: 600; letter-spacing: 0.35px; padding: 0 4px 1px 4px;"
        )
        self._detail.setTextFormat(Qt.TextFormat.RichText)
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._detail.setVisible(False)
        col.addWidget(self._detail)

        self._legend_div = QFrame()
        self._legend_div.setFrameShape(QFrame.Shape.NoFrame)
        self._legend_div.setFixedHeight(1)
        self._legend_div.setStyleSheet(f"background-color: {P.TEXT_LABEL}; margin: 2px 4px 2px 4px;")
        self._legend_div.setVisible(False)
        col.addWidget(self._legend_div)

        self._legend = QLabel(self._legend_html())
        self._legend.setStyleSheet(
            f"color: {P.TEXT_LABEL}; font-size: 11px; font-weight: 600; letter-spacing: 0.35px; padding: 0 4px 2px 4px;"
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
        div.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        return div

    def set_status(self, text: str):
        self._status.setText(self._status_html(text))
        self.content_changed()

    def set_diagnostics(self, diagnostics: dict) -> None:
        detail = self._diagnostics_html(diagnostics or {})
        active = bool(diagnostics)
        self._status_div.setVisible(active)
        self._detail.setVisible(active)
        self._legend_div.setVisible(active)
        self._legend.setVisible(active)
        self._detail.setText(detail)
        self.content_changed()

    def _diagnostics_html(self, diagnostics: dict) -> str:
        parts = []
        for key, info in diagnostics.items():
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
        """One age line per set of thresholds: the sources that share it, then
        ASOS."""
        green = f'<span style="color:{P.OK};">●</span>'
        yellow = f'<span style="color:{P.WARN};">●</span>'
        red = f'<span style="color:{P.ERROR};">●</span>'
        groups: dict[tuple[float, float], list[str]] = {}
        for source in self._all_sources:
            groups.setdefault(source.fresh_min, []).append(escape(source.short))
        groups.setdefault(ASOS_FRESH_MIN, []).append("ASOS")
        lines = []
        for (g, a), names in groups.items():
            lines.append(f"{' '.join(names)} age: {green} ≤ {g:g} min  {yellow} ≤ {a:g} min  "
                         f"{red} > {a:g} min")
        return "<br/>".join(lines)

    @staticmethod
    def _status_html(text: str) -> str:
        if not text:
            return ""
        parts = [escape(part.strip()) for part in text.split("  |  ") if part.strip()]
        return f" <span style=\"color:{P.TEXT_LABEL};\">|</span> ".join(parts)

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

    def _source_buttons(self) -> dict:
        # ASOS is left out: it needs a box drawn on the map each session
        return dict(self._source_btns)

    def source_button(self, key: str) -> QToolButton | None:
        return self._source_btns.get(key)

    def set_source_checked(self, key: str, on: bool = True) -> None:
        """Turn a source on (the launch window's auto-start), if it can be."""
        btn = self._source_btns.get(key)
        if btn is not None and btn.isEnabled() and not btn.isHidden():
            btn.setChecked(on)

    def save_state(self) -> dict:
        return {
            "sources": [k for k, b in self._source_buttons().items() if b.isChecked()],
            "plots": self.plots_visible(),
        }

    def restore_state(self, state: dict) -> None:
        for key in state.get("sources") or ():
            self.set_source_checked(key)
        if "plots" in state:
            self._chk_show_plots.setChecked(bool(state["plots"]))

    def configure_for_archive(self) -> None:
        """ARCHIVE: ASOS only, replayed on the archive clock from a box drawn
        on the map (the station_obs sources are live only)."""
        for btn in self._source_buttons().values():
            btn.hide()
        self._btn_asos.setToolTip("Replay the ASOS stations inside a box you draw on the map")
        self._asos_bbox_link.setToolTip("Draw another box (replaces these stations)")
        green = f'<span style="color:{P.OK};">●</span>'
        yellow = f'<span style="color:{P.WARN};">●</span>'
        red = f'<span style="color:{P.ERROR};">●</span>'
        self._legend.setText(
            f"Report age at the clock: {green} ≤ 70 min  {yellow} ≤ 90 min  {red} > 90 min")
        self._legend_div.setVisible(True)
        self._legend.setVisible(True)
        self.set_status("ASOS: turn on, then drag a box on the map")

    def asos_enabled(self) -> bool:
        return self._btn_asos.isChecked()

    def set_asos_enabled(self, enabled: bool) -> None:
        self._btn_asos.setChecked(enabled)
        self._asos_bbox_link.setVisible(enabled)

    def _on_asos_button_toggled(self, enabled: bool) -> None:
        self._asos_bbox_link.setVisible(enabled)
