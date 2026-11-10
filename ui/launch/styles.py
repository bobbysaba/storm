from ui.palette import qss


_DIALOG_STYLE = qss("""
QDialog {
    background-color: $bg_base;
}
QLabel {
    color: $text;
    background: transparent;
}
QLabel#title {
    color: $accent;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 3px;
}
QLabel#subtitle {
    color: $text_muted;
    font-size: 11px;
    letter-spacing: 1px;
}
QLabel#fieldLabel {
    color: $text_label;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}
QLabel#hint {
    color: $text_muted;
    font-size: 10px;
}
QLineEdit {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text;
    font-size: 13px;
    padding: 6px 10px;
    selection-background-color: $accent;
}
QLineEdit:focus {
    border: 1px solid $accent;
}
QPushButton#browseBtn {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text_label;
    font-size: 12px;
    padding: 6px 12px;
    min-width: 32px;
}
QPushButton#browseBtn:hover {
    border-color: $accent;
    color: $accent;
}
QPushButton#lockBtn {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text_muted;
    font-size: 14px;
    padding: 4px 8px;
    min-width: 32px;
}
QPushButton#lockBtn:hover {
    border-color: $accent;
    color: $accent;
}
QLineEdit:read-only {
    background-color: $bg_sunken;
    border: 1px solid $border_dim;
    color: $text_muted;
}
QLineEdit:disabled {
    background-color: $bg_sunken;
    border: 1px solid $border_dim;
    color: $text_muted;
}
QPushButton#lockBtn:disabled {
    background-color: $bg_sunken;
    border: 1px solid $border_dim;
    color: $text_disabled;
}
QPushButton#browseBtn:disabled {
    background-color: $bg_sunken;
    border: 1px solid $border_dim;
    color: $text_disabled;
}
QPushButton#launchBtn {
    background-color: $accent;
    border: none;
    border-radius: 8px;
    color: $bg_base;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 10px 32px;
}
QPushButton#launchBtn:hover {
    background-color: $accent_hover;
}
QPushButton#launchBtn:pressed {
    background-color: $accent_pressed;
}

QDateTimeEdit {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text;
    font-size: 13px;
    padding: 6px 10px;
    selection-background-color: $accent;
}
QDateTimeEdit:focus {
    border: 1px solid $accent;
}
QDateTimeEdit::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid $border_subtle;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: $bg_elevated;
}
QDateTimeEdit::drop-down:hover {
    background-color: $bg_info_tint;
    border-left-color: $accent;
}
QDateTimeEdit::down-arrow {
    width: 8px;
    height: 5px;
}
QCalendarWidget {
    background-color: $bg_base;
    color: $text;
    border: 1px solid $text_disabled;
    border-radius: 10px;
}
QCalendarWidget QToolButton {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 4px;
    color: $text;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 6px;
}
QCalendarWidget QToolButton:hover {
    border-color: $accent;
    color: $accent;
}
QCalendarWidget QToolButton#qt_calendar_prevmonth,
QCalendarWidget QToolButton#qt_calendar_nextmonth {
    min-width: 26px;
    padding: 3px 4px;
}
QCalendarWidget QMenu {
    background-color: $bg_base;
    border: 1px solid $border_subtle;
    color: $text;
}
QCalendarWidget QSpinBox {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 4px;
    color: $text;
    font-size: 11px;
    padding: 2px 4px;
}
QCalendarWidget QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    border-left: 1px solid $border_subtle;
    border-bottom: 1px solid $border_subtle;
    border-top-right-radius: 4px;
    background-color: $bg_elevated;
}
QCalendarWidget QSpinBox::up-button:hover {
    background-color: $bg_info_tint;
}
QCalendarWidget QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    border-left: 1px solid $border_subtle;
    border-top: 1px solid $border_subtle;
    border-bottom-right-radius: 4px;
    background-color: $bg_elevated;
}
QCalendarWidget QSpinBox::down-button:hover {
    background-color: $bg_info_tint;
}
QCalendarWidget QAbstractItemView {
    background-color: $bg_sunken_3;
    alternate-background-color: $bg_base;
    color: $text;
    selection-background-color: $accent;
    selection-color: $bg_base;
    gridline-color: $border_subtle;
    border: none;
}
QCalendarWidget QAbstractItemView:disabled {
    color: $text_disabled;
}
QCalendarWidget #qt_calendar_navigationbar {
    background-color: $bg_sunken;
    border-radius: 9px 9px 0px 0px;
    padding: 2px;
}

QFrame#divider {
    color: $border_subtle;
}
QPushButton#dataToggleBtn {
    background: transparent;
    border: none;
    color: $text_label;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 0px;
    text-align: left;
}
QPushButton#dataToggleBtn:hover {
    color: $accent;
}
QToolButton#iconBtn {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text_muted;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 4px 2px 3px 2px;
    min-width: 40px;
    min-height: 42px;
}
QToolButton#iconBtn:hover {
    border-color: $info;
    color: $info;
}
QToolButton#iconBtn:disabled {
    background-color: $bg_sunken;
    border: 1px solid $border_dim;
    color: $text_disabled;
}
QComboBox {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text;
    font-size: 12px;
    padding: 5px 10px;
    selection-background-color: $accent;
}
QComboBox:focus {
    border: 1px solid $accent;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid $border_subtle;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: $bg_elevated;
}
QComboBox::drop-down:hover {
    background-color: $bg_info_tint;
    border-left-color: $accent;
}
QComboBox QAbstractItemView {
    background-color: $bg_elevated;
    border: 1px solid $text_disabled;
    color: $text;
    selection-background-color: $accent;
    selection-color: $bg_base;
    outline: none;
}
""")

