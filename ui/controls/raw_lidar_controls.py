"""LIDAR drawer (archive): one row, like the radar's. A lidar is picked the
way a radar station is, on the map: while the drawer is open every place a
lidar ran PPI scans from this session is marked (a fixed lidar's site, or
each stop of a mobile one; core/lidar_scans.locations). The row shows
the picked lidar, SCANS for its scan window (ui/dialogs/lidar_scan_dialog.py:
field, intensity threshold, readout, zoom, and drawing on the map), STARES
for the lidars' vertical stares (ui/dialogs/lidar_stare_dialog.py: vertical
velocity against time and height) and WINDS for their wind profiles.

Two kinds of instrument, named by the provider (NSSL's lidar truck and
CLAMPS trailers):
  * mobile: placed from its vehicle's GPS, its heading recorded in its
    files or estimated from its compass (a ⚠ says so);
  * fixed: parked at a site its files record.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from ui import palette as P
from ui.widgets.drawer import Drawer


class RawLidarControls(Drawer):
    location_requested = pyqtSignal()         # the lidar button: center the map on it
    scans_requested = pyqtSignal()            # SCANS: the picked lidar's scan window
    stares_requested = pyqtSignal()           # STARES: the lidars' vertical stares
    winds_requested = pyqtSignal()

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

        self._scans_button = QToolButton()
        self._scans_button.setText("SCANS")
        self._scans_button.setToolTip("The picked lidar's PPI and sector scans in their own window: "
                                      "field, intensity threshold, readout and zoom, and drawing on the map")
        self._scans_button.setEnabled(False)
        self._scans_button.clicked.connect(self.scans_requested.emit)
        row.addWidget(self._scans_button)

        self._stares_button = QToolButton()
        self._stares_button.setText("STARES")
        self._stares_button.setEnabled(False)
        self._stares_button.setToolTip("Looking for lidar stares…")
        self._stares_button.clicked.connect(self.stares_requested.emit)
        row.addWidget(self._stares_button)

        self._winds_button = QToolButton()
        self._winds_button.setText("WINDS")
        self._winds_button.setToolTip("The lidars' wind profiles (VAD and CSM retrievals) for this date: "
                                      "hodograph and time-height")
        self._winds_button.clicked.connect(self.winds_requested.emit)
        row.addWidget(self._winds_button)
        row.addStretch(1)

        outer.addWidget(self._drawer)

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
        self._scans_button.setEnabled(self._chosen)
        if not title:
            self.set_heading_notice(None)

    def set_stares_available(self, available: bool, tooltip: str) -> None:
        """STARES: on when a lidar has vertical stares this session; why
        not, on hover."""
        self._stares_button.setEnabled(available)
        self._stares_button.setToolTip(tooltip)

    def _on_location_clicked(self) -> None:
        if self._chosen:
            self.location_requested.emit()

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
