"""LIDAR drawer (archive): one row, like the radar's. A lidar is picked the
way a radar station is, on the map: while the drawer is open every place a
lidar ran PPI scans from this session is marked (a CLAMPS trailer's site,
or each stop of the lidar truck; core/lidar_scans.locations). The row shows
the picked lidar, the field, whether its scans are drawn, a minimum
intensity, and WINDS for the lidars' wind profiles.

Two kinds of instrument, named plainly:
  * the lidar truck (DLTRUCK1): moves; placed from its GPS, its heading
    recorded in its files or estimated from its compass (a ⚠ says so);
  * the CLAMPS1 / CLAMPS2 trailers: each parked at a site its files record.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

from ui import palette as P
from ui.widgets.drawer import Drawer

INSTRUMENT_NAMES = {
    "DLTRUCK1": "Lidar truck",
    "CLAMPS1": "CLAMPS1 trailer",
    "CLAMPS2": "CLAMPS2 trailer",
}


def instrument_name(instrument: str) -> str:
    return INSTRUMENT_NAMES.get(instrument, instrument)


class RawLidarControls(Drawer):
    field_selected = pyqtSignal(str)
    data_toggled = pyqtSignal(bool)           # draw the picked lidar's scans
    location_requested = pyqtSignal()         # the lidar button: center the map on it
    min_intensity_changed = pyqtSignal(object)   # float, or None for every cell
    winds_requested = pyqtSignal()

    # intensity (the files' SNR + 1): noise sits near 1.000 ± 0.005, returns
    # above ~1.01. Slider steps of 0.001; 0 is off.
    INTENSITY_STEP = 0.001
    INTENSITY_MAX_STEPS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = "Looking for lidar data…"
        self._chosen = False
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("rawLidarDrawer")
        row = QHBoxLayout(self._drawer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._location_button = QToolButton()
        self._location_button.setMinimumWidth(150)
        self._location_button.setText(self._status)
        self._location_button.clicked.connect(self._on_location_clicked)
        row.addWidget(self._location_button)

        self._heading_warning = QLabel("⚠")
        self._heading_warning.hide()
        row.addWidget(self._heading_warning)

        self._field_combo = QComboBox()
        self._field_combo.setMinimumWidth(130)
        self._field_combo.setToolTip("The field drawn on the map, as the lidar recorded it")
        self._field_combo.setEnabled(False)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        row.addWidget(self._field_combo)

        self._chk_data = QCheckBox("show on map")
        self._chk_data.setObjectName("vehiclePillToggle")
        self._chk_data.setChecked(True)
        self._chk_data.setToolTip("Draw the picked lidar's scans as the clock reaches them")
        self._chk_data.toggled.connect(self.data_toggled.emit)
        row.addWidget(self._chk_data)

        small = f"color: {P.TEXT_LABEL}; font-size: 11px;"
        self._intensity_title = QLabel("min intensity")
        self._intensity_title.setStyleSheet(small)
        row.addWidget(self._intensity_title)
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self._intensity_slider.setObjectName("deployLocsSlider")
        self._intensity_slider.setRange(0, self.INTENSITY_MAX_STEPS)
        self._intensity_slider.setFixedWidth(110)
        self._intensity_slider.setToolTip(
            "Hide cells whose intensity (the file's SNR + 1) is below this: the noise beyond the "
            "aerosol layer. Noise sits near 1.000; about 1.005–1.02 usually clears it. "
            "All the way left shows every cell, as recorded.")
        self._intensity_slider.valueChanged.connect(self._on_intensity_moved)
        row.addWidget(self._intensity_slider)
        self._intensity_value = QLabel()
        self._intensity_value.setStyleSheet(small)
        self._intensity_value.setMinimumWidth(36)
        row.addWidget(self._intensity_value)
        # redraw once the slider settles, not on every step of a drag
        self._intensity_timer = QTimer(self)
        self._intensity_timer.setSingleShot(True)
        self._intensity_timer.setInterval(250)
        self._intensity_timer.timeout.connect(lambda: self.min_intensity_changed.emit(self.min_intensity()))
        self._on_intensity_moved(0, emit=False)
        self._set_intensity_available(False)

        self._winds_button = QToolButton()
        self._winds_button.setText("WINDS")
        self._winds_button.setToolTip("The lidars' wind profiles (VAD and CSM retrievals) for this date: "
                                      "hodograph and time-height")
        self._winds_button.clicked.connect(self.winds_requested.emit)
        row.addWidget(self._winds_button)
        row.addStretch(1)

        outer.addWidget(self._drawer)

    # ── intensity filter ──────────────────────────────────
    def min_intensity(self) -> float | None:
        steps = self._intensity_slider.value()
        if not steps or not self._intensity_slider.isEnabled():
            return None
        return round(1.0 + steps * self.INTENSITY_STEP, 3)

    def _on_intensity_moved(self, steps: int, emit: bool = True) -> None:
        self._intensity_value.setText(f"{1.0 + steps * self.INTENSITY_STEP:.3f}" if steps else "off")
        if emit:
            self._intensity_timer.start()

    def _set_intensity_available(self, available: bool) -> None:
        """Only files with an intensity field can be filtered."""
        for widget in (self._intensity_title, self._intensity_slider, self._intensity_value):
            widget.setEnabled(available)
        self._intensity_title.setToolTip("" if available else "This lidar's files have no intensity field")

    # ── the picked lidar ──────────────────────────────────
    def set_status(self, text: str) -> None:
        """Shown on the lidar button while none is picked."""
        self._status = str(text or "")
        if not self._chosen:
            self._location_button.setText(self._status)

    def set_location(self, title: str | None, details: str = "") -> None:
        """The picked lidar, e.g. 'Lidar truck · stop 2 of 4', with its
        position and scanning times on hover; None: none picked."""
        self._chosen = bool(title)
        self._location_button.setText(title or self._status)
        self._location_button.setToolTip(details if title else "")
        if not title:
            self._field_combo.setEnabled(False)
            self.set_heading_notice(None)

    def _on_location_clicked(self) -> None:
        if self._chosen:
            self.location_requested.emit()

    def set_fields(self, fields: dict) -> None:
        """Fields across the picked lidar's files (name -> units); keeps the
        choice when it's still there, else velocity."""
        current = self._field_combo.currentData()
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for name, units in fields.items():
            label = name.replace("_", " ").capitalize()
            self._field_combo.addItem(f"{label} ({units})" if units else label, name)
        index = self._field_combo.findData(current) if current else -1
        if index < 0:
            index = max(0, self._field_combo.findData("velocity"))
        self._field_combo.setCurrentIndex(index)
        self._field_combo.blockSignals(False)
        self._field_combo.setEnabled(bool(fields))
        self._set_intensity_available("intensity" in fields)
        if current != self._field_combo.currentData():
            self._on_field_changed()

    def set_heading_notice(self, provenance: dict | None) -> None:
        """An amber ⚠ when the truck's missing heading was estimated from its
        compass, red when its scans couldn't be oriented; why, on hover."""
        provenance = provenance or {}
        if provenance.get("heading_missing_in_file"):
            color = P.WARN
        elif provenance.get("north_referenced") is False:
            color = P.ERROR_SOFT
        else:
            self._heading_warning.hide()
            return
        self._heading_warning.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
        self._heading_warning.setToolTip(provenance.get("azimuth_reference", ""))
        self._heading_warning.show()

    # ── switches ──────────────────────────────────────────
    def current_field(self) -> str | None:
        return self._field_combo.currentData()

    def data_is_on(self) -> bool:
        return self._chk_data.isChecked()

    def set_data_on(self, on: bool) -> None:
        self._chk_data.setChecked(on)

    def _on_field_changed(self, _index=None):
        name = self._field_combo.currentData()
        if name:
            self.field_selected.emit(name)