_ICON_SELECTED_STYLE = qss("""
QToolButton {
    background-color: $bg_info_tint;
    border: 2px solid $accent;
    border-radius: 6px;
    color: $accent;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 3px 1px 2px 1px;
    min-width: 40px;
    min-height: 42px;
}
""")

_MODE_BTN_STYLE = qss("""
QPushButton {
    background-color: $bg_elevated;
    border: 1px solid $border_subtle;
    border-radius: 6px;
    color: $text_muted;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 7px 0px;
}
QPushButton:hover {
    border-color: $accent;
    color: $text_label;
}
""")

_MODE_BTN_SELECTED_STYLE = qss("""
QPushButton {
    background-color: $bg_info_tint;
    border: 2px solid $accent;
    border-radius: 6px;
    color: $accent;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 7px 0px;
}
""")

# update button style variants — applied directly to the widget so they can
_UPD_CHECKING = qss("""
    QPushButton {
        background-color: $bg_sunken_2;
        border: 1px solid $bg_elevated;
        border-radius: 6px;
        color: $text_disabled;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
""")
_UPD_CURRENT = qss("""
    QPushButton {
        background-color: $bg_sunken_2;
        border: 1px solid $bg_elevated;
        border-radius: 6px;
        color: $text_disabled;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
""")
_UPD_AVAILABLE = qss("""
    QPushButton {
        background-color: $accent;
        border: none;
        border-radius: 6px;
        color: $bg_base;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
    QPushButton:hover  { background-color: $accent_hover; }
    QPushButton:pressed { background-color: $accent_pressed; }
""")
_UPD_SUCCESS = qss("""
    QPushButton {
        background-color: $bg_ok_tint;
        border: 1px solid $border_ok;
        border-radius: 6px;
        color: $ok_2;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
""")
_UPD_ERROR = qss("""
    QPushButton {
        background-color: $bg_error_tint;
        border: 1px solid $border_error;
        border-radius: 6px;
        color: $error_soft;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
""")
_UPD_WARNING = qss("""
    QPushButton {
        background-color: $bg_warn_tint;
        border: 1px solid $border_warn;
        border-radius: 6px;
        color: $warn_2;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 8px 16px;
        min-width: 180px;
        max-width: 420px;
    }
""")
_LOG_BTN_STYLE = qss("""
    QPushButton {
        background: transparent;
        border: none;
        color: $text_disabled;
        font-size: 10px;
        letter-spacing: 0.5px;
        padding: 2px 8px;
    }
    QPushButton:hover { color: $text_muted; }
""")
