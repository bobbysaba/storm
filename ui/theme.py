# ui/theme.py
# STORM application theme — full dark, minimal chrome

from config import ACCENT_COLOR

DARK_THEME = """
/* ── Base ─────────────────────────────────────────────── */
* {
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    color: #E8EAF0;
    selection-background-color: #FF6B35;
    selection-color: #0A0A0F;
}

QMainWindow, QWidget {
    background-color: #0A0A0F;
    border: none;
}

/* ── Floating Toolbar ─────────────────────────────────── */
#floatingToolbar {
    background-color: rgba(15, 15, 26, 0.95);
    border-radius: 12px;
    border: 1px solid rgba(74, 83, 108, 0.55);
}

#floatingToolbar QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 7px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    color: #B8BFCD;
}

#floatingToolbar QToolButton:hover {
    background-color: #20253A;
    border-color: #3C4664;
    color: #EFF3FF;
}

#floatingToolbar QToolButton:checked, #floatingToolbar QToolButton:pressed {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
}

/* Slightly upscale controls when the window is wide enough. */
#floatingToolbar[wide="true"] QToolButton {
    padding: 4px 10px;
    font-size: 12px;
}

#floatingToolbar[wide="true"] QCheckBox {
    font-size: 12px;
}

#floatingToolbar[wide="true"] QComboBox#radarSiteCombo,
#floatingToolbar[wide="true"] QComboBox#radarProductCombo {
    min-height: 24px;
}

#floatingToolbar QWidget#radarDrawer,
#floatingToolbar QWidget#hazardDrawer,
#floatingToolbar QWidget#satelliteDrawer {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}

#floatingToolbar QWidget#radarDrawer > QWidget,
#floatingToolbar QWidget#hazardDrawer > QWidget,
#floatingToolbar QWidget#satelliteDrawer > QWidget {
    background: transparent;
}

#floatingToolbar QComboBox#radarSiteCombo,
#floatingToolbar QComboBox#radarProductCombo {
    background-color: rgba(32, 37, 58, 0.62);
    border: 1px solid rgba(74, 83, 108, 0.48);
}

#floatingToolbar QComboBox#radarSiteCombo:hover,
#floatingToolbar QComboBox#radarProductCombo:hover {
    border-color: rgba(120, 138, 178, 0.72);
}

#floatingToolbar QCheckBox::indicator {
    background: transparent;
    border: none;
}

/* ── Hazard Drawer ────────────────────────────────────── */
/* Overrides the global floatingToolbar QToolButton:checked orange rule so  *
 * hazard mode buttons always show blue and stay readable over the map.     */
#floatingToolbar QWidget#hazardDrawer QToolButton {
    background-color: transparent;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    color: #B8BFCD;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#hazardDrawer QToolButton:hover {
    background-color: rgba(74, 158, 255, 0.08);
    border-color: #4A9EFF;
    color: #EFF3FF;
}

#floatingToolbar QWidget#hazardDrawer QToolButton:checked {
    background-color: rgba(74, 158, 255, 0.18);
    border-color: #4A9EFF;
    color: #4A9EFF;
    font-weight: 600;
}

/* ── Outlook Panel ────────────────────────────────────── */
#outlookPanel {
    background-color: rgba(15, 15, 26, 0.95);
    border-radius: 12px;
    border: 1px solid rgba(74, 83, 108, 0.55);
}

#outlookPanelTitle {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: #4A9EFF;
    background: transparent;
}

#outlookPanelClose {
    background: transparent;
    border: none;
    color: #5A5B6A;
    font-size: 16px;
    padding: 0 2px;
}

#outlookPanelClose:hover {
    color: #E8EAF0;
}

#outlookPanelText {
    background-color: transparent;
    border: none;
    color: #C1C9D8;
    font-size: 9px;
    selection-background-color: #FF6B35;
}

/* ── Status Overlays ──────────────────────────────────── */
QWidget#statusOverlayLeft, QWidget#statusOverlayRight {
    background-color: rgba(15, 15, 26, 0.88);
    border-radius: 8px;
    border: 1px solid rgba(84, 94, 122, 0.5);
}

QWidget#statusOverlayLeft QLabel, QWidget#statusOverlayRight QLabel {
    background: transparent;
    font-size: 10px;
    letter-spacing: 0.5px;
    padding: 0;
    color: #C2C9D8;
}

/* ── Side Panel ───────────────────────────────────────── */
QDockWidget {
    background-color: #0F0F1A;
    border: none;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}

QDockWidget::title {
    background-color: #0F0F1A;
    border-bottom: 1px solid #1E1E2E;
    padding: 8px 12px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: #5A5B6A;
}

QDockWidget QWidget {
    background-color: #0F0F1A;
}

/* ── Scroll Bars ──────────────────────────────────────── */
QScrollBar:vertical {
    background-color: transparent;
    width: 6px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #2E2E4E;
    border-radius: 3px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #FF6B35;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 6px;
}

QScrollBar::handle:horizontal {
    background-color: #2E2E4E;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #FF6B35;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Labels ───────────────────────────────────────────── */
QLabel {
    background-color: transparent;
    color: #E8EAF0;
}

QLabel#sectionHeader {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    color: #5A5B6A;
    padding: 4px 0px;
}

QLabel#vehicleCallsign {
    font-size: 13px;
    font-weight: 600;
    color: #E8EAF0;
}

QLabel#vehicleMeta {
    font-size: 10px;
    color: #5A5B6A;
}

QLabel#obsValue {
    font-size: 12px;
    font-weight: 500;
    color: #FF6B35;
}

/* ── Buttons ──────────────────────────────────────────── */
QPushButton {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 11px;
    font-weight: 500;
    color: #E8EAF0;
}

QPushButton:hover {
    background-color: #1E1E3E;
    border-color: #FF6B35;
    color: #FF6B35;
}

QPushButton:pressed {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
}

QPushButton#primaryButton {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: #FF8555;
    border-color: #FF8555;
}

QPushButton#dangerButton {
    border-color: #E53935;
    color: #E53935;
}

QPushButton#dangerButton:hover {
    background-color: #E53935;
    color: #0A0A0F;
}

QComboBox#radarProductCombo, QComboBox#radarSiteCombo {
    padding: 4px 8px;
}

/* ── ComboBox ─────────────────────────────────────────── */
QComboBox {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 11px;
    color: #E8EAF0;
    min-width: 120px;
}

QComboBox:hover {
    border-color: #FF6B35;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    width: 8px;
    height: 8px;
    border-left: 2px solid #5A5B6A;
    border-bottom: 2px solid #5A5B6A;
    margin-right: 6px;
}

QComboBox QAbstractItemView {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    selection-background-color: #FF6B35;
    selection-color: #0A0A0F;
    padding: 4px;
}

/* ── Slider ───────────────────────────────────────────── */
QSlider::groove:horizontal {
    background-color: #1E1E2E;
    height: 3px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background-color: #FF6B35;
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -6px 0;
}

QSlider::sub-page:horizontal {
    background-color: #FF6B35;
    border-radius: 2px;
}

#floatingToolbar QWidget#radarDrawer QSlider,
#floatingToolbar QWidget#satelliteDrawer QSlider {
    background: transparent;
}

#floatingToolbar QWidget#radarDrawer QSlider::groove:horizontal,
#floatingToolbar QWidget#satelliteDrawer QSlider::groove:horizontal {
    background-color: rgba(184, 191, 205, 0.28);
    height: 2px;
    border-radius: 1px;
}

/* ── Radar / Satellite playback rows (small icon buttons, minimal padding) ── */
QWidget#radarPlaybackRow QToolButton,
QWidget#satPlaybackRow QToolButton {
    padding: 1px 3px;
    font-size: 13px;
}

QWidget#radarPlaybackRow QToolButton:hover,
QWidget#satPlaybackRow QToolButton:hover {
    background-color: #1A1A2E;
    border-color: #2E2E4E;
    color: #E8EAF0;
}

QWidget#radarPlaybackRow QToolButton:checked,
QWidget#radarPlaybackRow QToolButton:pressed,
QWidget#satPlaybackRow QToolButton:checked,
QWidget#satPlaybackRow QToolButton:pressed {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #0A0A0F;
}

/* ── Tooltips ─────────────────────────────────────────── */
QToolTip {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 4px;
    color: #E8EAF0;
    font-size: 10px;
    padding: 4px 8px;
}

/* ── Toolbar checkboxes ──────────────────────────────── */
#floatingToolbar QCheckBox {
    background: transparent;
    border: none;
    color: #B8BFCD;
    font-size: 11px;
    spacing: 5px;
    padding: 0;
}

#floatingToolbar QWidget#radarDrawer QCheckBox,
#floatingToolbar QWidget#hazardDrawer QCheckBox,
#floatingToolbar QWidget#satelliteDrawer QCheckBox {
    background: transparent;
}
#floatingToolbar QCheckBox::indicator {
    width: 12px;
    height: 12px;
    border: none;
    background: transparent;
    image: url(static/indicator_off.svg);
}
#floatingToolbar QCheckBox::indicator:checked {
    image: url(static/indicator_on.svg);
}

/* ── Separators ───────────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    color: #1E1E2E;
}

/* ── Annotation Dialog ────────────────────────────────── */
QDialog#annotationDialog {
    background-color: #0A0A0F;
}

QDialog#annotationDialog QLabel {
    background-color: transparent;
}

QDialog#annotationDialog QLineEdit {
    background-color: #1A1A2E;
    border: 1px solid #2E2E4E;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
    color: #E8EAF0;
}

QDialog#annotationDialog QLineEdit:focus {
    border-color: #FF6B35;
}
""".replace("#FF6B35", ACCENT_COLOR)

# Accent color for use in Python code — reads from config.toml [ui] accent_color
ACCENT = ACCENT_COLOR
ACCENT_ORANGE = ACCENT  # backwards-compat alias
ACCENT_BLUE   = "#4A9EFF"
ACCENT_GREEN  = "#39D98A"
ACCENT_RED    = "#E53935"
ACCENT_YELLOW = "#FFD166"

BG_BASE       = "#0A0A0F"
BG_PANEL      = "#0F0F1A"
BG_ELEVATED   = "#1A1A2E"
BORDER_COLOR  = "#1E1E2E"
TEXT_PRIMARY  = "#E8EAF0"
TEXT_MUTED    = "#5A5B6A"
