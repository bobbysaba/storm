
import math

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel

from config import ACCENT_COLOR as _STORM_CYAN


_FIG_BG    = "#0a0a0f"
_AX_BG     = "#0f0f1a"
_BORDER    = "#2a2a40"
_ACCENT    = "#ff6b35"
_TEXT      = "#e8eaf0"
_MUTED     = "#666688"
_HDR_CLR   = "#b8b8d8"   # bright table row/column headers

_TEMP_CLR   = "#ff6b6b"
_DEWP_CLR   = "#3ddc84"
_BARB_CLR   = "#aaaacc"
_PARCEL_CLR  = "#ffffff"
_VTEMP_CLR   = "#ffaa88"
_VPARCEL_CLR = "#ff3366"    # virtual-temperature parcel trace
_EIL_CLR    = "#00e676"
_RM_CLR     = "#ff6b6b"
_LM_CLR     = "#4fc3f7"
_OBS_MOTION_CLR = "#FFD400"   # observed storm motion (archive storm track)

# per-source identity for comparison overlay
_SOURCE_COLORS = {"obs": "#ff6b6b", "hrrr": "#4fc3f7", "provider": "#81c784",
                  "retrieval": "#ffb74d", "uas": "#ba68c8"}
_SOURCE_LABELS = {"obs": "OBS",     "hrrr": "HRRR",    "provider": "PROVIDER",
                  "retrieval": "RETRIEVAL", "uas": "UAS"}
_SOURCE_LS     = {"obs": "-",       "hrrr": "--",       "provider": ":",
                  "retrieval": "-.", "uas": (0, (5, 1, 1, 1))}
_SOURCE_GLYPHS = {"obs": "━━",      "hrrr": "╌╌",       "provider": "┈┈",
                  "retrieval": "━┈", "uas": "━╌"}

# the sources STORM compares against, in pill order; sounding [[sources]]
# and code-provider soundings are added by register_source
COMPARISON_SOURCES = ["obs", "hrrr", "provider"]
_EXTRA_STYLES = (("#f06292", "-", "━━"), ("#4db6ac", "--", "╌╌"), ("#fff176", ":", "┈┈"),
                 ("#9575cd", "-.", "━┈"), ("#a1887f", (0, (5, 1, 1, 1)), "━╌"))


def set_provider_label(label: str) -> None:
    """The provider soundings' pill and legend label (its LABEL: "NSSL")."""
    _SOURCE_LABELS["provider"] = label


def provider_label() -> str:
    return _SOURCE_LABELS["provider"]


def set_source_labels(labels: dict) -> None:
    """A provider's pill and legend labels for its retrieval and uas
    soundings (its archive sounding fetcher's pill_labels: NSSL's
    {"retrieval": "TROPoe", "uas": "COPTER"})."""
    for key in ("retrieval", "uas"):
        if labels.get(key):
            _SOURCE_LABELS[key] = str(labels[key])


def register_source(key: str, label: str) -> None:
    """Give a sounding source (data/sources.py sounding_key) a pill, a color
    and a line style, the next of _EXTRA_STYLES."""
    if key in _SOURCE_COLORS:
        return
    color, ls, glyph = _EXTRA_STYLES[(len(COMPARISON_SOURCES) - 3) % len(_EXTRA_STYLES)]
    _SOURCE_COLORS[key], _SOURCE_LS[key], _SOURCE_GLYPHS[key] = color, ls, glyph
    _SOURCE_LABELS[key] = label
    COMPARISON_SOURCES.append(key)

_HODO_LAYERS = [
    ("#ff8585", 0,  3),
    ("#ffe45c", 3,  6),
    ("#70d7ff", 6,  9),
    ("#c0c0d6", 9, 99),
]

_SLIDER_QSS = f"""
QSlider {{ background-color: transparent; }}
QSlider::groove:horizontal {{
    background-color: #1a1a2e; height: 4px; border-radius: 2px; margin: 0;
}}
QSlider::sub-page:horizontal {{
    background-color: {_STORM_CYAN}; height: 4px; border-radius: 2px; margin: 0;
}}
QSlider::handle:horizontal {{
    background-color: {_STORM_CYAN}; border: 2px solid {_FIG_BG};
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
"""

_EXPORT_BTN_QSS = f"""
QToolButton {{
    background-color: #151522;
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QToolButton:hover {{
    border-color: {_STORM_CYAN};
    color: {_STORM_CYAN};
}}
QToolButton:pressed {{
    background-color: #202033;
}}
"""


def _force_bg(widget):
    bg  = QColor(_FIG_BG)
    txt = QColor(_TEXT)
    pal = widget.palette()
    for role in (
        QPalette.ColorRole.Window,    QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button,
        QPalette.ColorRole.Midlight,  QPalette.ColorRole.Light,
        QPalette.ColorRole.Mid,       QPalette.ColorRole.Dark,
        QPalette.ColorRole.Shadow,
    ):
        pal.setColor(role, bg)
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Text,       QPalette.ColorRole.BrightText,
    ):
        pal.setColor(role, txt)
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)


def _lbl(text, color=_TEXT, size=10, bold=False,
         align=Qt.AlignmentFlag.AlignLeft):
    w = QLabel(text)
    weight = "bold" if bold else "normal"
    w.setStyleSheet(
        f"background-color: transparent; color: {color}; "
        f"font-size: {size}px; font-weight: {weight};"
    )
    w.setAlignment(align)
    return w


def _threshold_color(thresholds: dict[str, list], key: str, value) -> str | None:
    """The colour of the highest of *thresholds[key]* that *value* reaches,
    or None (no table for *key*, or no finite value)."""
    steps = thresholds.get(key)
    if not steps or value is None or not math.isfinite(value):
        return None
    color = steps[0][1]
    for threshold, c in steps:
        if value >= threshold:
            color = c
    return color
