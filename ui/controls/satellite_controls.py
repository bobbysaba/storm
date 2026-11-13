
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QFrame, QToolButton, QLabel,
    QSlider, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt, QEvent
from PyQt6.QtGui import QImage, QPixmap
import numpy as np
from core.goes_ir import IR_MAX_K, IR_MIN_K, ir_rgb
from ui import palette as P
from ui.widgets.drawer import Drawer
from ui.layer_status import StatusTimeLabel


class SatelliteControls(Drawer):
    """
    Collapsible two-row drawer for GOES-East satellite overlay controls.

    One row: CONUS / MESO-1 / MESO-2 mode buttons + opacity slider. In
    ARCHIVE, VIS / IR band buttons follow the sectors, with an IR color bar
    under the row while IR is shown.

    The playback widgets (⏮ ⏪ ▶/⏸ ⏩ ⏭, frame slider, time, speed) are never
    shown: they hold the loop state, and the bottom loop timeline drives them
    through the playback API (or the radar loop picks the frame).

    MESO buttons are disabled until SatelliteFetcher confirms sector exists.
    Playback controls are disabled until at least 2 frames are cached.

    Signals:
        mode_changed(str)      — "", "conus", "meso1", "meso2"
        band_changed(str)      — "vis", "ir" (ARCHIVE only)
        opacity_changed(float) — 0.0–1.0
        frame_requested(int)   — user selected a specific cache frame index
        loop_toggled(bool)     — playback loop started/stopped
        meso_preview(int,bool) — hover preview for MESO-1/2 sector box
    """

    mode_changed    = pyqtSignal(str)
    band_changed    = pyqtSignal(str)
    opacity_changed = pyqtSignal(float)
    frame_requested = pyqtSignal(int)
    loop_toggled    = pyqtSignal(bool)
    speed_changed   = pyqtSignal(int)      # new interval in ms
    meso_preview    = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating  = False
        self._pending_mode = ""     # restored MESO mode waiting for its sector
        self._meso_bboxes: dict[int, dict | None] = {1: None, 2: None}
        self._setup_ui()


    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("satelliteDrawer")
        col = QVBoxLayout(self._drawer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._btn_conus = self._btn("CONUS")
        self._btn_meso1 = self._btn("MESO-1")
        self._btn_meso2 = self._btn("MESO-2")

        self._btn_meso1.setEnabled(False)
        self._btn_meso1.setToolTip("Mesoscale sector 1 — not yet available")
        self._btn_meso2.setEnabled(False)
        self._btn_meso2.setToolTip("Mesoscale sector 2 — not yet available")

        self._btn_meso1.installEventFilter(self)
        self._btn_meso2.installEventFilter(self)

        self._btn_conus.toggled.connect(lambda on: self._on_mode_toggled("conus", on))
        self._btn_meso1.toggled.connect(lambda on: self._on_mode_toggled("meso1", on))
        self._btn_meso2.toggled.connect(lambda on: self._on_mode_toggled("meso2", on))

        for b in (self._btn_conus, self._btn_meso1, self._btn_meso2):
            r1.addWidget(b)

        r1.addWidget(self._vdiv())

        # band choice: archive only (live imagery is visible)
        self._btn_vis = self._btn("VIS")
        self._btn_vis.setChecked(True)
        self._btn_vis.setToolTip("Visible (band 2) reflectance")
        self._btn_ir = self._btn("IR")
        self._btn_ir.setToolTip("Infrared (band 13) cloud-top temperature")
        self._btn_vis.toggled.connect(lambda on: self._on_band_toggled("vis", on))
        self._btn_ir.toggled.connect(lambda on: self._on_band_toggled("ir", on))
        self._band_div = self._vdiv()
        self._band_widgets = (self._btn_vis, self._btn_ir, self._band_div)
        for w in self._band_widgets:
            r1.addWidget(w)
            w.hide()

        lbl = QLabel("OPACITY")
        lbl.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px; letter-spacing: 0.5px;")
        lbl.setFixedHeight(22)
        r1.addWidget(lbl)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(70)
        self._opacity_slider.setFixedHeight(22)
        self._opacity_slider.setFixedWidth(110)
        self._opacity_slider.setToolTip("Satellite image opacity")
        self._opacity_slider.valueChanged.connect(
            lambda v: self.opacity_changed.emit(v / 100.0)
        )
        r1.addWidget(self._opacity_slider)
        r1.addStretch(1)
        col.addWidget(row1)

        self._ir_legend = self._make_ir_legend()
        self._ir_legend.hide()
        col.addWidget(self._ir_legend)

        # why the day has no imagery (ARCHIVE before GOES-16, or a gap)
        self._note = QLabel()
        self._note.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 11px;")
        self._note.hide()
        col.addWidget(self._note)

        row2 = QWidget()
        row2.setObjectName("satPlaybackRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        self._btn_jump_start = self._pbtn("⏮", "Oldest frame")
        self._btn_back       = self._pbtn("⏪", "Step back one frame")
        self._btn_play       = self._pbtn("▶", "Play / Pause loop", checkable=True)
        self._btn_fwd        = self._pbtn("⏩", "Step forward one frame")
        self._btn_jump_end   = self._pbtn("⏭", "Latest (live)")

        self._btn_jump_start.clicked.connect(self._on_jump_start)
        self._btn_back.clicked.connect(self._on_step_back)
        self._btn_play.toggled.connect(self._on_play_toggled)
        self._btn_fwd.clicked.connect(self._on_step_forward)
        self._btn_jump_end.clicked.connect(self._on_jump_end)

        for b in (self._btn_jump_start, self._btn_back, self._btn_play,
                  self._btn_fwd, self._btn_jump_end):
            b.setEnabled(False)
            r2.addWidget(b)
        self._has_history = False
        self._synced = False

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.setFixedHeight(26)
        self._frame_slider.setMinimumWidth(80)
        self._frame_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._frame_slider.setEnabled(False)
        self._frame_slider.sliderReleased.connect(self._on_slider_released)
        r2.addWidget(self._frame_slider)

        self._time_label = StatusTimeLabel()
        self._time_label.setObjectName("satTimeLabel")
        self._time_label.setFixedHeight(26)
        self._time_label.setMinimumWidth(52)
        r2.addWidget(self._time_label)

        # speed selector — 0.5×, 1×, 2×, 3×
        self._speed_combo = QComboBox()
        self._speed_combo.setFixedHeight(26)
        self._speed_combo.setMaximumWidth(5)
        self._speed_combo.setToolTip("Playback speed")
        for label, ms in [("0.5×", 1200), ("1×", 600), ("2×", 300), ("3×", 200)]:
            self._speed_combo.addItem(label, userData=ms)
        self._speed_combo.setCurrentIndex(1)   # default 1×
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_combo)

        row2.hide()   # holds the state only; never added to the drawer
        self._playback_row = row2
        outer.addWidget(self._drawer)

    def _btn(self, label: str) -> QToolButton:
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setChecked(False)
        return b

    def _pbtn(self, text: str, tip: str, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setFixedSize(32, 26)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        return b

    def _make_ir_legend(self) -> QWidget:
        """-90 °C … 50 °C color bar of the IR color table."""
        row = QWidget()
        row.setObjectName("satIrLegend")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        width, height = 220, 10
        kelvin = np.linspace(IR_MIN_K, IR_MAX_K, width, dtype=np.float32)
        rgb = np.ascontiguousarray(np.repeat(ir_rgb(kelvin)[None, :, :], height, axis=0))
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()
        bar = QLabel()
        bar.setPixmap(QPixmap.fromImage(image))
        bar.setFixedSize(width, height)
        bar.setToolTip("Cloud-top temperature: grey above -30 °C, colors for colder tops")
        style = f"color: {P.TEXT_SECONDARY}; font-size: 10px;"
        cold = QLabel(f"{IR_MIN_K - 273.15:.0f} °C")
        warm = QLabel(f"{IR_MAX_K - 273.15:.0f} °C")
        for w in (cold, warm):
            w.setStyleSheet(style)
        lay.addWidget(cold)
        lay.addWidget(bar)
        lay.addWidget(warm)
        lay.addStretch(1)
        return row

    def _vdiv(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {P.BORDER}; margin: 4px 2px;")
        return div


    def set_meso_available(self, idx: int, available: bool, bbox: dict | None = None):
        btn = self._btn_meso1 if idx == 1 else self._btn_meso2
        btn.setEnabled(available)
        self._meso_bboxes[idx] = bbox if available else None
        if self._pending_mode == f"meso{idx}":
            self._pending_mode = ""
            # restored sector is gone: fall back to CONUS rather than nothing
            (btn if available else self._btn_conus).setChecked(True)
        if available:
            btn.setToolTip(f"Mesoscale sector {idx}")
        else:
            btn.setToolTip(f"Mesoscale sector {idx} — not currently available")
            if btn.isChecked():
                self._set_mode("conus")
                self.mode_changed.emit("conus")

    def sector_buttons(self) -> dict[str, QToolButton]:
        """Mode key -> its sector button (CONUS, MESO-1, MESO-2)."""
        return {"conus": self._btn_conus, "meso1": self._btn_meso1, "meso2": self._btn_meso2}

    def select_mode(self, mode: str) -> None:
        """Switch to a sector through its button; a disabled sector is ignored."""
        btn = self.sector_buttons().get(mode)
        if btn is not None and btn.isEnabled():
            btn.setChecked(True)

    def set_cache_size(self, n: int):
        """Update slider range as frame cache grows; stay at live if already there."""
        was_live = self.is_at_latest_frame()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, max(0, n - 1))
        if was_live:
            self._frame_slider.setValue(n - 1)
        self._frame_slider.blockSignals(False)
        self._has_history = n > 1
        self._apply_playback_enabled()

    def set_synced(self, synced: bool) -> None:
        """While synced the radar loop picks the frame, so own playback is off."""
        if synced == self._synced:
            return
        self._synced = synced
        if synced:
            self.stop_loop()
        self._apply_playback_enabled()

    def is_synced(self) -> bool:
        return self._synced

    def opacity(self) -> float:
        return self._opacity_slider.value() / 100.0

    def jump_to_latest(self) -> None:
        """Stop the loop and show the live frame."""
        self.stop_loop()
        self._on_jump_end()

    def _apply_playback_enabled(self) -> None:
        enabled = self._has_history and not self._synced
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(enabled)

    def set_frame(self, idx: int):
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)

    def set_scan_time(self, time_str: str, when=None):
        self._time_label.set_frame(time_str, when)

    def bind_status(self, status) -> None:
        """Color the time label and show data age from a LayerStatus."""
        self._time_label.bind(status)

    def current_frame(self) -> int:
        return self._frame_slider.value()

    def band_buttons(self) -> dict[str, QToolButton]:
        """Band key -> its button; empty outside ARCHIVE, where they're hidden."""
        if not self._btn_vis.isVisibleTo(self):
            return {}
        return {"vis": self._btn_vis, "ir": self._btn_ir}

    def select_band(self, band: str) -> None:
        btn = self.band_buttons().get(band)
        if btn is not None and btn.isEnabled():
            btn.setChecked(True)

    def current_band(self) -> str:
        return "ir" if self._btn_ir.isChecked() else "vis"

    def set_unavailable(self, reason: str) -> None:
        """No imagery for this day: sectors and bands off, with the reason."""
        self._pending_mode = ""
        self._set_mode("")
        for btn in (*self.sector_buttons().values(), self._btn_vis, self._btn_ir):
            btn.setEnabled(False)
            btn.setToolTip(reason)
        self._ir_legend.hide()
        self._note.setText(reason)
        self._note.show()

    def current_mode(self) -> str:
        if self._btn_conus.isChecked():
            return "conus"
        if self._btn_meso1.isChecked():
            return "meso1"
        if self._btn_meso2.isChecked():
            return "meso2"
        return ""

    def is_at_latest_frame(self) -> bool:
        return self._frame_slider.value() >= self._frame_slider.maximum()

    def save_state(self) -> dict:
        return {"mode": self.current_mode(), "opacity": self._opacity_slider.value(),
                "band": self.current_band()}

    def restore_state(self, state: dict, mode: bool = True) -> None:
        """Restore opacity and, unless ``mode`` is False, the CONUS/MESO choice.
        A MESO mode whose sector isn't known yet is applied when it arrives."""
        opacity = state.get("opacity")
        if isinstance(opacity, int):
            self._opacity_slider.setValue(max(0, min(100, opacity)))
        if state.get("band") in ("vis", "ir") and self._btn_ir.isEnabled():
            (self._btn_ir if state["band"] == "ir" else self._btn_vis).setChecked(True)
        if not mode:
            return
        btn = {"conus": self._btn_conus, "meso1": self._btn_meso1,
               "meso2": self._btn_meso2}.get(state.get("mode"))
        if btn is None:
            return
        if btn.isEnabled():
            btn.setChecked(True)
        else:
            self._pending_mode = state["mode"]

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def stop_loop(self):
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)

    def toggle_loop(self) -> None:
        if self._btn_play.isEnabled():
            self._btn_play.toggle()

    def step_frame(self, delta: int) -> None:
        btn = self._btn_back if delta < 0 else self._btn_fwd
        if btn.isEnabled():
            btn.click()

    # ── playback API shared with the loop timeline ──
    def request_frame(self, idx: int) -> None:
        """Show frame ``idx`` as if the user picked it."""
        idx = max(0, min(self._frame_slider.maximum(), int(idx)))
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def speed_labels(self) -> list[str]:
        return [self._speed_combo.itemText(i) for i in range(self._speed_combo.count())]

    def speed_index(self) -> int:
        return self._speed_combo.currentIndex()

    def set_speed_index(self, index: int) -> None:
        self._speed_combo.setCurrentIndex(index)

    def reset_cache_ui(self):
        self.stop_loop()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.blockSignals(False)
        self._time_label.clear_frame()
        self._has_history = False
        self._apply_playback_enabled()

    def configure_for_archive(self, enabled: bool):
        if enabled:
            # meso buttons are disabled until live sectors are found; in archive
            self._btn_meso1.setEnabled(True)
            self._btn_meso1.setToolTip("Mesoscale sector 1")
            self._btn_meso2.setEnabled(True)
            self._btn_meso2.setToolTip("Mesoscale sector 2")
            for w in self._band_widgets:
                w.show()
            self._ir_legend.setVisible(self._btn_ir.isChecked())

    def toggle_drawer(self, checked: bool):
        if not checked:
            self.meso_preview.emit(1, False)
            self.meso_preview.emit(2, False)
        super().toggle_drawer(checked)

    def eventFilter(self, obj, event):
        if obj in (self._btn_meso1, self._btn_meso2):
            idx = 1 if obj is self._btn_meso1 else 2
            if event.type() == QEvent.Type.Enter:
                if obj.isEnabled() and self._meso_bboxes.get(idx):
                    self.meso_preview.emit(idx, True)
            elif event.type() == QEvent.Type.Leave:
                self.meso_preview.emit(idx, False)
        return super().eventFilter(obj, event)


    def _set_mode(self, mode: str):
        self._updating = True
        try:
            self._btn_conus.setChecked(mode == "conus")
            self._btn_meso1.setChecked(mode == "meso1")
            self._btn_meso2.setChecked(mode == "meso2")
        finally:
            self._updating = False

    def _on_mode_toggled(self, mode: str, checked: bool):
        if self._updating:
            return
        if checked:
            self._pending_mode = ""
            self._set_mode(mode)
            self.mode_changed.emit(mode)
        else:
            if not any((
                self._btn_conus.isChecked(),
                self._btn_meso1.isChecked(),
                self._btn_meso2.isChecked(),
            )):
                self.mode_changed.emit("")

    def _on_band_toggled(self, band: str, checked: bool):
        if self._updating:
            return
        if not checked:
            # one band is always chosen: unchecking the current one keeps it
            if not (self._btn_vis.isChecked() or self._btn_ir.isChecked()):
                self._updating = True
                (self._btn_ir if band == "ir" else self._btn_vis).setChecked(True)
                self._updating = False
            return
        self._updating = True
        try:
            self._btn_vis.setChecked(band == "vis")
            self._btn_ir.setChecked(band == "ir")
        finally:
            self._updating = False
        self._ir_legend.setVisible(band == "ir" and self._btn_ir.isVisibleTo(self))
        self.band_changed.emit(band)

    def _on_play_toggled(self, checked: bool):
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_slider_released(self):
        self.frame_requested.emit(self._frame_slider.value())

    def _on_jump_start(self):
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self):
        n = self._frame_slider.maximum()
        self.set_frame(n)
        self.frame_requested.emit(n)

    def _on_step_back(self):
        v = max(0, self._frame_slider.value() - 1)
        self.set_frame(v)
        self.frame_requested.emit(v)

    def _on_step_forward(self):
        v = min(self._frame_slider.maximum(), self._frame_slider.value() + 1)
        self.set_frame(v)
        self.frame_requested.emit(v)

    def _on_speed_changed(self, index: int):
        ms = self._speed_combo.itemData(index)
        if ms is not None:
            self.speed_changed.emit(ms)
