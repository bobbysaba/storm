from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QToolButton,
    QPushButton, QSizePolicy, QFrame,
)
from ui import palette as P
from ui.widgets.drawer import Drawer

_PRODUCT_BUTTON_MIN_WIDTH = 64
_PRODUCT_BUTTON_MIN_HEIGHT = 22
_PLAYBACK_BUTTON_SIZE = 32
_REFRESH_BUTTON_WIDTH = 72
_STATUS_LABEL_WIDTH = 170

_GROUP_ORDER = ("thermo", "parcel", "lapse", "shear", "helicity", "composite", "upper", "kinematic")
_GROUP_LABELS = {
    "thermo": "SURFACE",
    "parcel": "PARCEL",
    "lapse": "LAPSE RATES",
    "shear": "SHEAR",
    "helicity": "SRH",
    "composite": "COMPOSITE",
    "upper": "UPPER AIR",
    "kinematic": "WINDS",
}

_COLUMN_GROUPS = (
    ("thermo",),
    ("parcel",),
    ("lapse", "composite"),
    ("helicity", "shear"),
    ("upper", "kinematic"),
)


class ContourControls(Drawer):
    """The contours source's drawer (NSSL: SFCOA): runs, and its fields
    in columns by group, in the order metadata.json lists them. Groups
    beyond the known ones share a last column."""

    product_toggled = pyqtSignal(str, bool)
    frame_requested = pyqtSignal(int)
    refresh_requested = pyqtSignal()
    visible_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._pending_products: set[str] = set()   # restored, waiting for set_products
        self._times: list[str] = []
        self._time_labels: list[str] = []
        self._current_frame = 0
        self._active_products: set[str] = set()
        self._product_buttons: dict[str, QToolButton] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drawer = QWidget()
        self._drawer.setObjectName("contourDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row = QWidget()
        row.setObjectName("contourControlRow")
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(4)

        self._refresh_btn = QPushButton("REFRESH")
        self._refresh_btn.setObjectName("contourRefreshButton")
        self._refresh_btn.setMinimumSize(_REFRESH_BUTTON_WIDTH, 22)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        r.addWidget(self._refresh_btn)
        r.addWidget(self._vdiv())

        self._btn_jump_start = self._pbtn("⏮", "First valid time")
        self._btn_back = self._pbtn("⏪", "Previous valid time")
        self._btn_fwd = self._pbtn("⏩", "Next valid time")
        self._btn_jump_end = self._pbtn("⏭", "Latest valid time")
        self._btn_jump_start.clicked.connect(self._on_jump_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_jump_end.clicked.connect(self._on_jump_end)
        for btn in (self._btn_jump_start, self._btn_back, self._btn_fwd, self._btn_jump_end):
            btn.setEnabled(False)
            r.addWidget(btn)

        self._status_label = QLabel("")
        self._status_label.setObjectName("contourTimeLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._status_label.setFixedSize(_STATUS_LABEL_WIDTH, 22)
        r.addWidget(self._status_label)
        col.addWidget(row)

        self._product_grid_widget = QWidget()
        self._product_grid_widget.setObjectName("contourProductGrid")
        self._product_grid = QGridLayout(self._product_grid_widget)
        self._product_grid.setContentsMargins(0, 0, 0, 0)
        self._product_grid.setHorizontalSpacing(0)
        self._product_grid.setVerticalSpacing(3)
        col.addWidget(self._product_grid_widget)
        outer.addWidget(self._drawer)

    def set_times(self, times) -> None:
        current = self.current_time()
        self._times = [item.time_id for item in times]
        self._time_labels = [item.label for item in times]
        idx = self._times.index(current) if current in self._times else max(0, len(self._times) - 1)
        self.set_cache_size(len(self._times))
        self.set_frame(idx)
        self.content_changed()

    def set_products(self, products) -> None:
        previous_active = set(self._active_products)
        available = {product.variable_id for product in products}
        self._active_products &= available

        while self._product_grid.count():
            item = self._product_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._product_buttons.clear()

        grouped: dict[str, list] = {group: [] for group in _GROUP_ORDER}
        for product in products:
            grouped.setdefault(product.group, []).append(product)

        known = {group for column in _COLUMN_GROUPS for group in column}
        extra = tuple(dict.fromkeys(p.group for p in products if p.group not in known))
        col_idx = 0
        for column_groups in _COLUMN_GROUPS + ((extra,) if extra else ()):
            row_idx = 0
            rendered_any = False
            for group in column_groups:
                group_products = grouped.get(group) or []
                if not group_products:
                    continue
                rendered_any = True
                label = QLabel(_GROUP_LABELS.get(group, group.upper()))
                label.setObjectName("contourGroupLabel")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._product_grid.addWidget(label, row_idx, col_idx)
                row_idx += 1
                for product in group_products:   # metadata.json's order
                    btn = QToolButton()
                    btn.setText(product.label)
                    btn.setCheckable(True)
                    btn.setChecked(product.variable_id in self._active_products)
                    tip = product.long_name or product.label
                    if product.units:
                        tip = f"{tip} ({product.units})"
                    btn.setToolTip(tip)
                    btn.setMinimumSize(_PRODUCT_BUTTON_MIN_WIDTH, _PRODUCT_BUTTON_MIN_HEIGHT)
                    btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                    btn.setProperty("paletteButton", True)
                    btn.clicked.connect(
                        lambda checked=False, pid=product.variable_id: self._toggle_product(pid, checked)
                    )
                    self._product_buttons[product.variable_id] = btn
                    self._product_grid.addWidget(btn, row_idx, col_idx, Qt.AlignmentFlag.AlignHCenter)
                    row_idx += 1
            if rendered_any:
                col_idx += 1

        for product_id in previous_active - self._active_products:
            self.product_toggled.emit(product_id, False)
        self.visible_toggled.emit(bool(self._active_products))
        self._apply_pending_products()
        self.content_changed()

    def set_status(self, text: str) -> None:
        text = str(text or "")
        self._status_label.setText(text)
        self._status_label.setToolTip(text)

    def current_time(self) -> str:
        if 0 <= self._current_frame < len(self._times):
            return self._times[self._current_frame]
        return ""

    def current_frame(self) -> int:
        return self._current_frame

    def active_products(self) -> set[str]:
        return set(self._active_products)

    def save_state(self) -> dict:
        return {"products": sorted(self._active_products | self._pending_products)}

    def restore_state(self, state: dict) -> None:
        """Products turn on when the next ``set_products`` lists them."""
        self._pending_products = set(state.get("products") or ())

    def _apply_pending_products(self) -> None:
        # products this listing doesn't offer stay pending for the next one
        for product_id in sorted(self._pending_products):
            btn = self._product_buttons.get(product_id)
            if btn is None or not btn.isEnabled():
                continue
            self._pending_products.discard(product_id)
            if product_id not in self._active_products:
                btn.setChecked(True)
                self._toggle_product(product_id, True)

    def set_cache_size(self, n: int) -> None:
        n = max(0, int(n))
        if n == 0:
            self._current_frame = 0
        else:
            self._current_frame = min(self._current_frame, n - 1)
        has_history = n > 1
        for w in (self._btn_jump_start, self._btn_back, self._btn_fwd, self._btn_jump_end):
            w.setEnabled(has_history)

    def set_frame(self, idx: int) -> None:
        if not self._times:
            self._current_frame = 0
            self.set_status("No times")
            return
        self._current_frame = max(0, min(len(self._times) - 1, int(idx)))
        label = self._time_labels[self._current_frame] if self._current_frame < len(self._time_labels) else self.current_time()
        self.set_status(label)

    def stop_loop(self) -> None:
        return

    def _toggle_product(self, product_id: str, checked: bool) -> None:
        if self._updating:
            return
        self._pending_products.discard(product_id)
        if checked:
            self._active_products.add(product_id)
            self.visible_toggled.emit(True)
        else:
            self._active_products.discard(product_id)
            if not self._active_products:
                self.visible_toggled.emit(False)
        self.product_toggled.emit(product_id, checked)

    def _on_jump_start(self) -> None:
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self) -> None:
        idx = max(0, len(self._times) - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_back(self) -> None:
        idx = max(0, self._current_frame - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_forward(self) -> None:
        idx = min(max(0, len(self._times) - 1), self._current_frame + 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _pbtn(self, text: str, tip: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(_PLAYBACK_BUTTON_SIZE, 22)
        btn.setToolTip(tip)
        return btn

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        return div
