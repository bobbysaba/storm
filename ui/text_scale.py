"""UI text scale: 100 / 115 / 130 % (MAP → TEXT, saved in QSettings).

Font sizes live in stylesheets, both the theme and many inline ``setStyleSheet``
calls, so scaling works at that level: ``install`` wraps ``QWidget.setStyleSheet``
once at startup, and every sheet that sets ``font-size: Npx`` is applied with the
size scaled. The unscaled sheet is kept on the widget (``_BASE_PROP``), so
``set_percent`` can re-apply every sheet live. Widgets without a stylesheet font
size follow the application font, which is scaled too.
"""
import re

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QWidget

SCALES = (100, 115, 130)

_KEY = "ui/text_scale"
_BASE_PROP = "textScaleBaseSheet"
_FONT_SIZE_RE = re.compile(r"(font-size:\s*)(\d+(?:\.\d+)?)px")

_percent = 100
_qt_set_style_sheet = None
_base_app_point_size: float | None = None


def scale_sheet(sheet: str, percent: int) -> str:
    """Return ``sheet`` with every ``font-size: Npx`` scaled to ``percent``."""
    if percent == 100:
        return sheet
    return _FONT_SIZE_RE.sub(
        lambda m: f"{m.group(1)}{max(1, round(float(m.group(2)) * percent / 100))}px", sheet
    )


def percent() -> int:
    return _percent


def _settings(settings: QSettings | None) -> QSettings:
    return settings if settings is not None else QSettings("NSSL", "STORM")


def saved_percent(settings: QSettings | None = None) -> int:
    try:
        value = int(_settings(settings).value(_KEY, 100))
    except (TypeError, ValueError):
        return 100
    return value if value in SCALES else 100


def _set_style_sheet(widget: QWidget, sheet: str) -> None:
    if sheet and "font-size" in sheet:
        widget.setProperty(_BASE_PROP, sheet)
        sheet = scale_sheet(sheet, _percent)
    elif widget.property(_BASE_PROP) is not None:
        widget.setProperty(_BASE_PROP, None)
    _qt_set_style_sheet(widget, sheet)


def _apply_app_font(app: QApplication) -> None:
    font = app.font()
    font.setPointSizeF(_base_app_point_size * _percent / 100)
    app.setFont(font)


def install(app: QApplication, settings: QSettings | None = None) -> None:
    """Hook stylesheet scaling and apply the saved scale. Call before building any window."""
    global _qt_set_style_sheet, _base_app_point_size, _percent
    if _qt_set_style_sheet is None:
        _qt_set_style_sheet = QWidget.setStyleSheet
        QWidget.setStyleSheet = _set_style_sheet
        _base_app_point_size = app.font().pointSizeF()
    _percent = saved_percent(settings)
    _apply_app_font(app)


def set_percent(value: int, settings: QSettings | None = None) -> bool:
    """Change the scale, save it, and re-apply every live stylesheet. Returns True if it changed."""
    global _percent
    if value not in SCALES or value == _percent:
        return False
    _percent = value
    _settings(settings).setValue(_KEY, value)
    app = QApplication.instance()
    if app is None or _qt_set_style_sheet is None:
        return True
    _apply_app_font(app)
    for widget in app.allWidgets():
        base = widget.property(_BASE_PROP)
        if base:
            _qt_set_style_sheet(widget, scale_sheet(base, value))
    return True
