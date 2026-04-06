# ui/layer_order_pill.py
# Floating pill that shows the current MapLibre layer stack and lets the user
# reorder layers with ↑/↓ arrows.  Collapsed by default; expands upward.

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, QSettings, pyqtSignal

# ── Default layer stack (bottom → top) ───────────────────────────────────────

DEFAULT_LAYERS: list[str] = [
    "satellite",
    "radar",
    "spc_outlook",
    "spc_tor",
    "spc_wind",
    "spc_hail",
    "spc_watches",
    "spc_mds",
    "nws_warnings",
    "annotations",
    "storm_cones",
    "route",
    "surface_obs",
]

LAYER_LABELS: dict[str, str] = {
    "satellite":    "Satellite",
    "radar":        "Radar",
    "spc_outlook":  "SPC Outlook",
    "spc_tor":      "SPC Tornado",
    "spc_wind":     "SPC Wind",
    "spc_hail":     "SPC Hail",
    "spc_watches":  "SPC Watches",
    "spc_mds":      "SPC MDs",
    "nws_warnings": "NWS Warnings",
    "annotations":  "Annotations",
    "storm_cones":  "Storm Cones",
    "route":        "Route",
    "surface_obs":  "Surface Obs",
}

MAPLIBRE_LAYERS: dict[str, list[str]] = {
    "satellite":    ["sat-layer", "sat-wms-layer"],
    "radar":        ["radar-overlay"],
    "spc_outlook":  ["spc-cat-fill", "spc-cat-line"],
    "spc_tor":      ["spc-tor-fill", "spc-tor-line", "spc-tor-sig-base",
                     "spc-tor-sign", "spc-tor-cig1", "spc-tor-cig2",
                     "spc-tor-cig3", "spc-tor-sig-line"],
    "spc_wind":     ["spc-wind-fill", "spc-wind-line", "spc-wind-sig-base",
                     "spc-wind-sign", "spc-wind-cig1", "spc-wind-cig2",
                     "spc-wind-cig3", "spc-wind-sig-line"],
    "spc_hail":     ["spc-hail-fill", "spc-hail-line", "spc-hail-sig-base",
                     "spc-hail-sign", "spc-hail-cig1", "spc-hail-cig2",
                     "spc-hail-cig3", "spc-hail-sig-line"],
    "spc_watches":  ["spc-watches-fill", "spc-watches-line"],
    "spc_mds":      ["spc-mds-fill", "spc-mds-line"],
    "nws_warnings": ["nws-warnings-fill", "nws-warnings-line"],
    "annotations":  [],
    "storm_cones":  [],
    "route":        ["route-casing", "route-line"],
    "surface_obs":  [],
}

_SETTINGS_KEY = "layers/order"


def load_layer_order() -> list[str]:
    s = QSettings("NSSL", "STORM")
    saved = s.value(_SETTINGS_KEY, None)
    if isinstance(saved, list):
        merged = [k for k in saved if k in LAYER_LABELS]
        for k in DEFAULT_LAYERS:
            if k not in merged:
                merged.append(k)
        return merged
    return list(DEFAULT_LAYERS)


def save_layer_order(order: list[str]) -> None:
    QSettings("NSSL", "STORM").setValue(_SETTINGS_KEY, order)


