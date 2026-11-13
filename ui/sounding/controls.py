
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QComboBox
from PyQt6.QtCore import pyqtSignal, Qt
from ui import palette as P
from ui.widgets.drawer import Drawer


class SoundingControls(Drawer):
    """Floating drawer that appears when the SOUNDING toolbar button is active.

    Presents mutually-exclusive source buttons:
        [HRRR]  — click anywhere on map to fetch HRRR point sounding (live only)
        [OBS]   — radiosonde station dots appear; click one to fetch observed data
        [NSSL]  — NSSL mobile soundings (archive: lidar truck sondes, then
                  CLAMPS TROPoe retrievals)
        [COPTERSONDE] — PERiLS CopterSonde ascents, picked by site (archive only)
    """

    mode_changed   = pyqtSignal(str)   # "hrrr", "obs", "nssl" or "coptersonde"
    coptersonde_selected = pyqtSignal(object)   # SoundingSet of the picked site

    def __init__(self, parent=None):
        super().__init__(parent)
        self._default_mode = "hrrr"
        self._active_mode = "hrrr"
        self._setup_ui()


    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("soundingDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._btn_hrrr = self._btn("HRRR")
        self._btn_obs  = self._btn("OBS")
        self._btn_nssl = self._btn("NSSL")
        self._btn_copter = self._btn("COPTERSONDE")
        self._btn_copter.setToolTip("PERiLS CopterSonde (UAS) ascents on this day")
        self._copter_combo = QComboBox()
        self._copter_combo.setToolTip("CopterSonde launch site")
        self._copter_combo.currentIndexChanged.connect(self._on_copter_selected)
        self._buttons = {"hrrr": self._btn_hrrr, "obs": self._btn_obs,
                         "nssl": self._btn_nssl, "coptersonde": self._btn_copter}

        self._btn_hrrr.setChecked(True)   # default source

        self._btn_hrrr.toggled.connect(lambda on: self._on_mode_toggled("hrrr", on))
        self._btn_obs.toggled.connect( lambda on: self._on_mode_toggled("obs",  on))
        self._btn_nssl.toggled.connect(lambda on: self._on_mode_toggled("nssl", on))
        self._btn_copter.toggled.connect(lambda on: self._on_mode_toggled("coptersonde", on))

        self._hrrr_div = self._vdiv()
        row.addWidget(self._btn_hrrr)
        row.addWidget(self._hrrr_div)
        row.addWidget(self._btn_obs)
        row.addWidget(self._vdiv())
        row.addWidget(self._btn_nssl)
        self._copter_div = self._vdiv()
        row.addWidget(self._copter_div)
        row.addWidget(self._btn_copter)
        row.addWidget(self._copter_combo)
        row.addStretch(1)
        for w in (self._copter_div, self._btn_copter, self._copter_combo):
            w.hide()

        col.addWidget(btn_row)
        outer.addWidget(self._drawer)

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        return div


    def _on_mode_toggled(self, mode: str, checked: bool):
        if not checked:
            # prevent both buttons from being unchecked simultaneously
            if self._active_mode == mode:
                # re-check the button that was just unchecked
                btn = self._buttons[mode]
                btn.blockSignals(True)
                btn.setChecked(True)
                btn.blockSignals(False)
            return

        self._active_mode = mode

        # enforce mutual exclusivity
        for btn_mode, btn in self._buttons.items():
            if btn_mode != mode:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._show_copter_combo()

        self.mode_changed.emit(mode)


    def set_hrrr_available(self, available: bool):
        """Archive mode has no HRRR source: hide its button and default to OBS."""
        self._btn_hrrr.setVisible(available)
        self._hrrr_div.setVisible(available)
        self._default_mode = "hrrr" if available else "obs"
        self.reset_mode()

    def set_coptersonde_available(self, available: bool):
        """Show COPTERSONDE (archive mode)."""
        self._copter_div.setVisible(available)
        self._btn_copter.setVisible(available)
        self._show_copter_combo()
        self.content_changed()

    def reset_mode(self):
        """Silently reset to the default mode (called when SOUNDING button is deactivated)."""
        self._active_mode = self._default_mode
        for mode, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(mode == self._default_mode)
            btn.blockSignals(False)
        self._show_copter_combo()

    def set_coptersondes(self, sets: dict):
        """List the day's CopterSonde sites ({site: SoundingSet}) and emit the
        first, or the one already picked if it's still there."""
        previous = self._copter_combo.currentText()
        self._copter_combo.blockSignals(True)
        self._copter_combo.clear()
        for site, sset in sorted(sets.items()):
            self._copter_combo.addItem(site, sset)
        index = self._copter_combo.findText(previous)
        self._copter_combo.setCurrentIndex(index if index >= 0 else 0)
        self._copter_combo.blockSignals(False)
        self._show_copter_combo()
        self.content_changed()
        if sets:
            self._on_copter_selected()

    def _show_copter_combo(self):
        self._copter_combo.setVisible(
            self._btn_copter.isVisibleTo(self) and self._active_mode == "coptersonde"
            and self._copter_combo.count() > 0
        )

    def _on_copter_selected(self, _index=None):
        sset = self._copter_combo.currentData()
        if sset is not None and self._active_mode == "coptersonde":
            self.coptersonde_selected.emit(sset)

    @property
    def active_mode(self) -> str:
        return self._active_mode
