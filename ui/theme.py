from ui.palette import qss, ACCENT, BG_BASE, BG_ELEVATED, TEXT_MUTED  # noqa: F401  (re-exported)

DARK_THEME = qss("""
/* ── Base ─────────────────────────────────────────────── */
* {
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    color: $text;
    selection-background-color: $accent;
    selection-color: $bg_base;
}

QMainWindow, QWidget {
    background-color: $bg_base;
    border: none;
}

/* ── Floating Toolbar ─────────────────────────────────── */
#floatingToolbar {
    background-color: $bg_panel@0.95;
    border-radius: 12px;
    border: 1px solid $border_soft@0.55;
}

#floatingToolbar QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 7px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    color: $text_secondary;
}

/* Icon + label (responsive toolbar's full tier): Qt leaves ~9px of slack after
   the label, so shift padding left to center the icon and label in the button. */
#floatingToolbar QToolButton[labelled="true"] {
    padding: 3px 3px 3px 9px;
}

#floatingToolbar QToolButton:hover {
    background-color: $bg_hover;
    border-color: $border_strong;
    color: $text_bright;
}

#floatingToolbar QToolButton:checked, #floatingToolbar QToolButton:pressed {
    background-color: $accent;
    border-color: $accent;
    color: $bg_base;
}

/* Loop timeline, bottom centre (ui/widgets/loop_timeline.py). */
#loopTimeline {
    background-color: $bg_panel@0.95;
    border-radius: 12px;
    border: 1px solid $border_soft@0.55;
}

#loopTimeline QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 7px;
    font-size: 11px;
    font-weight: 500;
    color: $text_secondary;
}

#loopTimeline QToolButton:hover {
    background-color: $bg_hover;
    border-color: $border_strong;
    color: $text_bright;
}

#loopTimeline QToolButton:checked {
    background-color: $accent;
    border-color: $accent;
    color: $bg_base;
}

#loopTimeline QToolButton:disabled {
    color: $text_disabled;
}

#loopTimeline QLabel#loopTimelineTime {
    background-color: transparent;
    color: $text_secondary;
    font-size: 12px;
    font-weight: 600;
}

#loopTimeline QLabel#loopTimelineTime[looping="true"] {
    color: $warn;
}

/* Overflow (⋯) button of the responsive toolbar: no menu arrow. */
#floatingToolbar QToolButton#toolbarMore::menu-indicator {
    image: none;
    width: 0px;
}

/* Drawer pinned open (right-click a toolbar button): ui/widgets/pin_dot.py. */
QLabel#drawerPinDot {
    background-color: $accent;
    border: 1px solid $bg_panel;
    border-radius: 3px;
}

/* Layer health (ui/layer_status.py): dot on toolbar buttons, drawer time labels. */
QLabel#layerStatusDot {
    background-color: transparent;
    border: 1px solid $bg_panel;
    border-radius: 4px;
}

QLabel#layerStatusDot[layerState="stale"] {
    background-color: $warn;
}

QLabel#layerStatusDot[layerState="error"] {
    background-color: $error;
}

#floatingToolbar QLabel[layerState="warn"] {
    color: $warn;
}

#floatingToolbar QLabel[layerState="stale"] {
    color: $warn_5;
}

#floatingToolbar QLabel[layerState="error"] {
    color: $error_soft;
}

/* Reusable transparent outlined buttons for product palettes. */
#floatingToolbar QToolButton[paletteButton="true"] {
    background: transparent;
    background-color: transparent;
    border: 1px solid $border_soft@0.62;
    border-radius: 6px;
    color: $text_secondary;
}

#floatingToolbar QToolButton[paletteButton="true"]:hover {
    background: $info@0.08;
    border-color: $info@0.55;
    color: $text_bright;
}

#floatingToolbar QToolButton[paletteButton="true"]:checked,
#floatingToolbar QToolButton[paletteButton="true"]:pressed {
    background: $info@0.18;
    border-color: $info;
    color: $text_bright;
}

/* Pills paint their own background; the global QWidget rule would paint the
   plain containers, dividers and sliders inside them $bg_base on top of it.
   A leading dot matches that exact class only, so inputs and popups keep
   their own backgrounds. Custom-painted widgets are named by class. */
#floatingToolbar .QWidget,
#floatingToolbar .QFrame,
#floatingToolbar QSlider,
#archiveControls .QWidget,
#archiveControls .QFrame,
#archiveControls QSlider,
#archiveControls _HourAxis,
#archiveControls _CoverageStrip {
    background: transparent;
}

/* Styled QFrames draw no VLine, so the _vdiv dividers get a 1px border in
   the color each control set for them. */
#floatingToolbar QFrame[frameShape="5"] {
    border-left: 1px solid $border;
}

#archiveControls QFrame[frameShape="5"] {
    border-left: 1px solid $divider;
}

#floatingToolbar QComboBox#radarSiteCombo,
#floatingToolbar QComboBox#radarProductCombo,
#floatingToolbar QToolButton#radarStationsButton {
    background-color: $bg_hover@0.62;
    border: 1px solid $border_soft@0.48;
}

#floatingToolbar QComboBox#radarSiteCombo:hover,
#floatingToolbar QComboBox#radarProductCombo:hover,
#floatingToolbar QToolButton#radarStationsButton:hover {
    border-color: $text_label@0.72;
}

#floatingToolbar QCheckBox::indicator {
    background: transparent;
    border: none;
}

/* ── Vehicle Pill ─────────────────────────────────────── */
#vehiclePill {
    background-color: $bg_menu@0.98;
    border-radius: 12px;
    border: 1px solid $border_soft@0.6;
    min-width: 280px;
}

#vehiclePill QWidget, #vehiclePill QFrame {
    background: transparent;
}

#vehiclePill QLabel {
    background: transparent;
    color: $text_secondary;
}

#vehiclePill QLabel#vehiclePillTitle {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.3px;
    color: $info;
}

#vehiclePill QLabel#vehiclePillCount {
    background-color: $info@0.16;
    color: $info;
    border-radius: 8px;
    padding: 1px 6px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
}

#vehiclePill QCheckBox#vehiclePillToggle {
    font-size: 11px;
    color: $text_label;
    background: transparent;
}

#vehiclePill QLabel#vehiclePillEmpty {
    color: $text_label;
    font-size: 11px;
    padding: 6px 0;
}

#vehiclePill QWidget#vehicleRowsContainer {
    background: transparent;
}

/* ── Vehicle Detail Pill ─────────────────────────────── */
#vehicleDetailPill QWidget, #vehicleDetailPill QFrame {
    background: transparent;
}

#vehicleDetailPill {
    background-color: $bg_menu_2@0.98;
    border-radius: 12px;
    border: 1px solid $border_soft@0.6;
    min-width: 280px;
}

#vehicleDetailPill QLabel#vehicleDetailTitle {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    color: $text;
}

/* ── Hazard Drawer ────────────────────────────────────── */
/* Overrides the global floatingToolbar QToolButton:checked orange rule so  *
 * hazard mode buttons always show blue and stay readable over the map.     */
#floatingToolbar QWidget#hazardDrawer QToolButton {
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#hazardDrawer QToolButton:hover {
    background-color: $info@0.08;
    border-color: $info;
    color: $text_bright;
}

#floatingToolbar QWidget#hazardDrawer QToolButton:checked {
    background-color: $info@0.18;
    border-color: $info;
    color: $info;
    font-weight: 600;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo {
    background-color: $bg_hover@0.62;
    border: 1px solid $border_soft@0.48;
    border-radius: 6px;
    color: $text;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0px;
    padding: 2px 18px 2px 8px;
    min-height: 18px;
    max-height: 24px;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo:hover {
    background-color: $bg_hover@0.78;
    border-color: $text_label@0.72;
    color: $text_bright;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo::drop-down {
    width: 18px;
    border: none;
    background: transparent;
}

#floatingToolbar QWidget#hazardDrawer QComboBox#spcDayCombo QAbstractItemView {
    background-color: $bg_elevated;
    border: 1px solid $border;
    color: $text;
    selection-background-color: $accent;
    selection-color: $bg_base;
    outline: none;
    padding: 0px;
    min-width: 156px;
    min-height: 100px;
}

QFrame#spcDayPopup {
    background-color: $bg_elevated;
    border: 1px solid $border;
    border-radius: 6px;
}

QToolButton#spcDayPopupButton {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    color: $text;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0px;
    padding: 0px;
}

QToolButton#spcDayPopupButton:hover {
    background-color: $bg_hover;
    color: $text_bright;
}

QToolButton#spcDayPopupButton:checked {
    background-color: $info@0.18;
    color: $info;
    font-weight: 600;
}

/* ── Map Drawer ───────────────────────────────────────── */
#floatingToolbar QWidget#mapDrawer QToolButton,
#floatingToolbar QToolButton[mapSubButton="true"],
#floatingToolbar QWidget#satelliteDrawer QToolButton {
    background: transparent;
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#mapDrawer QToolButton:hover,
#floatingToolbar QToolButton[mapSubButton="true"]:hover,
#floatingToolbar QWidget#satelliteDrawer QToolButton:hover {
    background: $info@0.08;
    background-color: $info@0.08;
    border-color: $info;
    color: $text_bright;
}

#floatingToolbar QWidget#mapDrawer QToolButton:checked,
#floatingToolbar QWidget#mapDrawer QToolButton:pressed,
#floatingToolbar QToolButton[mapSubButton="true"]:checked,
#floatingToolbar QToolButton[mapSubButton="true"]:pressed,
#floatingToolbar QWidget#satelliteDrawer QToolButton:checked,
#floatingToolbar QWidget#satelliteDrawer QToolButton:pressed {
    background: $info@0.18;
    background-color: $info@0.18;
    border-color: $info;
    color: $info;
    font-weight: 600;
}

/* ── Deploy Locs Drawer ───────────────────────────────── */
#floatingToolbar QWidget#deployLocsDrawer QToolButton,
#floatingToolbar QWidget#trackDrawer QToolButton {
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#deployLocsDrawer QToolButton:hover,
#floatingToolbar QWidget#trackDrawer QToolButton:hover {
    background-color: $info@0.08;
    border-color: $info;
    color: $text_bright;
}

#floatingToolbar QWidget#trackDrawer QToolButton:disabled {
    border-color: $border_subtle;
    color: $text_disabled;
}

#floatingToolbar QWidget#deployLocsDrawer QToolButton:checked {
    background-color: $info@0.18;
    border-color: $info;
    color: $info;
    font-weight: 600;
}

#floatingToolbar QWidget#deployLocsFilterRow {
    background: transparent;
}

#floatingToolbar QSlider#deployLocsSlider {
    height: 18px;
}

#floatingToolbar QSlider#deployLocsSlider::groove:horizontal {
    height: 4px;
    background: $border;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSlider::sub-page:horizontal {
    background: $info;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: $text_bright;
    border-radius: 6px;
}

#floatingToolbar QSlider#deployLocsSlider::handle:horizontal:hover {
    background: $info;
}

#floatingToolbar QLabel#deployLocsSliderLabel {
    background: transparent;
    color: $text_secondary;
    font-size: 11px;
}

#floatingToolbar QWidget#deployLocsSizeRow {
    background: transparent;
}

#floatingToolbar QSlider#deployLocsSizeSlider {
    height: 18px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::groove:horizontal {
    height: 4px;
    background: $border;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::sub-page:horizontal {
    background: $info;
    border-radius: 2px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    background: $text_bright;
    border-radius: 6px;
}

#floatingToolbar QSlider#deployLocsSizeSlider::handle:horizontal:hover {
    background: $info;
}

/* ── Routing Drawer ───────────────────────────────────── */
#floatingToolbar QWidget#routingDrawer QLineEdit {
    background-color: $bg_elevated@0.85;
    border: 1px solid $border;
    border-radius: 5px;
    padding: 3px 7px;
    font-size: 11px;
    color: $text;
    min-height: 20px;
}

#floatingToolbar QWidget#routingDrawer QLineEdit:focus {
    border-color: $info;
}

#floatingToolbar QWidget#routingDrawer QToolButton {
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 3px 8px;
}

#floatingToolbar QWidget#routingDrawer QToolButton:hover {
    background-color: $info@0.08;
    border-color: $info;
    color: $text_bright;
}

#floatingToolbar QWidget#routingDrawer QToolButton:checked,
#floatingToolbar QWidget#routingDrawer QToolButton:pressed {
    background-color: $info@0.18;
    border-color: $info;
    color: $info;
    font-weight: 600;
}

/* Small fixed-size buttons in origin / dest rows (⊕ pick, nav-arrow loc) */
#floatingToolbar QWidget#routingDrawer QToolButton[text="⊕"],
#floatingToolbar QWidget#routingDrawer QToolButton#locBtn {
    padding: 1px 2px;
    font-size: 13px;
    border-radius: 4px;
}

#floatingToolbar QWidget#routingDrawer QToolButton#locBtn:enabled {
    border-color: $info@0.4;
}

#floatingToolbar QWidget#routingDrawer QToolButton#locBtn:enabled:hover {
    background-color: $info@0.15;
    border-color: $info;
}

#floatingToolbar QWidget#routingDrawer QToolButton#locBtn:disabled {
    border-color: $border_4;
}

/* ── Outlook Panel ────────────────────────────────────── */
#outlookPanel {
    background-color: $bg_panel@0.95;
    border-radius: 12px;
    border: 1px solid $border_soft@0.55;
}

#outlookPanelTitle {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: $info;
    background: transparent;
}

#outlookPanelClose {
    background: transparent;
    border: none;
    color: $text_muted;
    font-size: 16px;
    padding: 0 2px;
}

#outlookPanelClose:hover {
    color: $text;
}

#outlookPanelText {
    background-color: transparent;
    border: none;
    color: $text_secondary;
    font-size: 11px;
    selection-background-color: $accent;
}

QWidget#outlookTabRow {
    background: transparent;
}

#outlookTabBtn {
    background: $white@0.05;
    border: 1px solid $border_soft@0.5;
    border-radius: 4px;
    color: $text_label;
    font-size: 10px;
    font-weight: 600;
    padding: 2px 7px;
}

#outlookTabBtn:hover {
    background: $info@0.12;
    border-color: $info@0.4;
    color: $text_secondary;
}

#outlookTabBtn:checked {
    background: $info@0.18;
    border-color: $info;
    color: $info;
}

/* ── Status Overlays ──────────────────────────────────── */
QWidget#statusOverlayLeft {
    background-color: $bg_panel@0.88;
    border-radius: 8px;
    border: 1px solid $border_soft_2@0.5;
}

QWidget#statusOverlayLeft QLabel,
QWidget#statusOverlayLeft QPushButton {
    background: transparent;
    border: none;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.5px;
    padding: 0;
    color: $text_secondary;
}

/* GPS / AWS / NET / mode badge and the clock: the things read at a glance. */
QWidget#statusOverlayLeft *[pillRole="badge"] {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
}

QWidget#statusOverlayLeft *[pillRole="clock"] {
    font-size: 12px;
}

QWidget#statusOverlayLeft *[pillRole="action"] {
    font-weight: 700;
    letter-spacing: 1px;
}

/* Colors set with _set_pill_status() in ui/app/main_window.py. */
QWidget#statusOverlayLeft *[pillStatus="ok"]     { color: $ok; }
QWidget#statusOverlayLeft *[pillStatus="info"]   { color: $info; }
QWidget#statusOverlayLeft *[pillStatus="warn"]   { color: $warn; }
QWidget#statusOverlayLeft *[pillStatus="error"]  { color: $error_soft; }
QWidget#statusOverlayLeft *[pillStatus="deps"]   { color: $warn_2; }
QWidget#statusOverlayLeft *[pillStatus="accent"] { color: $accent; }
QWidget#statusOverlayLeft *[pillStatus="label"]  { color: $text_label; }
QWidget#statusOverlayLeft *[pillStatus="muted"]  { color: $text_muted; }
QWidget#statusOverlayLeft *[pillStatus="off"]    { color: $text_disabled; }
QWidget#statusOverlayLeft *[pillStatus="mode_vehicle"] { color: $mode_vehicle; }
QWidget#statusOverlayLeft *[pillStatus="mode_monitor"] { color: $mode_monitor; }
QWidget#statusOverlayLeft *[pillStatus="mode_viewer"]  { color: $mode_viewer; }
QWidget#statusOverlayLeft *[pillStatus="mode_archive"] { color: $mode_archive; }
QWidget#statusOverlayLeft *[pillStatus="mode_admin"]   { color: $mode_admin; }

/* Current map click mode (ui/click_mode.py): "▶ measure — click first point · Esc to exit". */
QLabel#clickModeBanner {
    background-color: $bg_panel@0.92;
    border: 1px solid $accent@0.6;
    border-radius: 8px;
    padding: 5px 12px;
    color: $accent;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.5px;
}

/* Keyboard shortcut list (? key, ui/widgets/shortcut_overlay.py). */
QFrame#shortcutOverlay {
    background-color: $bg_panel@0.97;
    border: 1px solid $border_soft@0.7;
    border-radius: 12px;
}

QFrame#shortcutOverlay QWidget {
    background: transparent;
}

QFrame#shortcutOverlay QLabel {
    color: $text_secondary;
    font-size: 12px;
}

QFrame#shortcutOverlay QLabel#shortcutOverlayTitle {
    color: $text;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1px;
}

QFrame#shortcutOverlay QLabel#shortcutOverlayGroup {
    color: $text_label;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding-bottom: 2px;
}

QFrame#shortcutOverlay QLabel#shortcutKey {
    color: $accent;
    font-weight: 700;
}

QFrame#shortcutOverlay QLabel#shortcutOverlayHint {
    color: $text_muted;
    font-size: 11px;
}

/* ── Navigation Pill ──────────────────────────────────── */
QWidget#navPill {
    background-color: $bg_panel@0.93;
    border-radius: 10px;
    border: 1px solid $info@0.4;
}

QWidget#navPill QLabel#navPillStep {
    background: transparent;
    color: $text_bright;
    font-size: 15px;
    font-weight: 600;
    padding: 0;
}

QWidget#navPill QLabel#navPillSummary {
    background: transparent;
    color: $info;
    font-size: 13px;
    font-weight: 500;
    padding: 0;
}

QWidget#navPill QToolButton {
    background: transparent;
    border: none;
    color: $text_label;
    font-size: 16px;
    padding: 0;
}

QWidget#navPill QToolButton:hover {
    color: $text_bright;
}

/* ── Side Panel ───────────────────────────────────────── */
QDockWidget {
    background-color: $bg_panel;
    border: none;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}

QDockWidget::title {
    background-color: $bg_panel;
    border-bottom: 1px solid $border_subtle;
    padding: 8px 12px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: $text_muted;
}

QDockWidget QWidget {
    background-color: $bg_panel;
}

/* ── Scroll Bars ──────────────────────────────────────── */
QScrollBar:vertical {
    background-color: transparent;
    width: 6px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: $border;
    border-radius: 3px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: $accent;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 6px;
}

QScrollBar::handle:horizontal {
    background-color: $border;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background-color: $accent;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Labels ───────────────────────────────────────────── */
QLabel {
    background-color: transparent;
    color: $text;
}

QLabel#sectionHeader {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    color: $text_muted;
    padding: 4px 0px;
}

QLabel#vehicleCallsign {
    font-size: 13px;
    font-weight: 600;
    color: $text;
}

QLabel#vehicleMeta {
    font-size: 10px;
    color: $text_muted;
}

QLabel#obsValue {
    font-size: 12px;
    font-weight: 500;
    color: $accent;
}

/* ── Buttons ──────────────────────────────────────────── */
QPushButton {
    background-color: $bg_elevated;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 11px;
    font-weight: 500;
    color: $text;
}

QPushButton:hover {
    background-color: $bg_elevated_2;
    border-color: $accent;
    color: $accent;
}

QPushButton:pressed {
    background-color: $accent;
    border-color: $accent;
    color: $bg_base;
}

QPushButton#primaryButton {
    background-color: $accent;
    border-color: $accent;
    color: $bg_base;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: $accent_hover_legacy;
    border-color: $accent_hover_legacy;
}

QPushButton#dangerButton {
    border-color: $error;
    color: $error;
}

QPushButton#dangerButton:hover {
    background-color: $error;
    color: $bg_base;
}

QComboBox#radarProductCombo, QComboBox#radarSiteCombo, QToolButton#radarStationsButton {
    padding: 4px 8px;
}

/* ── ComboBox ─────────────────────────────────────────── */
QComboBox {
    background-color: $bg_elevated;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 11px;
    color: $text;
    min-width: 120px;
}

QComboBox:hover {
    border-color: $accent;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    width: 8px;
    height: 8px;
    border-left: 2px solid $text_muted;
    border-bottom: 2px solid $text_muted;
    margin-right: 6px;
}

QComboBox QAbstractItemView {
    background-color: $bg_elevated;
    border: 1px solid $border;
    border-radius: 6px;
    selection-background-color: $accent;
    selection-color: $bg_base;
    padding: 4px;
}

/* ── Slider ───────────────────────────────────────────── */
QSlider::groove:horizontal {
    background-color: $border_subtle;
    height: 3px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background-color: $accent;
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -6px 0;
}

QSlider::sub-page:horizontal {
    background-color: $accent;
    border-radius: 2px;
}

#floatingToolbar QWidget#radarDrawer QSlider,
#floatingToolbar QWidget#satelliteDrawer QSlider,
#floatingToolbar QWidget#landcoverDrawer QSlider {
    background: transparent;
}

#floatingToolbar QWidget#radarDrawer QSlider::groove:horizontal,
#floatingToolbar QWidget#satelliteDrawer QSlider::groove:horizontal,
#floatingToolbar QWidget#landcoverDrawer QSlider::groove:horizontal {
    background-color: $text_secondary@0.28;
    height: 2px;
    border-radius: 1px;
}

/* ── Mesoanalysis / SFCOA drawers ─────────────────────── */
#floatingToolbar QWidget#mesoanalysisDrawer,
#floatingToolbar QWidget#sfcoaDrawer,
#floatingToolbar QWidget#mesoanalysisControlRow,
#floatingToolbar QWidget#sfcoaControlRow,
#floatingToolbar QWidget#sfcoaProductGrid,
#floatingToolbar QWidget#mesoanalysisStatusRow {
    background: transparent;
    border: none;
}

#floatingToolbar QWidget#mesoanalysisDrawer QToolButton,
#floatingToolbar QWidget#sfcoaDrawer QToolButton {
    background-color: transparent;
    border: 1px solid $border_soft@0.42;
    border-radius: 6px;
    color: $text_secondary;
    padding: 3px 8px;
}

#floatingToolbar QWidget#mesoanalysisDrawer QLabel#mesoanalysisGroupLabel,
#floatingToolbar QWidget#sfcoaDrawer QLabel#sfcoaGroupLabel {
    color: $text_muted;
    font-size: 11px;
    font-weight: 700;
    min-width: 72px;
}

#floatingToolbar QWidget#mesoanalysisProductGrid QToolButton,
#floatingToolbar QWidget#sfcoaProductGrid QToolButton {
    background-color: transparent;
    background: transparent;
    min-width: 62px;
    padding: 2px 6px;
    font-size: 11px;
}

#floatingToolbar QWidget#mesoanalysisProductGrid,
#floatingToolbar QWidget#mesoanalysisProductGrid QWidget,
#floatingToolbar QWidget#mesoanalysisPlaybackCell,
#floatingToolbar QWidget#sfcoaProductGrid,
#floatingToolbar QWidget#sfcoaProductGrid QWidget {
    background: transparent;
    background-color: transparent;
}

#floatingToolbar QWidget#mesoanalysisDrawer QLabel#mesoanalysisTimeLabel,
#floatingToolbar QWidget#sfcoaDrawer QLabel#sfcoaTimeLabel {
    color: $text_secondary;
    font-size: 11px;
    font-weight: 600;
}

#floatingToolbar QWidget#mesoanalysisProductGrid QToolButton:hover,
#floatingToolbar QWidget#sfcoaProductGrid QToolButton:hover {
    background-color: $info@0.08;
    border-color: $info@0.35;
}

#floatingToolbar QWidget#mesoanalysisProductGrid QToolButton:checked,
#floatingToolbar QWidget#sfcoaProductGrid QToolButton:checked {
    background-color: $info@0.18;
    border-color: $info;
}

#floatingToolbar QWidget#mesoanalysisDrawer QToolButton:hover,
#floatingToolbar QWidget#sfcoaDrawer QToolButton:hover {
    background-color: $info@0.08;
    border-color: $info@0.55;
}

#floatingToolbar QWidget#mesoanalysisDrawer QToolButton:checked,
#floatingToolbar QWidget#sfcoaDrawer QToolButton:checked {
    background-color: $info@0.18;
    border-color: $info;
    color: $text_bright;
}

#floatingToolbar QWidget#mesoanalysisDrawer QComboBox,
#floatingToolbar QWidget#sfcoaDrawer QComboBox {
    background-color: $bg_hover@0.62;
    border: 1px solid $border_soft@0.48;
    border-radius: 6px;
    color: $text_secondary;
    padding: 2px 7px;
    min-width: 0;
}

#floatingToolbar QWidget#mesoanalysisDrawer QComboBox:hover,
#floatingToolbar QWidget#sfcoaDrawer QComboBox:hover {
    border-color: $text_label@0.72;
}

#floatingToolbar QWidget#mesoanalysisDrawer QPushButton,
#floatingToolbar QWidget#sfcoaDrawer QPushButton {
    background: transparent;
    border: 1px solid $border_soft@0.48;
    border-radius: 6px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
}

#floatingToolbar QWidget#mesoanalysisDrawer QPushButton:hover,
#floatingToolbar QWidget#sfcoaDrawer QPushButton:hover {
    background-color: $info@0.08;
    border-color: $info@0.55;
    color: $text_bright;
}

#floatingToolbar QWidget#mesoanalysisDrawer QSlider {
    background: transparent;
}

#floatingToolbar QWidget#mesoanalysisDrawer QSlider::groove:horizontal {
    background-color: $text_secondary@0.28;
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
    background-color: $bg_elevated;
    border-color: $border;
    color: $text;
}

QWidget#radarPlaybackRow QToolButton:checked,
QWidget#radarPlaybackRow QToolButton:pressed,
QWidget#satPlaybackRow QToolButton:checked,
QWidget#satPlaybackRow QToolButton:pressed {
    background-color: $accent;
    border-color: $accent;
    color: $bg_base;
}

/* ── Tooltips ─────────────────────────────────────────── */
/* Popup menus (toolbar ⋯, map right-click, loop speed): one look, like the pills. */
QMenu {
    background-color: $bg_menu;
    border: 1px solid $border_soft;
    border-radius: 8px;
    padding: 4px;
}

QMenu::item {
    background-color: transparent;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 500;
    padding: 5px 18px 5px 10px;
    border-radius: 5px;
}

QMenu::item:selected {
    background-color: $info@0.18;
    color: $text_bright;
}

QMenu::item:disabled {
    color: $text_muted;
}

QMenu::item:checked {
    color: $info;
}

QMenu::separator {
    height: 1px;
    background-color: $divider;
    margin: 4px 6px;
}

/* Menu bar (File / Edit / View / Help): a thin row on Windows and Linux;
   macOS shows it in the system menu bar instead. */
QMenuBar {
    background-color: $bg_base;
    border-bottom: 1px solid $border_subtle;
    padding: 1px 4px;
}

QMenuBar::item {
    background-color: transparent;
    color: $text_secondary;
    font-size: 11px;
    padding: 3px 9px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: $info@0.18;
    color: $text_bright;
}

QToolTip {
    background-color: $bg_elevated;
    border: 1px solid $border;
    border-radius: 4px;
    color: $text;
    font-size: 11px;
    padding: 4px 8px;
}

/* ── Toolbar checkboxes ──────────────────────────────── */
#floatingToolbar QCheckBox {
    background: transparent;
    border: none;
    color: $text_secondary;
    font-size: 11px;
    spacing: 5px;
    padding: 0;
}

#floatingToolbar QWidget#radarDrawer QCheckBox,
#floatingToolbar QWidget#hazardDrawer QCheckBox,
#floatingToolbar QWidget#satelliteDrawer QCheckBox,
#floatingToolbar QWidget#deployLocsDrawer QCheckBox,
#floatingToolbar QWidget#routingDrawer QCheckBox,
#floatingToolbar QWidget#soundingDrawer QCheckBox,
#floatingToolbar QWidget#trackDrawer QCheckBox,
#floatingToolbar QWidget#surfaceDrawer QCheckBox {
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
    color: $border_subtle;
}

/* ── Annotation Dialog ────────────────────────────────── */
QDialog#annotationDialog {
    background-color: $bg_base;
}

QDialog#annotationDialog QLabel {
    background-color: transparent;
}

QDialog#annotationDialog QLineEdit {
    background-color: $bg_elevated;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
    color: $text;
}

QDialog#annotationDialog QLineEdit:focus {
    border-color: $accent;
}

/* ── Layer Order Pill ────────────────────────────────── */
#layerOrderPill {
    background: transparent;
}

#layerOrderPanel {
    background-color: $bg_panel@0.95;
    border-radius: 10px;
    border: 1px solid $border_soft@0.55;
    min-width: 200px;
}

#layerOrderPanel QWidget {
    background: transparent;
}

/* ── Archive Controls Bar ─────────────────────────────── */
#archiveControls {
    background-color: $bg_panel@0.88;
    border: 1px solid $border_soft_2@0.5;
    border-radius: 8px;
}

#archiveControls QLabel {
    background: transparent;
    padding: 0;
}

#archiveControls QToolButton {
    background-color: $bg_elevated@0.34;
    border: 1px solid transparent;
    border-radius: 5px;
    color: $text_secondary;
    font-size: 12px;
    font-weight: 600;
    padding: 0;
}

#archiveControls QToolButton:hover {
    background-color: $bg_hover@0.85;
    border-color: $border_soft@0.75;
    color: $text_bright;
}

#archiveControls QToolButton:checked {
    background-color: $mode_archive;
    border-color: $mode_archive;
    color: $bg_base;
}

#archiveControls QToolButton:disabled {
    background-color: transparent;
    color: $text_disabled;
}

#archiveControls QSlider::groove:horizontal {
    height: 4px;
    background: $border_subtle;
    border-radius: 2px;
}

#archiveControls QSlider::handle:horizontal {
    background: $accent;
    border: none;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}

#archiveControls QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 2px;
}

#archiveControls QComboBox {
    background-color: $bg_elevated@0.55;
    border: 1px solid $border@0.85;
    border-radius: 5px;
    color: $text_secondary;
    font-size: 11px;
    font-weight: 600;
    padding: 1px 6px;
    min-width: 40px;   /* not the global 120px: 40 + padding and border = the speed box's 54px */
}

#archiveControls QComboBox:hover {
    border-color: $border_strong;
}

#archiveControls QComboBox QAbstractItemView {
    background-color: $bg_elevated;
    border: 1px solid $border_strong;
    color: $text;
    selection-background-color: $mode_archive;
    selection-color: $bg_base;
}
""")


def set_pill_status(widget, status: str) -> None:
    """Color a status-pill label through its ``pillStatus`` property (rules above)."""
    if widget.property("pillStatus") == status:
        return
    widget.setProperty("pillStatus", status)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
