# ui/radar_controls.py
# radar site selector, product toggle, and playback controls for the main toolbar.

import json
import math
import re
import threading
from urllib.request import Request, urlopen

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QToolButton, QLabel,
    QCheckBox, QInputDialog, QSlider, QFrame, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer, Qt


# ── NEXRAD Sites ─────────────────────────────────────────────────────────────
# format: (site_id, display_name, latitude, longitude)
NEXRAD_SITES = [
    ("KTLX", "Oklahoma City, OK", 35.333, -97.278),
    ("KOUN", "Norman, OK (Research)", 35.236, -97.463),
    ("KVNX", "Enid/Vance, OK", 36.741, -98.127),
    ("KFDR", "Frederick, OK", 34.362, -98.976),
    ("KINX", "Tulsa, OK", 36.175, -95.565),
    ("KSRX", "Ft. Smith, AR", 35.291, -94.362),
    ("KDDC", "Dodge City, KS", 37.760, -99.969),
    ("KICT", "Wichita, KS", 37.655, -97.443),
    ("KTWX", "Topeka, KS", 38.996, -96.232),
    ("KEAX", "Kansas City, MO", 38.810, -94.264),
    ("KSGF", "Springfield, MO", 37.235, -93.401),
    ("KLZK", "Little Rock, AR", 34.836, -92.262),
    ("KAMA", "Amarillo, TX", 35.233, -101.709),
    ("KFWS", "Dallas/Ft. Worth, TX", 32.573, -97.303),
    ("KSHV", "Shreveport, LA", 32.451, -93.841),
    ("KLBB", "Lubbock, TX", 33.654, -101.814),
    ("KMAF", "Midland, TX", 31.943, -102.189),
    ("KABR", "Aberdeen, SD", 45.455, -98.413),
    ("KUDX", "Rapid City, SD", 44.125, -102.830),
    ("KLNX", "North Platte, NE", 41.958, -100.576),
    ("KOAX", "Omaha, NE", 41.320, -96.366),
    ("KUEX", "Grand Island, NE", 40.321, -98.442),
    ("KGLD", "Goodland, KS", 39.366, -101.700),
    ("KPUX", "Pueblo, CO", 38.460, -104.181),
    ("KFTG", "Denver, CO", 39.786, -104.545),
    ("KCYS", "Cheyenne, WY", 41.152, -104.806),
    ("KRIW", "Riverton, WY", 43.066, -108.477),
    ("KBIS", "Bismarck, ND", 46.771, -100.761),
    ("KMBX", "Minot, ND", 48.393, -100.864),
    ("KFSD", "Sioux Falls, SD", 43.588, -96.728),
    ("KDVN", "Davenport, IA", 41.611, -90.581),
    ("KDMX", "Des Moines, IA", 41.731, -93.723),
]

PRODUCTS = [("N0Q", "REFLECTIVITY"), ("N0U", "VELOCITY")]
DEFAULT_REF_LAT = 35.22
DEFAULT_REF_LON = -97.44
DEFAULT_NEAREST_COUNT = 5
OTHER_SITE_VALUE = "__OTHER__"