class LayerOrderPill(QWidget):
    """Floating layer-order pill.  Expands upward from the toggle button.

    Signals:
        order_changed(list[str]) — emitted on confirm; payload is bottom→top.
    """

    order_changed = pyqtSignal(list)
    size_changed  = pyqtSignal()       # emitted whenever the pill resizes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("layerOrderPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._expanded = False
        self._order: list[str] = load_layer_order()
        self._saved_order: list[str] = list(self._order)
        self._active: set[str] = set()

        # ── Toggle button (always visible, anchored at bottom of pill) ────
        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setObjectName("layerOrderToggle")
        self._toggle_btn.setStyleSheet(
            "QToolButton { background: rgba(15,15,26,0.95); "
            "border: 1px solid rgba(74,83,108,0.55); border-radius: 8px; "
            "color: #C8D0DE; font-size: 10px; font-weight: 600; "
            "letter-spacing: 0.5px; padding: 5px 10px; }"
            "QToolButton:hover { border-color: #4A9EFF; color: #4A9EFF; }"
        )
        self._toggle_btn.clicked.connect(self._toggle_expanded)

        # ── Expanded panel (child of pill, shown above toggle button) ─────
        self._panel = QWidget(self)
        self._panel.setObjectName("layerOrderPanel")
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        title = QLabel("LAYER ORDER")
        title.setStyleSheet(
            "font-size: 10px; font-weight: 700; letter-spacing: 1.3px; color: #4A9EFF;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        self._btn_reset = QPushButton("RESET")
        self._btn_reset.setFixedHeight(20)
        self._btn_reset.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #2E2E4E; "
            "border-radius: 4px; color: #6E7A8F; font-size: 9px; "
            "font-weight: 600; letter-spacing: 0.8px; padding: 0 6px; }"
            "QPushButton:hover { border-color: #4A9EFF; color: #4A9EFF; }"
        )
        self._btn_reset.clicked.connect(self._on_reset)
        hdr.addWidget(self._btn_reset)
        panel_layout.addLayout(hdr)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #1E1E2E; margin: 2px 0;")
        panel_layout.addWidget(div)

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        panel_layout.addWidget(self._rows_widget)

        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet("color: #1E1E2E; margin: 2px 0;")
        panel_layout.addWidget(div2)

        self._btn_confirm = QPushButton("APPLY ORDER")
        self._btn_confirm.setStyleSheet(
            "QPushButton { background: rgba(0,207,255,0.12); border: 1px solid #00CFFF; "
            "border-radius: 5px; color: #00CFFF; font-size: 10px; "
            "font-weight: 700; letter-spacing: 0.8px; padding: 4px 0; }"
            "QPushButton:hover { background: rgba(0,207,255,0.22); }"
            "QPushButton:pressed { background: rgba(0,207,255,0.35); }"
        )
        self._btn_confirm.clicked.connect(self._on_confirm)
        panel_layout.addWidget(self._btn_confirm)

        self._panel.hide()

        self._refresh_label()
        self._relayout()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _relayout(self):
        """Size and position children; resize the pill itself.

        The toggle button is always at the BOTTOM of the pill (y = pill_h - btn_h).
        The panel, when visible, sits above it.  _layout_overlays in MainWindow
        positions the pill by its bottom-left corner, so growing upward is free.
        """
        self._toggle_btn.adjustSize()
        btn_w = self._toggle_btn.sizeHint().width()
        btn_h = self._toggle_btn.sizeHint().height()

        if self._expanded and not self._panel.isHidden():
            self._rows_widget.layout().activate()
            self._panel.layout().activate()
            self._panel.setMinimumWidth(220)
            self._panel.adjustSize()
            panel_w = max(self._panel.sizeHint().width(), btn_w, 220)
            panel_h = self._panel.sizeHint().height()
            GAP = 4
            total_w = panel_w
            total_h = panel_h + GAP + btn_h
            self._panel.setGeometry(0, 0, panel_w, panel_h)
            self._toggle_btn.setGeometry(0, panel_h + GAP, total_w, btn_h)
        else:
            total_w = btn_w
            total_h = btn_h
            self._toggle_btn.setGeometry(0, 0, btn_w, btn_h)

        prev = self.size()
        self.setFixedSize(total_w, total_h)
        if self.width() != prev.width() or self.height() != prev.height():
            self.size_changed.emit()

    # ── Row building ──────────────────────────────────────────────────────────

    def _build_rows(self):
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        display_order = list(reversed(self._order))  # top-of-stack first
        for i, key in enumerate(display_order):
            self._rows_layout.addWidget(self._make_row(key, i, len(display_order)))

    def _make_row(self, key: str, display_idx: int, total: int) -> QWidget:
        active = key in self._active
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        lbl = QLabel(LAYER_LABELS.get(key, key))
        lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 500; "
            f"color: {'#C8D0DE' if active else '#3A3B5A'};"
        )
        rl.addWidget(lbl, 1)

        dot = QLabel("●")
        dot.setStyleSheet(f"font-size: 8px; color: {'#39D98A' if active else '#1E2434'};")
        rl.addWidget(dot)

        btn_up = QToolButton()
        btn_up.setText("↑")
        btn_up.setFixedSize(20, 20)
        btn_up.setEnabled(display_idx > 0)
        btn_up.setStyleSheet(self._arrow_style(display_idx > 0))
        btn_up.clicked.connect(lambda _, k=key: self._move_layer(k, -1))
        rl.addWidget(btn_up)

        btn_dn = QToolButton()
        btn_dn.setText("↓")
        btn_dn.setFixedSize(20, 20)
        btn_dn.setEnabled(display_idx < total - 1)
        btn_dn.setStyleSheet(self._arrow_style(display_idx < total - 1))
        btn_dn.clicked.connect(lambda _, k=key: self._move_layer(k, +1))
        rl.addWidget(btn_dn)

        return row

    @staticmethod
    def _arrow_style(enabled: bool) -> str:
        color = "#C8D0DE" if enabled else "#2A2B3A"
        return (
            f"QToolButton {{ background: transparent; border: 1px solid #1E1E2E; "
            f"border-radius: 3px; color: {color}; font-size: 11px; }}"
            f"QToolButton:hover {{ border-color: #4A9EFF; color: #4A9EFF; }}"
        )

    # ── Interaction ───────────────────────────────────────────────────────────

    def _toggle_expanded(self):
        self._expanded = not self._expanded
        self._refresh_label()
        if self._expanded:
            self._build_rows()
            self._panel.show()
        else:
            self._panel.hide()
        self._relayout()

    def _move_layer(self, key: str, direction: int):
        # _order is bottom→top; display is reversed.
        # direction -1 = up in display = toward end of _order list.
        stack_idx = self._order.index(key)
        new_idx = max(0, min(len(self._order) - 1, stack_idx + (-direction)))
        if new_idx == stack_idx:
            return
        self._order.insert(new_idx, self._order.pop(stack_idx))
        self._build_rows()
        self._relayout()

    def _on_confirm(self):
        self._saved_order = list(self._order)
        save_layer_order(self._saved_order)
        self._refresh_label()
        self.order_changed.emit(list(self._order))

    def _on_reset(self):
        self._order = list(self._saved_order)
        self._build_rows()
        self._relayout()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_layer_active(self, key: str, active: bool):
        if active:
            self._active.add(key)
        else:
            self._active.discard(key)
        self._refresh_label()
        if self._expanded:
            self._build_rows()
            self._relayout()

    def current_order(self) -> list[str]:
        return list(self._saved_order)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh_label(self):
        arrow = "▲" if self._expanded else "▼"
        self._toggle_btn.setText(
            f"LAYERS  {len(self._active)}/{len(self._order)}  {arrow}"
        )
        self._toggle_btn.adjustSize()
