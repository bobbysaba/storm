
import os
import tempfile

from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QPainter, QPixmap


# down-arrow SVG written to a temp file so QSS can reference it via url().
_COMBO_DOWN_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>"
    "<path fill='none' stroke='#8E97AB' stroke-width='2' stroke-linecap='round' "
    "stroke-linejoin='round' d='M4 6l4 4 4-4'/></svg>"
)
_COMBO_DOWN_PATH = os.path.join(tempfile.gettempdir(), "storm_combo_down.svg").replace("\\", "/")
try:
    with open(_COMBO_DOWN_PATH, "w") as _f:
        _f.write(_COMBO_DOWN_SVG)
except Exception:
    _COMBO_DOWN_PATH = ""

# svg icon definitions for the vehicle icon picker (same shapes as the map markers).
_ICON_SVGS = {
    "car":     '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="3" y="9" width="18" height="8" rx="2" fill="{c}"/><rect x="6" y="5" width="12" height="7" rx="2" fill="{c}" opacity="0.85"/><circle cx="7.5" cy="18" r="2.2" fill="{c}"/><circle cx="16.5" cy="18" r="2.2" fill="{c}"/></svg>',
    "drone":   '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><line x1="7" y1="7" x2="17" y2="17" stroke="{c}" stroke-width="2"/><line x1="17" y1="7" x2="7" y2="17" stroke="{c}" stroke-width="2"/><rect x="10" y="10" width="4" height="4" rx="1" fill="{c}"/><circle cx="5.5" cy="5.5" r="2.5" fill="{c}" opacity="0.85"/><circle cx="18.5" cy="5.5" r="2.5" fill="{c}" opacity="0.85"/><circle cx="5.5" cy="18.5" r="2.5" fill="{c}" opacity="0.85"/><circle cx="18.5" cy="18.5" r="2.5" fill="{c}" opacity="0.85"/></svg>',
    "mesonet": '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="11" y="12" width="2" height="10" rx="1" fill="{c}"/><line x1="3" y1="10" x2="22" y2="10" stroke="{c}" stroke-width="1.5"/><circle cx="12" cy="10" r="1.5" fill="{c}"/><line x1="3" y1="5" x2="3" y2="15" stroke="{c}" stroke-width="3" stroke-linecap="round"/><polygon points="17,10 22,10 22,4" fill="{c}"/></svg>',
    "lidar":   '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="7" y="14" width="10" height="7" fill="{c}"/><line x1="7" y1="14" x2="2" y2="14" stroke="{c}" stroke-width="4" stroke-linecap="square"/><line x1="17" y1="14" x2="22" y2="14" stroke="{c}" stroke-width="4" stroke-linecap="square"/><circle cx="12" cy="11" r="2.5" fill="{c}"/><rect x="11" y="21" width="2" height="3" fill="{c}"/></svg>',
    "radar":   '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="9" r="7" fill="{c}"/><rect x="11" y="16" width="2" height="3" fill="{c}"/><rect x="7" y="19" width="10" height="2" rx="1" fill="{c}"/></svg>',
    "hailcam": '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M21.5,12 L18.1,15.5 L16.8,20.2 L12,19 L7.2,20.2 L5.9,15.5 L2.5,12 L5.9,8.5 L7.2,3.8 L12,5 L16.8,3.8 L18.1,8.5 Z" fill="{c}"/></svg>',
}


def _svg_pixmap(key: str, color: str, size: int = 28) -> QPixmap:
    """Render one of the _ICON_SVGS to a QPixmap at *size* × *size* pixels."""
    try:
        from PyQt6.QtSvg import QSvgRenderer
        svg_bytes = QByteArray(_ICON_SVGS[key].replace("{c}", color).encode())
        renderer = QSvgRenderer(svg_bytes)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return pixmap
    except Exception:
        return QPixmap()


def combo_down_arrow_qss() -> str:
    if not _COMBO_DOWN_PATH:
        return ""
    return (
        f'QComboBox::down-arrow {{'
        f'  image: url("{_COMBO_DOWN_PATH}");'
        f'  width: 10px; height: 10px;'
        f'}}'
    )
