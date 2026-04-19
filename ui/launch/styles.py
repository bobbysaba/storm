
_DIALOG_STYLE = """
QDialog {
    background-color: #0A0A0F;
}
QLabel {
    color: #E8EAF0;
    background: transparent;
}
QLabel#title {
    color: #00CFFF;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 3px;
}
QLabel#subtitle {
    color: #5A5B6A;
    font-size: 11px;
    letter-spacing: 1px;
}
QLabel#fieldLabel {
    color: #8E97AB;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}
QLabel#hint {
    color: #5A5B6A;
    font-size: 10px;
}
QLineEdit {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #E8EAF0;
    font-size: 13px;
    padding: 6px 10px;
    selection-background-color: #00CFFF;
}
QLineEdit:focus {
    border: 1px solid #00CFFF;
}
QPushButton#browseBtn {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #8E97AB;
    font-size: 12px;
    padding: 6px 12px;
    min-width: 32px;
}
QPushButton#browseBtn:hover {
    border-color: #00CFFF;
    color: #00CFFF;
}
QPushButton#lockBtn {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #5A5B6A;
    font-size: 14px;
    padding: 4px 8px;
    min-width: 32px;
}
QPushButton#lockBtn:hover {
    border-color: #00CFFF;
    color: #00CFFF;
}
QLineEdit:read-only {
    background-color: #12121E;
    border: 1px solid #16162A;
    color: #5A5B6A;
}
QLineEdit:disabled {
    background-color: #12121E;
    border: 1px solid #16162A;
    color: #5A5B6A;
}
QPushButton#lockBtn:disabled {
    background-color: #12121E;
    border: 1px solid #16162A;
    color: #2A2A3E;
}
QPushButton#browseBtn:disabled {
    background-color: #12121E;
    border: 1px solid #16162A;
    color: #2A2A3E;
}
QPushButton#launchBtn {
    background-color: #00CFFF;
    border: none;
    border-radius: 8px;
    color: #0A0A0F;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 10px 32px;
}
QPushButton#launchBtn:hover {
    background-color: #33D9FF;
}
QPushButton#launchBtn:pressed {
    background-color: #009ECC;
}

QDateTimeEdit {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #E8EAF0;
    font-size: 13px;
    padding: 6px 10px;
    selection-background-color: #00CFFF;
}
QDateTimeEdit:focus {
    border: 1px solid #00CFFF;
}
QDateTimeEdit::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #1E1E2E;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #1A1A2E;
}
QDateTimeEdit::drop-down:hover {
    background-color: #0D1A2E;
    border-left-color: #00CFFF;
}
QDateTimeEdit::down-arrow {
    width: 8px;
    height: 5px;
}
QCalendarWidget {
    background-color: #0A0A0F;
    color: #E8EAF0;
    border: 1px solid #2A2A3E;
    border-radius: 10px;
}
QCalendarWidget QToolButton {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 4px;
    color: #E8EAF0;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 6px;
}
QCalendarWidget QToolButton:hover {
    border-color: #00CFFF;
    color: #00CFFF;
}
QCalendarWidget QToolButton#qt_calendar_prevmonth,
QCalendarWidget QToolButton#qt_calendar_nextmonth {
    min-width: 26px;
    padding: 3px 4px;
}
QCalendarWidget QMenu {
    background-color: #0A0A0F;
    border: 1px solid #1E1E2E;
    color: #E8EAF0;
}
QCalendarWidget QSpinBox {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 4px;
    color: #E8EAF0;
    font-size: 11px;
    padding: 2px 4px;
}
QCalendarWidget QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    border-left: 1px solid #1E1E2E;
    border-bottom: 1px solid #1E1E2E;
    border-top-right-radius: 4px;
    background-color: #1A1A2E;
}
QCalendarWidget QSpinBox::up-button:hover {
    background-color: #0D1A2E;
}
QCalendarWidget QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    border-left: 1px solid #1E1E2E;
    border-top: 1px solid #1E1E2E;
    border-bottom-right-radius: 4px;
    background-color: #1A1A2E;
}
QCalendarWidget QSpinBox::down-button:hover {
    background-color: #0D1A2E;
}
QCalendarWidget QAbstractItemView {
    background-color: #0D0D1A;
    alternate-background-color: #0A0A0F;
    color: #E8EAF0;
    selection-background-color: #00CFFF;
    selection-color: #0A0A0F;
    gridline-color: #1E1E2E;
    border: none;
}
QCalendarWidget QAbstractItemView:disabled {
    color: #2A2A3E;
}
QCalendarWidget #qt_calendar_navigationbar {
    background-color: #12121E;
    border-radius: 9px 9px 0px 0px;
    padding: 2px;
}

QFrame#divider {
    color: #1E1E2E;
}
QPushButton#dataToggleBtn {
    background: transparent;
    border: none;
    color: #8E97AB;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 0px;
    text-align: left;
}
QPushButton#dataToggleBtn:hover {
    color: #00CFFF;
}
QToolButton#iconBtn {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #5A5B6A;
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 4px 2px 3px 2px;
    min-width: 40px;
    min-height: 42px;
}
QToolButton#iconBtn:hover {
    border-color: #4A9EFF;
    color: #4A9EFF;
}
QToolButton#iconBtn:disabled {
    background-color: #12121E;
    border: 1px solid #16162A;
    color: #2A2A3E;
}
QComboBox {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #E8EAF0;
    font-size: 12px;
    padding: 5px 10px;
    selection-background-color: #00CFFF;
}
QComboBox:focus {
    border: 1px solid #00CFFF;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #1E1E2E;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #1A1A2E;
}
QComboBox::drop-down:hover {
    background-color: #0D1A2E;
    border-left-color: #00CFFF;
}
QComboBox QAbstractItemView {
    background-color: #1A1A2E;
    border: 1px solid #2A2A3E;
    color: #E8EAF0;
    selection-background-color: #00CFFF;
    selection-color: #0A0A0F;
    outline: none;
}
"""

