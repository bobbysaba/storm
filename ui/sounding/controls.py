
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QToolButton, QComboBox
from PyQt6.QtCore import pyqtSignal, Qt
from ui import palette as P
from ui.widgets.drawer import Drawer


class SoundingControls(Drawer):
    """Floating drawer that appears when the SOUNDING toolbar button is active.

    Presents mutually-exclusive source buttons:
        [HRRR]  — click anywhere on map to fetch HRRR point sounding (live only)
        [OBS]   — radiosonde station dots appear; click one to fetch observed data
        [NSSL]  — a provider's soundings, named by it (NSSL: the lidar
                  truck's sondes; in archive then its retrievals)
        [UAS]   — a provider's uncrewed-aircraft ascents, picked by site,
                  named by it (archive only; NSSL: COPTERSONDE)
        then one button per sounding source (data/sources.py), live only;
        its mode is "source:<id>"
    """

    mode_changed   = pyqtSignal(str)   # "hrrr", "obs", "provider", "uas" or "source:<id>"
    uas_selected   = pyqtSignal(object)   # SoundingSet of the picked site

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
        self._btn_provider = self._btn("PROVIDER")
        self._btn_uas = self._btn("UAS")
        self._uas_combo = QComboBox()
        self._uas_combo.setToolTip("Launch site")
        self._uas_combo.currentIndexChanged.connect(self._on_uas_selected)
        self._buttons = {"hrrr": self._btn_hrrr, "obs": self._btn_obs,
                         "provider": self._btn_provider, "uas": self._btn_uas}

        self._btn_hrrr.setChecked(True)   # default source

        self._btn_hrrr.toggled.connect(lambda on: self._on_mode_toggled("hrrr", on))
        self._btn_obs.toggled.connect( lambda on: self._on_mode_toggled("obs",  on))
        self._btn_provider.toggled.connect(lambda on: self._on_mode_toggled("provider", on))
        self._btn_uas.toggled.connect(lambda on: self._on_mode_toggled("uas", on))

        self._hrrr_div = self._vdiv()
        row.addWidget(self._btn_hrrr)
        row.addWidget(self._hrrr_div)
        row.addWidget(self._btn_obs)
        self._provider_div = self._vdiv()
        row.addWidget(self._provider_div)
        row.addWidget(self._btn_provider)
        self._uas_div = self._vdiv()
        row.addWidget(self._uas_div)
        row.addWidget(self._btn_uas)
        row.addWidget(self._uas_combo)
        self._row = row
        row.addStretch(1)
        for w in (self._uas_div, self._btn_uas, self._uas_combo):
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
        self._show_uas_combo()

        self.mode_changed.emit(mode)


    def set_hrrr_available(self, available: bool):
        """Archive mode has no HRRR source: hide its button and default to OBS."""
        self._btn_hrrr.setVisible(available)
        self._hrrr_div.setVisible(available)
        self._default_mode = "hrrr" if available else "obs"
        self.reset_mode()

    def set_provider_available(self, label: str | None):
        """A provider's soundings (live_sounding_fetcher, or ARCHIVE's
        archive_sounding_fetcher), on a button named *label* ("NSSL");
        hidden when None."""
        if label:
            self._btn_provider.setText(label)
        self._provider_div.setVisible(bool(label))
        self._btn_provider.setVisible(bool(label))
        self.content_changed()

    def set_uas_available(self, label: str | None, tooltip: str = ""):
        """A provider's uncrewed-aircraft ascents (archive mode), on a button
        named *label* (NSSL's: COPTERSONDE); hidden when None."""
        if label:
            self._btn_uas.setText(label)
            self._btn_uas.setToolTip(tooltip or "Uncrewed-aircraft (UAS) ascents on this day")
        self._uas_div.setVisible(bool(label))
        self._btn_uas.setVisible(bool(label))
        self._show_uas_combo()
        self.content_changed()

    def add_source_button(self, mode: str, short: str, name: str, missing_key: str = ""):
        """A button for a sounding source; greyed out, naming the variable,
        when its API key isn't set."""
        btn = self._btn(short)
        btn.setToolTip(f"{name}: needs {missing_key} in .env" if missing_key else name)
        btn.setEnabled(not missing_key)
        btn.toggled.connect(lambda on: self._on_mode_toggled(mode, on))
        self._buttons[mode] = btn
        # before the stretch
        at = self._row.count() - 1
        self._row.insertWidget(at, self._vdiv())
        self._row.insertWidget(at + 1, btn)
        self.content_changed()

    def reset_mode(self):
        """Silently reset to the default mode (called when SOUNDING button is deactivated)."""
        self._active_mode = self._default_mode
        for mode, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(mode == self._default_mode)
            btn.blockSignals(False)
        self._show_uas_combo()

    def set_uas_sets(self, sets: dict):
        """List the day's UAS launch sites ({site: SoundingSet}) and emit the
        first, or the one already picked if it's still there."""
        previous = self._uas_combo.currentText()
        self._uas_combo.blockSignals(True)
        self._uas_combo.clear()
        for site, sset in sorted(sets.items()):
            self._uas_combo.addItem(site, sset)
        index = self._uas_combo.findText(previous)
        self._uas_combo.setCurrentIndex(index if index >= 0 else 0)
        self._uas_combo.blockSignals(False)
        self._show_uas_combo()
        self.content_changed()
        if sets:
            self._on_uas_selected()

    def _show_uas_combo(self):
        self._uas_combo.setVisible(
            self._btn_uas.isVisibleTo(self) and self._active_mode == "uas"
            and self._uas_combo.count() > 0
        )

    def _on_uas_selected(self, _index=None):
        sset = self._uas_combo.currentData()
        if sset is not None and self._active_mode == "uas":
            self.uas_selected.emit(sset)

    @property
    def active_mode(self) -> str:
        return self._active_mode