class RadarControls(QWidget):
    """
    Toolbar widget containing:
      - RADAR toggle button — slides the two-row control drawer in/out
      - collapsible two-row drawer:
          Row 1: site selector | product selector | show data checkbox | stretch
          Row 2: ⏮ ⏪ ▶/⏸ ⏩ ⏭  +  expanding timeline slider  +  time label

    Signals:
        radar_toggled(bool)    — data fetch enabled/disabled (from show data checkbox)
        site_changed(str)      — new site ID selected
        product_changed(str)   — selected product code (e.g. "N0Q")
        fetch_requested()      — trigger immediate fetch
        frame_requested(int)   — user selected a specific cache frame index
        loop_toggled(bool)     — loop playback started/stopped
    """

    radar_toggled   = pyqtSignal(bool)
    site_changed    = pyqtSignal(str)
    product_changed = pyqtSignal(str)
    fetch_requested = pyqtSignal()
    frame_requested = pyqtSignal(int)
    loop_toggled    = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radar_on           = False
        self._product            = "N0Q"
        self._updating_site_list = False
        self._manual_site        = ""
        self._all_sites          = list(NEXRAD_SITES)
        self._animation          = None   # hold ref to prevent GC during animation
        self._expanded_height    = 0
        self._setup_ui()
        self.set_reference_location(DEFAULT_REF_LAT, DEFAULT_REF_LON)

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        # Starts collapsed — height animates open as a dropdown pill below toolbar.
        self.setMaximumHeight(0)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("radarDrawer")
        drawer_layout = QVBoxLayout(self._drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(4)

        # ── Row 1: site | product | show data checkbox ────────────────────
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._site_combo = QComboBox()
        self._site_combo.setFixedHeight(22)
        self._site_combo.setMinimumWidth(220)
        self._site_combo.setObjectName("radarSiteCombo")
        self._site_combo.currentIndexChanged.connect(self._on_site_changed)
        r1.addWidget(self._site_combo)

        self._product_combo = QComboBox()
        self._product_combo.setFixedHeight(22)
        self._product_combo.setMinimumWidth(128)
        self._product_combo.setObjectName("radarProductCombo")
        for code, label in PRODUCTS:
            self._product_combo.addItem(label, userData=code)
        self._product_combo.currentIndexChanged.connect(self._on_product_changed)
        r1.addWidget(self._product_combo)

        # checkbox sits immediately right of product selector
        self._chk_show_data = QCheckBox("show data")
        self._chk_show_data.setChecked(True)
        self._chk_show_data.setFixedHeight(22)
        self._chk_show_data.setToolTip("enable or disable radar data fetch and display")
        self._chk_show_data.toggled.connect(self._on_data_enabled_toggled)
        r1.addWidget(self._chk_show_data)

        drawer_layout.addWidget(row1)

        # ── Row 2: playback buttons + expanding slider + time label ───────
        row2 = QWidget()
        row2.setObjectName("radarPlaybackRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        self._btn_jump_start = QToolButton()
        self._btn_jump_start.setText("⏮")
        self._btn_jump_start.setFixedSize(32, 26)
        self._btn_jump_start.setEnabled(False)
        self._btn_jump_start.setToolTip("Oldest frame")
        self._btn_jump_start.clicked.connect(self._on_jump_start)
        r2.addWidget(self._btn_jump_start)

        self._btn_back = QToolButton()
        self._btn_back.setText("⏪")
        self._btn_back.setFixedSize(32, 26)
        self._btn_back.setEnabled(False)
        self._btn_back.setToolTip("Step back one frame")
        self._btn_back.clicked.connect(self._on_step_back)
        r2.addWidget(self._btn_back)

        self._btn_play = QToolButton()
        self._btn_play.setText("▶")
        self._btn_play.setCheckable(True)
        self._btn_play.setFixedSize(32, 26)
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip("Play / Pause loop")
        self._btn_play.toggled.connect(self._on_play_toggled)
        r2.addWidget(self._btn_play)

        self._btn_fwd = QToolButton()
        self._btn_fwd.setText("⏩")
        self._btn_fwd.setFixedSize(32, 26)
        self._btn_fwd.setEnabled(False)
        self._btn_fwd.setToolTip("Step forward one frame")
        self._btn_fwd.clicked.connect(self._on_step_forward)
        r2.addWidget(self._btn_fwd)

        self._btn_jump_end = QToolButton()
        self._btn_jump_end.setText("⏭")
        self._btn_jump_end.setFixedSize(32, 26)
        self._btn_jump_end.setEnabled(False)
        self._btn_jump_end.setToolTip("Latest (live)")
        self._btn_jump_end.clicked.connect(self._on_jump_end)
        r2.addWidget(self._btn_jump_end)

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.setFixedHeight(26)
        self._frame_slider.setMinimumWidth(80)   # expand to fill available space
        self._frame_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._frame_slider.setEnabled(False)
        # use sliderReleased instead of valueChanged — avoids re-render on every drag pixel
        self._frame_slider.sliderReleased.connect(self._on_slider_released)
        r2.addWidget(self._frame_slider)

        # time label sits right of slider so it reads: [slider] [23:45Z]
        self._frame_time_label = QLabel("--:--Z")
        self._frame_time_label.setObjectName("radarTimeLabel")
        self._frame_time_label.setFixedHeight(26)
        self._frame_time_label.setMinimumWidth(52)
        r2.addWidget(self._frame_time_label)

        drawer_layout.addWidget(row2)

        layout.addWidget(self._drawer)

        # measure natural height after layout settles, before first collapse
        QTimer.singleShot(0, self._measure_expanded_height)

    # ── Public API ────────────────────────────────────────────────────────────

    def current_site(self) -> str:
        site_id = self._site_combo.currentData()
        if site_id and site_id != OTHER_SITE_VALUE:
            return site_id
        if self._manual_site:
            return self._manual_site
        return "KTLX"

    def current_product(self) -> str:
        return self._product

    def set_scan_time(self, time_str: str):
        # update the time label next to the slider
        self._frame_time_label.setText(time_str)

    def set_radar_active(self, active: bool):
        """programmatically open/close the radar drawer (visual only)."""
        if active != self._radar_on:
            self.toggle_drawer(active)

    def set_reference_location(self, lat: float, lon: float, top_n: int = DEFAULT_NEAREST_COUNT):
        """update the dropdown list with nearest N radar sites for a location."""
        current = self.current_site()
        ranked = sorted(
            self._all_sites,
            key=lambda site: _haversine_km(lat, lon, site[2], site[3])
        )
        nearest = ranked[:top_n]
        self._set_site_items(nearest, preserve=current)

    def set_manual_site(self, site_id: str):
        """set a custom site code selected via the OTHER flow."""
        normalized = _normalize_site(site_id)
        if not normalized:
            return
        self._manual_site = normalized
        self._set_other_label(normalized)
        other_idx = self._site_combo.findData(OTHER_SITE_VALUE)
        if other_idx >= 0:
            self._site_combo.setCurrentIndex(other_idx)
        self.site_changed.emit(normalized)
        self.fetch_requested.emit()
        # Resolve the city name in the background and update the label.
        threading.Thread(target=self._lookup_site_name, args=(normalized,), daemon=True).start()

    def _lookup_site_name(self, site_id: str):
        """Fetch the human-readable name for a NEXRAD site from the NWS API."""
        try:
            url = f"https://api.weather.gov/radar/stations/{site_id}"
            req = Request(url, headers={
                "User-Agent": "STORM/1.0 (contact: support)",
                "Accept": "application/geo+json",
            })
            with urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            name = data.get("properties", {}).get("name", "")
            label = f"{site_id} - {name}" if name else site_id
        except Exception:
            label = site_id
        # Qt UI updates must happen on the main thread.
        QTimer.singleShot(0, lambda: self._set_other_label(label))

    def set_cache_size(self, n: int):
        """update slider range as scan cache grows; stay at live if already there."""
        was_live = self.is_at_latest_frame()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, max(0, n - 1))
        if was_live:
            self._frame_slider.setValue(n - 1)
        self._frame_slider.blockSignals(False)
        has_history = n > 1
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(has_history)

    def set_frame(self, idx: int):
        """move slider to index without emitting frame_requested."""
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)

    def current_frame(self) -> int:
        return self._frame_slider.value()

    def is_at_latest_frame(self) -> bool:
        return self._frame_slider.value() >= self._frame_slider.maximum()

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def stop_loop(self):
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)   # triggers _on_play_toggled(False)

    def reset_cache_ui(self):
        """reset playback controls when site or product changes."""
        self.stop_loop()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.blockSignals(False)
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(False)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _measure_expanded_height(self):
        """store the natural height of RadarControls before it was ever collapsed."""
        self.setMaximumHeight(16777215)
        self._expanded_height = self.sizeHint().height()
        self.setMaximumHeight(0)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def toggle_drawer(self, checked: bool):
        """animate RadarControls open or closed — called by the toolbar RADAR button."""
        self._radar_on = checked

        target = self._expanded_height if checked else 0
        if checked:
            current = self.maximumHeight()
            if target == 0:
                # expanded height wasn't measured yet — measure now
                self.setMaximumHeight(16777215)
                target = self.sizeHint().height()
                self.setMaximumHeight(current)
        else:
            # after opening, maximumHeight is 16777215 — animate from actual pixel
            # height so the slide-back starts immediately instead of snapping
            current = self.height()
            self.setMaximumHeight(current)

        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(180)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        if checked:
            anim.finished.connect(lambda: self.setMaximumHeight(self._expanded_height))
        anim.start()
        self._animation = anim   # keep ref alive for duration of animation

    def _on_data_enabled_toggled(self, checked: bool):
        """show data checkbox — controls whether data is actually fetched."""
        self.radar_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_play_toggled(self, checked: bool):
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_site_changed(self, index: int):
        if self._updating_site_list:
            return
        site_id = self._site_combo.itemData(index)
        if site_id and site_id != OTHER_SITE_VALUE:
            self.site_changed.emit(site_id)
            self.fetch_requested.emit()
            return
        if site_id == OTHER_SITE_VALUE:
            self._prompt_for_manual_site()

    def _on_product_changed(self, index: int):
        product = self._product_combo.itemData(index)
        if not product:
            return
        self._product = product
        self.product_changed.emit(product)

    def _on_slider_released(self):
        # emit frame_requested only when user releases the slider handle
        # (avoids expensive PNG renders on every pixel of drag movement)
        self.frame_requested.emit(self._frame_slider.value())

    def _on_jump_start(self):
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self):
        n = self._frame_slider.maximum()
        self.set_frame(n)
        self.frame_requested.emit(n)

    def _on_step_back(self):
        # use set_frame + manual emit so we bypass the sliderReleased path consistently
        new_val = max(0, self._frame_slider.value() - 1)
        self.set_frame(new_val)
        self.frame_requested.emit(new_val)

    def _on_step_forward(self):
        new_val = min(self._frame_slider.maximum(), self._frame_slider.value() + 1)
        self.set_frame(new_val)
        self.frame_requested.emit(new_val)

    def _prompt_for_manual_site(self):
        current = self._manual_site or "KTLX"
        text, ok = QInputDialog.getText(
            self,
            "Custom Radar Site",
            "Enter 4-letter radar code (e.g., KTLX):",
            text=current
        )
        if not ok:
            return
        manual = _normalize_site(text)
        if manual:
            self.set_manual_site(manual)

    def _set_site_items(self, sites: list[tuple[str, str, float, float]], preserve: str = ""):
        self._updating_site_list = True
        try:
            self._site_combo.clear()
            for site_id, name, _, _ in sites:
                self._site_combo.addItem(f"{site_id} - {name}", userData=site_id)
            self._site_combo.addItem("OTHER...", userData=OTHER_SITE_VALUE)

            desired = _normalize_site(preserve) or "KTLX"
            idx = self._site_combo.findData(desired)
            if idx >= 0:
                self._site_combo.setCurrentIndex(idx)
            elif self._manual_site:
                self._set_other_label(self._manual_site)
                other_idx = self._site_combo.findData(OTHER_SITE_VALUE)
                self._site_combo.setCurrentIndex(other_idx if other_idx >= 0 else 0)
            else:
                self._site_combo.setCurrentIndex(0)
        finally:
            self._updating_site_list = False

    def _set_other_label(self, manual_site: str):
        other_idx = self._site_combo.findData(OTHER_SITE_VALUE)
        if other_idx >= 0:
            self._site_combo.setItemText(other_idx, f"OTHER ({manual_site})")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_site(site_id: str) -> str:
    text = (site_id or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\b[A-Z][A-Z0-9]{3}\b", text)
    return match.group(0) if match else ""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """great-circle distance in km — used to sort radar sites by proximity."""
    r = 6371.0
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c