_ICON_SELECTED_STYLE = """
QToolButton {
    background-color: #0D1A2E;
    border: 2px solid #00CFFF;
    border-radius: 6px;
    color: #00CFFF;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 3px 1px 2px 1px;
    min-width: 40px;
    min-height: 42px;
}
"""

_MODE_BTN_STYLE = """
QPushButton {
    background-color: #1A1A2E;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    color: #5A5B6A;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 7px 0px;
}
QPushButton:hover {
    border-color: #00CFFF;
    color: #8E97AB;
}
"""

_MODE_BTN_SELECTED_STYLE = """
QPushButton {
    background-color: #0D1A2E;
    border: 2px solid #00CFFF;
    border-radius: 6px;
    color: #00CFFF;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 7px 0px;
}
"""

# update button style variants — applied directly to the widget so they can
_UPD_CHECKING = """
    QPushButton {
        background-color: #111120;
        border: 1px solid #1A1A2E;
        border-radius: 6px;
        color: #3A3B4A;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
"""
_UPD_CURRENT = """
    QPushButton {
        background-color: #111120;
        border: 1px solid #1A1A2E;
        border-radius: 6px;
        color: #3A3B4A;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
"""
_UPD_AVAILABLE = """
    QPushButton {
        background-color: #00CFFF;
        border: none;
        border-radius: 6px;
        color: #0A0A0F;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
    QPushButton:hover  { background-color: #33D9FF; }
    QPushButton:pressed { background-color: #009ECC; }
"""
_UPD_SUCCESS = """
    QPushButton {
        background-color: #0D2A1A;
        border: 1px solid #1A4A2A;
        border-radius: 6px;
        color: #4ADE80;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
"""
_UPD_ERROR = """
    QPushButton {
        background-color: #2A0D0D;
        border: 1px solid #4A1A1A;
        border-radius: 6px;
        color: #F87171;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 5px 16px;
        min-width: 180px;
    }
"""
_UPD_WARNING = """
    QPushButton {
        background-color: #241A00;
        border: 1px solid #3D2E00;
        border-radius: 6px;
        color: #FFB800;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.5px;
        padding: 8px 16px;
        min-width: 180px;
        max-width: 420px;
    }
"""
_LOG_BTN_STYLE = """
    QPushButton {
        background: transparent;
        border: none;
        color: #2A2B3A;
        font-size: 9px;
        letter-spacing: 0.5px;
        padding: 2px 8px;
    }
    QPushButton:hover { color: #5A5B6A; }
"""
