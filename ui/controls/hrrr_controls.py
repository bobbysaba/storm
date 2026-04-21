from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox,
    QLabel, QListView, QToolButton, QPushButton, QSizePolicy, QFrame,
)

_FIELD_BUTTON_MIN_WIDTH = 62
_FIELD_BUTTON_MIN_HEIGHT = 22
_PLAYBACK_BUTTON_SIZE = 32
_REFRESH_BUTTON_WIDTH = 72
_RUN_COMBO_WIDTH = 96
_RUN_POPUP_WIDTH = 132

_GROUP_ORDER = ("thermo", "sfc", "parcel", "uh", "srh", "other")
_GROUP_LABELS = {
    "thermo": "SURFACE",
    "sfc": "SFC PARCEL",
    "parcel": "ML PARCEL",
    "uh": "UH",
    "srh": "SRH",
    "other": "OTHER",
}


class HrrrControls(QWidget):
    """Compact drawer for server-rendered HRRR overlay products."""

    run_changed    = pyqtSignal(str)
    field_changed  = pyqtSignal(str)
    frame_requested = pyqtSignal(int)
    loop_toggled   = pyqtSignal(bool)
    refresh_requested = pyqtSignal()
    content_resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating = False
        self._hours: list[int] = []
        self._current_frame = 0
        self._selected_field: str = ""
        self._field_buttons: dict[str, QToolButton] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drawer = QWidget()
        self._drawer.setObjectName("hrrrDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        # ── row 1: run combo + refresh + playback ────────────────────────
        row1 = QWidget()
        row1.setObjectName("hrrrControlRow")
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._run_combo = QComboBox()
        self._run_combo.setFixedHeight(22)
        self._run_combo.setFixedWidth(_RUN_COMBO_WIDTH)
        self._run_combo.setMaxVisibleItems(6)
        self._run_combo.setView(QListView())
        self._run_combo.view().setMinimumWidth(_RUN_POPUP_WIDTH)
        self._run_combo.setObjectName("hrrrRunCombo")
        self._run_combo.setToolTip("HRRR model run")
        self._run_combo.currentIndexChanged.connect(self._on_run_changed)
        r1.addWidget(self._run_combo)

        self._refresh_btn = QPushButton("REFRESH")
        self._refresh_btn.setObjectName("hrrrRefreshButton")
        self._refresh_btn.setFixedSize(_REFRESH_BUTTON_WIDTH, 22)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        r1.addWidget(self._refresh_btn)

        r1.addWidget(self._vdiv())

        self._btn_jump_start = self._pbtn("⏮", "First forecast hour")
        self._btn_back       = self._pbtn("⏪", "Step back one forecast hour")
        self._btn_play       = self._pbtn("▶", "Play / Pause loop", checkable=True)
        self._btn_fwd        = self._pbtn("⏩", "Step forward one forecast hour")
        self._btn_jump_end   = self._pbtn("⏭", "Last forecast hour")

        self._btn_jump_start.clicked.connect(self._on_jump_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_play.toggled.connect(self._on_play_toggled)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_jump_end.clicked.connect(self._on_jump_end)

        for btn in (
            self._btn_jump_start,
            self._btn_back,
            self._btn_play,
            self._btn_fwd,
            self._btn_jump_end,
        ):
            btn.setEnabled(False)
            r1.addWidget(btn)

        col.addWidget(row1)

        # ── row 2: dynamic product button grid ───────────────────────────
        self._field_grid_widget = QWidget()
        self._field_grid_widget.setObjectName("hrrrFieldGrid")
        self._field_grid = QGridLayout(self._field_grid_widget)
        self._field_grid.setContentsMargins(0, 0, 0, 0)
        self._field_grid.setHorizontalSpacing(8)
        self._field_grid.setVerticalSpacing(3)
        col.addWidget(self._field_grid_widget)

        outer.addWidget(self._drawer)

    # ── drawer animation ──────────────────────────────────────────────────

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

    # ── public setters ────────────────────────────────────────────────────

    def set_fields(self, fields) -> None:
        previous = self._selected_field

        while self._field_grid.count():
            item = self._field_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._field_buttons.clear()

        grouped: dict[str, list] = {group: [] for group in _GROUP_ORDER}
        for field in fields:
            grouped[_field_group(field)].append(field)

        col_idx = 0
        for group in _GROUP_ORDER:
            group_fields = grouped[group]
            if not group_fields:
                continue

            label = QLabel(_GROUP_LABELS[group])
            label.setObjectName("hrrrFieldGroupLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._field_grid.addWidget(label, 0, col_idx)

            for row_idx, field in enumerate(_sort_group_fields(group, group_fields), start=1):
                btn = QToolButton()
                btn.setText(field.label)
                btn.setCheckable(True)
                btn.setToolTip(field.label)
                btn.setMinimumSize(_FIELD_BUTTON_MIN_WIDTH, _FIELD_BUTTON_MIN_HEIGHT)
                btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                btn.setProperty("paletteButton", True)
                btn.clicked.connect(
                    lambda _checked=False, fid=field.field_id: self._on_field_btn_clicked(fid)
                )
                self._field_buttons[field.field_id] = btn
                self._field_grid.addWidget(btn, row_idx, col_idx, Qt.AlignmentFlag.AlignHCenter)
            col_idx += 1

        if previous and previous in self._field_buttons:
            self._field_buttons[previous].blockSignals(True)
            self._field_buttons[previous].setChecked(True)
            self._field_buttons[previous].blockSignals(False)
        elif self._field_buttons:
            first_id = next(iter(self._field_buttons))
            self._field_buttons[first_id].blockSignals(True)
            self._field_buttons[first_id].setChecked(True)
            self._field_buttons[first_id].blockSignals(False)
            self._selected_field = first_id

        new = self._selected_field
        if new and new != previous:
            self.field_changed.emit(new)
        self._notify_content_resized()

    def set_runs(self, runs) -> None:
        previous = self.current_run()
        self._updating = True
        try:
            self._run_combo.clear()
            for run in runs:
                self._run_combo.addItem(run.label, run.run_id)
            idx = self._run_combo.findData(previous)
            if idx >= 0:
                self._run_combo.setCurrentIndex(idx)
            self._resize_run_popup()
        finally:
            self._updating = False
        new = self.current_run()
        if new and new != previous:
            self.run_changed.emit(new)
        self._notify_content_resized()

    def _resize_run_popup(self) -> None:
        view = self._run_combo.view()
        count = self._run_combo.count()
        visible_rows = min(max(count, 1), 6)
        row_height = max(view.sizeHintForRow(0), self._run_combo.fontMetrics().height() + 8, 22)
        view.setMinimumWidth(_RUN_POPUP_WIDTH)
        view.setMinimumHeight((row_height * visible_rows) + 8)
        view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if count > visible_rows
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def set_hours(self, hours: list[int]) -> None:
        current = self.current_hour()
        self._hours = [int(h) for h in hours]
        idx = self._hours.index(current) if current in self._hours else 0
        self.set_cache_size(len(self._hours))
        self.set_frame(idx)
        self._notify_content_resized()

    def set_status(self, text: str) -> None:  # noqa: ARG002
        pass

    # ── public accessors ──────────────────────────────────────────────────

    def current_field(self) -> str:
        return self._selected_field

    def current_run(self) -> str:
        data = self._run_combo.currentData()
        return str(data or "")

    def current_hour(self) -> int:
        if 0 <= self._current_frame < len(self._hours):
            return int(self._hours[self._current_frame])
        return 0

    def current_frame(self) -> int:
        return self._current_frame

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def set_cache_size(self, n: int) -> None:
        n = max(0, int(n))
        if n == 0:
            self._current_frame = 0
        else:
            self._current_frame = min(self._current_frame, n - 1)
        has_history = n > 1
        for w in (
            self._btn_jump_start,
            self._btn_back,
            self._btn_play,
            self._btn_fwd,
            self._btn_jump_end,
        ):
            w.setEnabled(has_history)

    def set_frame(self, idx: int) -> None:
        if not self._hours:
            self._current_frame = 0
            return
        self._current_frame = max(0, min(len(self._hours) - 1, int(idx)))

    def stop_loop(self) -> None:
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)

    # ── internal slots ────────────────────────────────────────────────────

    def _on_field_btn_clicked(self, field_id: str) -> None:
        if self._updating:
            return
        for fid, btn in self._field_buttons.items():
            checked = fid == field_id
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.blockSignals(False)
        self._selected_field = field_id
        self.stop_loop()
        self.field_changed.emit(field_id)

    def _on_run_changed(self, index: int = -1) -> None:
        run = self._run_combo.itemData(index) if index >= 0 else self.current_run()
        if run and not self._updating:
            self.run_changed.emit(str(run))

    def _on_play_toggled(self, checked: bool) -> None:
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_jump_start(self) -> None:
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self) -> None:
        idx = max(0, len(self._hours) - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_back(self) -> None:
        idx = max(0, self._current_frame - 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _on_step_forward(self) -> None:
        idx = min(max(0, len(self._hours) - 1), self._current_frame + 1)
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def _pbtn(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setFixedSize(_PLAYBACK_BUTTON_SIZE, 22)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        return btn

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div

    def _notify_content_resized(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self.updateGeometry()
        self.content_resized.emit()


def _field_group(field) -> str:
    field_id = str(getattr(field, "field_id", "") or "").lower()
    label = str(getattr(field, "label", "") or "").lower()
    text = f"{field_id} {label}"

    if "srh" in text or "helicity" in text and "storm" in text:
        return "srh"
    if "uh" in text or "updraft helicity" in text:
        return "uh"
    if (
        field_id.startswith(("sfc", "sb"))
        or "surface parcel" in text
        or "surface-based" in text
    ):
        return "sfc"
    if (
        field_id.startswith(("ml", "mu"))
        or "mixed layer" in text
        or "most unstable" in text
        or text.startswith(("ml ", "mu "))
        or " ml " in text
        or " mu " in text
    ):
        return "parcel"
    if (
        "tmp2m" in field_id
        or "2m" in text
        or "2 m" in text
        or "dew" in text
        or "dwpt" in text
    ):
        return "thermo"
    return "other"


def _sort_group_fields(group: str, fields: list) -> list:
    return sorted(fields, key=lambda field: (_field_sort_key(group, field), str(field.label)))


def _field_sort_key(group: str, field) -> int:
    field_id = str(getattr(field, "field_id", "") or "").lower()
    label = str(getattr(field, "label", "") or "").lower()
    text = f"{field_id} {label}"
    ordered_tokens = {
        "thermo": ("tmp", "temp", "dew", "dwpt"),
        "sfc": ("cape", "cinh", "cin", "lcl", "lfc"),
        "parcel": ("mlcape", "mlcinh", "mlcin", "mllcl", "mllfc",
                   "mucape", "mucinh", "mucin", "mulcl", "mulfc"),
        "uh": ("02", "0-2", "03", "0-3"),
        "srh": ("01", "0-1", "03", "0-3"),
    }.get(group, ())
    for idx, token in enumerate(ordered_tokens):
        if token in text:
            return idx
    return len(ordered_tokens)
