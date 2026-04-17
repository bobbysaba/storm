# ui/scan_sector_dialog.py
# Dialog for activating or updating the local vehicle's current scan sector.

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.scan_sector import ScanSector


_KEY_PREFIX = "scan_sector"


class ScanSectorDialog(QDialog):
    """Collect scan-sector settings from the local operator."""

    def __init__(
        self,
        vehicle_id: str,
        lat: float,
        lon: float,
        active_scan: ScanSector | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Scan Sector")
        self._vehicle_id = vehicle_id
        self._lat = lat
        self._lon = lon
        self._active_scan = active_scan
        self.action = "cancel"

        self.setMinimumWidth(320)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("CURRENT SAMPLING")
        title.setStyleSheet("font-size: 11px; font-weight: 700; letter-spacing: 1.2px; color: #00CFFF;")
        root.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Point", "point")
        self.mode_combo.addItem("Circle", "circle")
        self.mode_combo.addItem("Sector", "sector")
        form.addRow("Mode", self.mode_combo)

        self.range_spin = _meters_spin(0, 300000, 8000)
        form.addRow("Range", self.range_spin)

        self.inner_spin = _meters_spin(0, 300000, 0)
        form.addRow("Inner Range", self.inner_spin)

        self.azimuth_spin = _deg_spin(0, 359.9, 270)
        form.addRow("Azimuth", self.azimuth_spin)

        self.width_spin = _deg_spin(0.1, 360, 60)
        form.addRow("Beam Width", self.width_spin)

        self.follow_check = QCheckBox("Follow vehicle position")
        form.addRow("", self.follow_check)

        root.addLayout(form)

        note = QLabel(f"Anchor: {lat:.4f}, {lon:.4f}")
        note.setStyleSheet("color: #8C96AA; font-size: 10px;")
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()

        self.stop_btn = QPushButton("Stop Scan")
        self.stop_btn.clicked.connect(self._stop)
        buttons.addWidget(self.stop_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self.apply_btn = QPushButton("Update Scan" if active_scan and active_scan.active else "Start Scan")
        self.apply_btn.clicked.connect(self._apply)
        buttons.addWidget(self.apply_btn)
        root.addLayout(buttons)

        self._load_values()
        self.mode_combo.currentIndexChanged.connect(self._sync_enabled)
        self._sync_enabled()

    def scan_sector(self) -> ScanSector:
        mode = self.mode_combo.currentData()
        return ScanSector(
            vehicle_id=self._vehicle_id,
            active=True,
            mode=mode,
            lat=self._lat,
            lon=self._lon,
            range_m=self.range_spin.value() if mode in ("circle", "sector") else None,
            inner_range_m=self.inner_spin.value() if mode in ("circle", "sector") else 0.0,
            azimuth_deg=self.azimuth_spin.value() if mode == "sector" else None,
            beam_width_deg=self.width_spin.value() if mode == "sector" else None,
            follow_vehicle=self.follow_check.isChecked(),
        )

    def _load_values(self):
        s = QSettings("NSSL", "STORM")
        scan = self._active_scan if self._active_scan and self._active_scan.active else None

        mode = scan.mode if scan else s.value(f"{_KEY_PREFIX}/mode", "sector", type=str)
        idx = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 2)

        self.range_spin.setValue(scan.range_m if scan and scan.range_m is not None else s.value(f"{_KEY_PREFIX}/range_m", 8000.0, type=float))
        self.inner_spin.setValue(scan.inner_range_m if scan else s.value(f"{_KEY_PREFIX}/inner_range_m", 0.0, type=float))
        self.azimuth_spin.setValue(scan.azimuth_deg if scan and scan.azimuth_deg is not None else s.value(f"{_KEY_PREFIX}/azimuth_deg", 270.0, type=float))
        self.width_spin.setValue(scan.beam_width_deg if scan and scan.beam_width_deg is not None else s.value(f"{_KEY_PREFIX}/beam_width_deg", 60.0, type=float))

        if scan:
            follow = scan.follow_vehicle
        else:
            follow = s.value(f"{_KEY_PREFIX}/follow_vehicle", mode == "point", type=bool)
        self.follow_check.setChecked(bool(follow))
        self.stop_btn.setVisible(bool(scan))

    def _save_values(self):
        s = QSettings("NSSL", "STORM")
        s.setValue(f"{_KEY_PREFIX}/mode", self.mode_combo.currentData())
        s.setValue(f"{_KEY_PREFIX}/range_m", self.range_spin.value())
        s.setValue(f"{_KEY_PREFIX}/inner_range_m", self.inner_spin.value())
        s.setValue(f"{_KEY_PREFIX}/azimuth_deg", self.azimuth_spin.value())
        s.setValue(f"{_KEY_PREFIX}/beam_width_deg", self.width_spin.value())
        s.setValue(f"{_KEY_PREFIX}/follow_vehicle", self.follow_check.isChecked())

    def _sync_enabled(self):
        mode = self.mode_combo.currentData()
        ranged = mode in ("circle", "sector")
        sector = mode == "sector"
        self.range_spin.setEnabled(ranged)
        self.inner_spin.setEnabled(ranged)
        self.azimuth_spin.setEnabled(sector)
        self.width_spin.setEnabled(sector)
        if mode == "point" and not self._active_scan:
            self.follow_check.setChecked(True)

    def _apply(self):
        self._save_values()
        self.action = "apply"
        self.accept()

    def _stop(self):
        self.action = "stop"
        self.accept()

def _meters_spin(min_v: float, max_v: float, default: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(min_v, max_v)
    spin.setDecimals(0)
    spin.setSingleStep(250)
    spin.setSuffix(" m")
    spin.setValue(default)
    return spin


def _deg_spin(min_v: float, max_v: float, default: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(min_v, max_v)
    spin.setDecimals(1)
    spin.setSingleStep(5)
    spin.setSuffix(" deg")
    spin.setValue(default)
    return spin
