"""Named UI colors for STORM's Qt chrome.

Every interface color lives here. Stylesheets reference tokens instead of hex:

- Python strings: ``f"color: {P.TEXT_MUTED};"`` (``from ui import palette as P``)
- Large QSS blocks: ``qss("color: $text_muted; border: 1px solid $border@0.5;")``
  where ``$name`` is a token (lower-case) and ``@a`` turns it into ``rgba(..., a)``.

Data colors (SPC categories, NWS phenomena, land cover classes, deployment
ranks, sounding thresholds, matplotlib plot styling) are not theme colors and
stay next to the data they describe.

The numbered background, border and warn variants (``BG_HOVER_2`` etc.) are
near-duplicates that were hard-coded in different places, kept so the move to
tokens stayed pixel-identical. The text tokens have been folded into one scale.
"""
import re

from config import ACCENT_COLOR

# ── Backgrounds ───────────────────────────────────────────
BG_BASE         = "#0A0A0F"
BG_DEEP         = "#050508"
BG_PANEL        = "#0F0F1A"
BG_MENU         = "#0C0E16"   # used translucent, e.g. $bg_menu@0.98
BG_MENU_2       = "#0A0C14"
BG_MENU_3       = "#0C0D16"
BG_SUNKEN       = "#12121E"
BG_SUNKEN_2     = "#111120"
BG_SUNKEN_3     = "#0D0D1A"
BG_ELEVATED     = "#1A1A2E"
BG_ELEVATED_2   = "#1E1E3E"
BG_HOVER        = "#20253A"
BG_HOVER_2      = "#1E2433"
BG_HOVER_3      = "#1A1F30"
BG_HOVER_4      = "#252D42"
BG_HOVER_5      = "#252540"
BG_INFO_TINT    = "#0D1A2E"
BG_OK_TINT      = "#0D2A1A"
BG_WARN_TINT    = "#241A00"
BG_ERROR_TINT   = "#2A0D0D"

# ── Borders and dividers ──────────────────────────────────
BORDER          = "#2E2E4E"
BORDER_SUBTLE   = "#1E1E2E"
BORDER_DIM      = "#16162A"
BORDER_STRONG   = "#3C4664"
BORDER_SOFT     = "#4A536C"   # used as a translucent outline, e.g. $border_soft@0.55
BORDER_SOFT_2   = "#545E7A"
BORDER_2        = "#2A3045"
BORDER_3        = "#1E2434"
BORDER_4        = "#252535"
DIVIDER         = "#394056"
DIVIDER_2       = "#4A4A5E"
BORDER_OK       = "#1A4A2A"
BORDER_WARN     = "#3D2E00"
BORDER_ERROR    = "#4A1A1A"

# ── Text ──────────────────────────────────────────────
# Contrast against BG_BASE / BG_ELEVATED: TEXT 16.4 / 14.2, TEXT_SECONDARY
# 12.7 / 11.0, TEXT_LABEL 7.8 / 6.7, TEXT_MUTED 5.7 / 4.9. Everything a user has
# to read is at least TEXT_MUTED (WCAG AA, 4.5:1). TEXT_DISABLED (~3:1) is only
# for disabled controls and inactive rows.
TEXT            = "#E8EAF0"
TEXT_BRIGHT     = "#EFF3FF"
WHITE           = "#FFFFFF"
TEXT_SECONDARY  = "#C8D0DE"
TEXT_LABEL      = "#9BA3B2"
TEXT_MUTED      = "#808A9F"
TEXT_DISABLED   = "#5E6276"

# ── Accent and status ─────────────────────────────────────
ACCENT          = ACCENT_COLOR
ACCENT_HOVER    = "#33D9FF"
ACCENT_PRESSED  = "#009ECC"
# hover color of the old orange accent; still used by #primaryButton:hover
ACCENT_HOVER_LEGACY = "#FF8555"
INFO            = "#4A9EFF"
OK              = "#39D98A"
OK_2            = "#4ADE80"
OK_HOVER        = "#5CFFA8"
WARN            = "#FFD166"
WARN_2          = "#FFB800"
WARN_3          = "#F5C542"
WARN_4          = "#E8A020"
WARN_5          = "#FF9F43"
ERROR           = "#E53935"
ERROR_SOFT      = "#F87171"
ERROR_SOFT_2    = "#FF8F8F"

# ── Launch modes ──────────────────────────────────────────
MODE_VEHICLE    = OK
MODE_MONITOR    = WARN
MODE_VIEWER     = "#9B8FFF"
MODE_ARCHIVE    = "#FF9F1C"
MODE_ADMIN      = ACCENT

# ── Archive availability (launch calendar, case finder) ───
AVAILABLE       = "#123C50"   # a day with data; the finder's grid ramps from it to ACCENT
AVAILABLE_DIM   = "#142936"   # the same, on a day outside the month shown
AVAILABLE_EDGE  = "#28627A"
AVAILABLE_EDGE_DIM = "#24404F"


TOKENS: dict[str, str] = {
    name.lower(): value
    for name, value in dict(globals()).items()
    if name.isupper() and isinstance(value, str) and value.startswith("#")
}


def alpha(color: str, a: float) -> str:
    """``#RRGGBB`` → ``rgba(r, g, b, a)`` for translucent fills and outlines."""
    h = color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {a})"


_TOKEN_RE = re.compile(r"\$([a-z][a-z0-9_]*)(?:@([0-9]*\.?[0-9]+))?")


def qss(template: str) -> str:
    """Substitute ``$token`` / ``$token@alpha`` in a stylesheet template."""
    def _sub(m: re.Match) -> str:
        color = TOKENS[m.group(1)]
        return alpha(color, m.group(2)) if m.group(2) else color
    return _TOKEN_RE.sub(_sub, template)
