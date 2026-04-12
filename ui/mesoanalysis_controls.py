# ui/mesoanalysis_controls.py
# Collapsible toolbar drawer for SPC mesoanalysis (SfcOA) overlay controls.
#
# Layout:
#   Row 1: [Sector ▼]  [Category ▼]  [Param ▼]   │  OPACITY ====
#   Row 2: [ -2h | -1h | NOW | +2h | +4h | +6h ]  (forecast positions grey
#          out when the selected parameter has no forecast product)

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPropertyAnimation, QEasingCurve, Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton,
    QVBoxLayout, QWidget,
)

from data.mesoanalysis_fetcher import (
    PARAMS, PARAM_CATEGORIES, SECTOR_LABELS, param_info,
)


HOUR_OFFSETS = [-2, -1, 0, 2, 4, 6]
HOUR_LABELS  = {-2: "-2h", -1: "-1h", 0: "NOW", 2: "+2h", 4: "+4h", 6: "+6h"}
AUTO_SECTOR_ID = -1  # sentinel for the "Auto (Vehicle)" entry


class MesoanalysisControls(QWidget):
    """
    Drawer widget for SfcOA overlay selection.

    Signals:
        sector_changed(int)         — sector id, or AUTO_SECTOR_ID for auto
        param_changed(str)          — param slug or "" when none selected
        hour_changed(int)           — hour offset (-2..+6)
        opacity_changed(float)      — 0..1
        sector_preview(int, bool)   — hover preview; (sector_id, on/off)
        selection_changed()         — any of sector/param/hour changed
    """

    sector_changed    = pyqtSignal(int)
    param_changed     = pyqtSignal(str)
    hour_changed      = pyqtSignal(int)
    opacity_changed   = pyqtSignal(float)
    sector_preview    = pyqtSignal(int, bool)
    selection_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        self._updating = False
        self._vehicle_mode = False
        self._last_auto_sector: int | None = None
        self._setup_ui()

    # ── Build ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("sfcoaDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        # ── Row 1: sector + category + param + opacity ────────────────────
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._sector_combo = QComboBox()
        self._sector_combo.setObjectName("sfcoaSectorCombo")
        self._sector_combo.setFixedHeight(24)
        self._sector_combo.setMinimumWidth(140)
        self._sector_combo.setToolTip("SPC sector (hover to preview on map)")
        self._populate_sector_combo()
        self._sector_combo.currentIndexChanged.connect(self._on_sector_index_changed)
        self._sector_combo.highlighted.connect(self._on_sector_highlighted)
        view = self._sector_combo.view()
        if view is not None:
            view.installEventFilter(self)
        r1.addWidget(self._sector_combo)

        self._category_combo = QComboBox()
        self._category_combo.setObjectName("sfcoaCategoryCombo")
        self._category_combo.setFixedHeight(24)
        self._category_combo.setMinimumWidth(100)
        self._category_combo.addItem("— none —", userData="")
        for cat in PARAM_CATEGORIES:
            self._category_combo.addItem(cat, userData=cat)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        r1.addWidget(self._category_combo)

        self._param_combo = QComboBox()
        self._param_combo.setObjectName("sfcoaParamCombo")
        self._param_combo.setFixedHeight(24)
        self._param_combo.setMinimumWidth(170)
        self._param_combo.setEnabled(False)
        self._param_combo.currentIndexChanged.connect(self._on_param_changed)
        r1.addWidget(self._param_combo)

        r1.addWidget(self._vdiv())

        lbl = QLabel("OPACITY")
        lbl.setStyleSheet("color: #B5BDCC; font-size: 10px; letter-spacing: 0.5px;")
        lbl.setFixedHeight(22)
        r1.addWidget(lbl)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(75)
        self._opacity_slider.setFixedHeight(22)
        self._opacity_slider.setFixedWidth(110)
        self._opacity_slider.setToolTip("SfcOA overlay opacity")
        self._opacity_slider.valueChanged.connect(
            lambda v: self.opacity_changed.emit(v / 100.0)
        )
        r1.addWidget(self._opacity_slider)
        r1.addStretch(1)
        col.addWidget(row1)

        # ── Row 2: hour selector ──────────────────────────────────────────
        row2 = QWidget()
        row2.setObjectName("sfcoaHourRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        lbl2 = QLabel("TIME")
        lbl2.setStyleSheet("color: #B5BDCC; font-size: 10px; letter-spacing: 0.5px;")
        lbl2.setFixedHeight(26)
        r2.addWidget(lbl2)

        self._hour_buttons: dict[int, QToolButton] = {}
        for off in HOUR_OFFSETS:
            b = QToolButton()
            b.setText(HOUR_LABELS[off])
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setMinimumWidth(42)
            b.setToolTip(self._hour_tooltip(off))
            b.clicked.connect(lambda _c, o=off: self._on_hour_clicked(o))
            r2.addWidget(b)
            self._hour_buttons[off] = b

        # Default: NOW selected
        self._hour_buttons[0].setChecked(True)
        self._selected_hour = 0

        r2.addStretch(1)
        col.addWidget(row2)

        outer.addWidget(self._drawer)

    def _populate_sector_combo(self):
        """Build the sector dropdown.  'Auto (Vehicle)' is inserted when
        vehicle mode is active."""
        self._sector_combo.blockSignals(True)
        self._sector_combo.clear()
        if self._vehicle_mode:
            self._sector_combo.addItem("Auto (Vehicle)", userData=AUTO_SECTOR_ID)
            self._sector_combo.insertSeparator(self._sector_combo.count())
        for sid, label in SECTOR_LABELS:
            self._sector_combo.addItem(label, userData=sid)
        self._sector_combo.setCurrentIndex(0)
        self._sector_combo.blockSignals(False)

    def _hour_tooltip(self, off: int) -> str:
        if off < 0:
            return f"Observed {abs(off)} hour ago"
        if off == 0:
            return "Current observed analysis"
        return f"Forecast +{off} h"

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("color: #2E2E4E; margin: 4px 2px;")
        return div

    # ── Public API ─────────────────────────────────────────────────────────

    def set_vehicle_mode(self, enabled: bool):
        """Show/hide the 'Auto (Vehicle)' sector option."""
        if enabled == self._vehicle_mode:
            return
        prev_sector = self.current_sector()
        self._vehicle_mode = enabled
        self._populate_sector_combo()
        if enabled:
            self._sector_combo.setCurrentIndex(0)  # Auto
        else:
            # Fall back to whatever the prior sector was, or S Plains.
            if prev_sector not in (None, AUTO_SECTOR_ID):
                self.set_current_sector(prev_sector)
            else:
                self.set_current_sector(14)
        self._emit_sector()

    def set_auto_sector(self, sector: int):
        """When in Auto mode, update which regional sector is being displayed
        without changing the dropdown label — used for tooltips / telemetry."""
        self._last_auto_sector = sector
        if self.current_sector() == AUTO_SECTOR_ID:
            idx = self._sector_combo.findData(AUTO_SECTOR_ID)
            if idx >= 0:
                self._sector_combo.setItemText(idx, f"Auto → {self._sector_label(sector)}")

    def _sector_label(self, sector: int) -> str:
        for sid, label in SECTOR_LABELS:
            if sid == sector:
                return label
        return f"s{sector}"

    def current_sector(self) -> int | None:
        data = self._sector_combo.currentData()
        if data is None:
            return None
        return int(data)

    def set_current_sector(self, sector: int):
        idx = self._sector_combo.findData(sector)
        if idx >= 0:
            self._sector_combo.blockSignals(True)
            self._sector_combo.setCurrentIndex(idx)
            self._sector_combo.blockSignals(False)

    def current_param(self) -> str:
        data = self._param_combo.currentData()
        return data or ""

    def current_hour(self) -> int:
        return self._selected_hour

    def resolved_sector(self) -> int | None:
        """Return the sector actually being used (resolves Auto → last auto)."""
        s = self.current_sector()
        if s == AUTO_SECTOR_ID:
            return self._last_auto_sector
        return s

    def clear_selection(self):
        """Reset to 'no param selected'."""
        self._updating = True
        try:
            self._category_combo.setCurrentIndex(0)
            self._param_combo.clear()
            self._param_combo.setEnabled(False)
        finally:
            self._updating = False
        self.param_changed.emit("")
        self.selection_changed.emit()

    # ── Drawer animation (copied from satellite_controls pattern) ──────────

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
            # Clear any lingering hover preview when the drawer closes
            self.sector_preview.emit(0, False)

        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(16777215))
        anim.start()
        self._animation = anim

    # ── Event filter: clear hover preview when popup closes ───────────────

    def eventFilter(self, obj, event):
        if obj is self._sector_combo.view():
            if event.type() == QEvent.Type.Hide:
                self.sector_preview.emit(0, False)
        return super().eventFilter(obj, event)

    # ── Slot handlers ──────────────────────────────────────────────────────

    def _on_sector_index_changed(self, _idx: int):
        if self._updating:
            return
        self._emit_sector()

    def _emit_sector(self):
        sid = self.current_sector()
        if sid is None:
            return
        self.sector_changed.emit(int(sid))
        self.selection_changed.emit()

    def _on_sector_highlighted(self, idx: int):
        data = self._sector_combo.itemData(idx)
        if data is None or int(data) == AUTO_SECTOR_ID:
            # Auto has no single bbox to preview — hide any box.
            self.sector_preview.emit(0, False)
            return
        self.sector_preview.emit(int(data), True)

    def _on_category_changed(self, _idx: int):
        if self._updating:
            return
        cat = self._category_combo.currentData() or ""
        self._updating = True
        try:
            self._param_combo.clear()
            if not cat:
                self._param_combo.setEnabled(False)
                self.param_changed.emit("")
                self.selection_changed.emit()
                return
            for slug, name, pcat, _fc in PARAMS:
                if pcat == cat:
                    self._param_combo.addItem(name, userData=slug)
            self._param_combo.setEnabled(self._param_combo.count() > 0)
            # Trigger param_changed for the first item.
            first = self._param_combo.currentData()
        finally:
            self._updating = False
        if first:
            self._apply_forecast_availability(first)
            self.param_changed.emit(first)
            self.selection_changed.emit()

    def _on_param_changed(self, _idx: int):
        if self._updating:
            return
        slug = self.current_param()
        if slug:
            self._apply_forecast_availability(slug)
        self.param_changed.emit(slug)
        self.selection_changed.emit()

    def _apply_forecast_availability(self, slug: str):
        info = param_info(slug)
        fc = bool(info and info[2])
        for off, btn in self._hour_buttons.items():
            if off > 0:
                btn.setEnabled(fc)
                if not fc and btn.isChecked():
                    btn.setChecked(False)
                    self._hour_buttons[0].setChecked(True)
                    self._selected_hour = 0
                    self.hour_changed.emit(0)

    def _on_hour_clicked(self, off: int):
        if self._updating:
            return
        # Radio-like behavior: exactly one hour button checked at a time.
        for o, b in self._hour_buttons.items():
            b.setChecked(o == off)
        self._selected_hour = off
        self.hour_changed.emit(off)
        self.selection_changed.emit()
