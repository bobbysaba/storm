"""A provider's own ARCHIVE drawer (the archive_drawer hook; NSSL's): one row
of its items, as the provider names them:

    [VEHICLES] | [LIDAR] | [SOUNDING]  [UAS] [site ▾]  [RETRIEVALS]

VEHICLES and LIDAR stay pressed while on (LIDAR opens the lidar drawer
below); SOUNDING, UAS and RETRIEVALS fetch and open the sounding viewer, UAS
then listing its launch sites. An item without data for the date is greyed
out (set_availability).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QToolButton, QVBoxLayout, QWidget

from ui import palette as P
from ui.widgets.drawer import Drawer

# items that stay pressed while on; the rest are one-shot fetches
TOGGLES = ("vehicles", "lidar")


class ProviderDrawerControls(Drawer):
    item_toggled = pyqtSignal(str, bool)      # "vehicles" / "lidar", on
    item_requested = pyqtSignal(str)          # "sounding" / "uas" / "retrievals"
    uas_selected = pyqtSignal(object)         # SoundingSet of the picked site

    def __init__(self, items, parent=None):
        """*items*: (key, label, tooltip) in order (NsslArchiveDrawer.items)."""
        super().__init__(parent)
        self.buttons: dict[str, QToolButton] = {}
        self._tooltips: dict[str, str] = {}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        drawer = QWidget()
        col = QVBoxLayout(drawer)
        col.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for i, (key, label, tooltip) in enumerate(items):
            if i and key in TOGGLES or key == "sounding":
                row.addWidget(self._vdiv())
            btn = QToolButton()
            btn.setText(label.upper())
            btn.setToolTip(tooltip)
            self._tooltips[key] = tooltip
            if key in TOGGLES:
                btn.setCheckable(True)
                btn.toggled.connect(lambda on, k=key: self.item_toggled.emit(k, on))
            else:
                btn.clicked.connect(lambda _=False, k=key: self.item_requested.emit(k))
            self.buttons[key] = btn
            row.addWidget(btn)
            if key == "uas":
                self.uas_combo = QComboBox()
                self.uas_combo.setToolTip("Launch site")
                self.uas_combo.currentIndexChanged.connect(self._on_uas_selected)
                self.uas_combo.hide()
                row.addWidget(self.uas_combo)
        row.addStretch(1)
        col.addLayout(row)
        outer.addWidget(drawer)

    @staticmethod
    def _vdiv() -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        return div

    def set_availability(self, available: dict) -> None:
        """Grey out the items *available* says have no data for the date
        (False); True and None (unknown) leave an item on."""
        for key, btn in self.buttons.items():
            has = available.get(key)
            btn.setEnabled(has is not False)
            btn.setToolTip(self._tooltips[key] if has is not False
                           else f"{self._tooltips[key]}: none for this date")
        self.content_changed()

    def any_available(self) -> bool:
        return any(btn.isEnabled() for btn in self.buttons.values())

    def set_uas_sets(self, sets: dict) -> None:
        """List the day's launch sites ({site: SoundingSet}) and emit the
        first, or the one already picked if it's still there."""
        previous = self.uas_combo.currentText()
        self.uas_combo.blockSignals(True)
        self.uas_combo.clear()
        for site, sset in sorted(sets.items()):
            self.uas_combo.addItem(site, sset)
        index = self.uas_combo.findText(previous)
        self.uas_combo.setCurrentIndex(index if index >= 0 else 0)
        self.uas_combo.blockSignals(False)
        self.uas_combo.setVisible(bool(sets))
        self.content_changed()
        if sets:
            self._on_uas_selected()

    def _on_uas_selected(self, _index=None) -> None:
        sset = self.uas_combo.currentData()
        if sset is not None:
            self.uas_selected.emit(sset)
