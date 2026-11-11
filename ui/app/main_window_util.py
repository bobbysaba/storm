"""Module-level constants and helpers shared by MainWindow and its mixins."""

import os
import runtime_flags
from datetime import datetime
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from ui import palette as P

SCAN_MOVE_THRESHOLD_KM = 0.1
SCAN_MOVE_FIXES_TO_STOP = 3
LIVE_RADAR_MAX_AGE_MINUTES = 45
VEHICLE_PANEL_WIDTH = 340
NLCD_TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "storm_nlcd.mbtiles")
)
SATELLITE_TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tiles", "satellite.mbtiles")
)


def _overlay_time_label(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).strftime("%H:%MZ")
    except ValueError:
        if "T" in text:
            return text.split("T", 1)[1].replace(":00Z", "Z")[:6]
    return text


def _make_camera_icon(size: int = 18, color: str = P.TEXT_SECONDARY) -> QIcon:
    """Return a minimal camera QIcon rendered from inline SVG."""
    svg = (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M4 8 H8 L9.5 6 H14.5 L16 8 H20 V18 H4 Z"'
        ' fill="none" stroke="{c}" stroke-width="1.8"'
        ' stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="12" cy="13" r="3.2"'
        ' fill="none" stroke="{c}" stroke-width="1.8"/>'
        '</svg>'
    ).format(c=color)
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    return QIcon(px)


def _loop_interval_ms(ms: int) -> int:
    """Loop frame interval, slowed to LITE_MIN_LOOP_MS in lite mode."""
    if runtime_flags.FLAGS.lite_mode:
        return max(int(ms), runtime_flags.LITE_MIN_LOOP_MS)
    return int(ms)


def _coords_close(a, b, tol: float = 1e-4) -> bool:
    """Return True if two [lat, lon] points are within ~10 m of each other."""
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _clear_layout(layout):
    """Remove and schedule deletion of all widgets in a layout."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


# ANNOTATE drawer tools; switching between them keeps the drawer's tool highlighted
_ANNOTATE_KINDS = ("annotation", "drawing", "storm_motion_fix")

_SOUNDING_PROMPTS = {
    "hrrr": "HRRR sounding — click map",
    "obs": "observed sounding — click a station",
    "nssl": "NSSL soundings",
}


class _NetChecker(QObject):
    """Worker that checks internet connectivity from a background thread."""
    result_ready = pyqtSignal(str)   # "ok", "slow", or "none"

    def check(self):
        import socket, time
        try:
            t0 = time.monotonic()
            s  = socket.create_connection(("1.1.1.1", 53), timeout=2)
            s.close()
            self.result_ready.emit("slow" if time.monotonic() - t0 > 1.0 else "ok")
        except OSError:
            self.result_ready.emit("none")
