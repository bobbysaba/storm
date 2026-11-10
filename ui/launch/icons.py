
import os
import tempfile

from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from ui import palette as P


# down-arrow SVG written to a temp file so QSS can reference it via url().
_COMBO_DOWN_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>"
    f"<path fill='none' stroke='{P.TEXT_LABEL}' stroke-width='2' stroke-linecap='round' "
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


# line icons for the floating toolbar buttons (24×24 viewBox, stroked).
_TOOLBAR_SVGS = {
    "radar":     '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12 L18.4 5.6"/>',
    "satellite": '<rect x="9" y="9" width="6" height="6" rx="1" transform="rotate(45 12 12)"/><path d="M8.5 8.5 L4.5 4.5 M15.5 15.5 L19.5 19.5 M3 7 L7 3 M17 21 L21 17"/>',
    "hazards":   '<path d="M12 3.5 L21 19.5 H3 Z"/><path d="M12 10 V14 M12 16.6 V16.8"/>',
    "surface":   '<path d="M10 14.5 V5 a2 2 0 0 1 4 0 V14.5 a4 4 0 1 1 -4 0 Z"/><path d="M12 9 V16"/>',
    "meso":      '<path d="M3 8 C7 4 10 12 14 8 S19 6 21 8"/><path d="M3 13 C7 9 10 17 14 13 S19 11 21 13"/><path d="M3 18 C7 14 10 22 14 18 S19 16 21 18"/>',
    "sfcoa":     '<ellipse cx="12" cy="12" rx="9" ry="7"/><ellipse cx="12" cy="12" rx="5" ry="3.5"/><path d="M12 11.5 V12.5"/>',
    "sounding":  '<path d="M12 3 a5 5.5 0 0 1 5 5.5 c0 3.5 -3 6 -5 6.5 c-2 -0.5 -5 -3 -5 -6.5 A5 5.5 0 0 1 12 3 Z"/><path d="M12 15 C11 17 13 19 12 21"/>',
    "vehicles":  '<path d="M4 15 V12 L6.5 7 H17.5 L20 12 V15 Z"/><circle cx="8" cy="17" r="1.8"/><circle cx="16" cy="17" r="1.8"/>',
    "prev_locs": '<path d="M12 21 C8 16 5.5 13 5.5 9.5 a6.5 6.5 0 0 1 13 0 C18.5 13 16 16 12 21 Z"/><circle cx="12" cy="9.5" r="2.2"/>',
    "annotate":  '<path d="M4 20 L5 15.5 L15.5 5 a2 2 0 0 1 3 0 L19 5.5 a2 2 0 0 1 0 3 L8.5 19 Z"/><path d="M13.5 7 L17 10.5"/>',
    "map":       '<path d="M3 6 L9 4 L15 6 L21 4 V18 L15 20 L9 18 L3 20 Z"/><path d="M9 4 V18 M15 6 V20"/>',
    "more":      '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
}


def _toolbar_pixmap(key: str, color: str, size: int) -> QPixmap:
    from PyQt6.QtSvg import QSvgRenderer
    svg = (
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none" '
        f'stroke="{color}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        f'{_TOOLBAR_SVGS[key]}</svg>'
    )
    # rendered at 2× so the icon stays crisp on HiDPI screens
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return pixmap


def toolbar_icon(key: str, size: int = 14) -> QIcon:
    """Toolbar button icon: secondary text color, dark when the button is checked."""
    icon = QIcon()
    icon.addPixmap(_toolbar_pixmap(key, P.TEXT_SECONDARY, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_toolbar_pixmap(key, P.TEXT_BRIGHT, size), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(_toolbar_pixmap(key, P.BG_BASE, size), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(_toolbar_pixmap(key, P.BG_BASE, size), QIcon.Mode.Active, QIcon.State.On)
    return icon
