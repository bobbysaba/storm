# ui/launch_dialog.py
# Startup dialog — shown on every launch to confirm vehicle ID and data
# directory.  Persists settings via QSettings so they survive across sessions.

import hashlib
import os
import sys
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QToolButton, QFileDialog, QFrame,
    QTextEdit, QApplication, QMessageBox, QSizePolicy, QWidget,
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QByteArray, QSize
from PyQt6.QtGui import QPixmap, QPainter, QIcon

import config as _config

# SVG icon definitions for the vehicle icon picker (same shapes as the map markers).
# Color is substituted at render time so selected/unselected states differ.
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

# Update button style variants — applied directly to the widget so they can
# change at runtime without touching the dialog-level stylesheet.
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
        padding: 5px 16px;
        min-width: 180px;
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


from data.update_checker import UpdateWorker as _UpdateWorker


class _LogViewerDialog(QDialog):
    """Shows the contents of storm_fault.log with a copy-to-clipboard button."""

    def __init__(self, log_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STORM — Crash Log")
        self.setMinimumSize(620, 400)
        self.setStyleSheet("""
            QDialog { background-color: #0A0A0F; }
            QLabel  { color: #8E97AB; font-size: 11px; background: transparent; }
            QTextEdit {
                background-color: #050508;
                border: 1px solid #1A1A2E;
                border-radius: 6px;
                color: #39D98A;
                font-family: 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }
            QPushButton {
                background-color: #1A1A2E;
                border: 1px solid #1E1E2E;
                border-radius: 6px;
                color: #8E97AB;
                font-size: 11px;
                padding: 6px 16px;
            }
            QPushButton:hover { border-color: #00CFFF; color: #00CFFF; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"Log file: {log_path}"))

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        layout.addWidget(self._text)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._load(log_path)

    def _load(self, path: str):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                self._text.setPlainText("(Crash log is empty — no faults recorded.)")
            else:
                self._text.setPlainText("".join(lines[-50:]))
                self._text.verticalScrollBar().setValue(
                    self._text.verticalScrollBar().maximum()
                )
        except FileNotFoundError:
            self._text.setPlainText("(No crash log found — storm_fault.log does not exist yet.)")
        except Exception as exc:
            self._text.setPlainText(f"(Could not read log: {exc})")

    def _copy(self):
        QApplication.clipboard().setText(self._text.toPlainText())


class LaunchDialog(QDialog):
    """
    Pre-launch configuration dialog.  Reads previous settings from
    config.toml and writes them back on confirmation so the next launch
    is pre-populated automatically.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STORM")
        self.setFixedWidth(380)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(_DIALOG_STYLE)

        s = QSettings()
        saved = {
            "vehicle_id":     s.value("launch/vehicle_id",     "",        type=str),
            "data_dir":       s.value("launch/data_dir",       "",        type=str),
            "mode":           s.value("launch/mode",           "vehicle", type=str),
            "vehicle_icon":   s.value("launch/vehicle_icon",   "car",     type=str),
            "auto_spc":       s.value("launch/auto_spc",       False,     type=bool),
            "auto_nws":       s.value("launch/auto_nws",       False,     type=bool),
            "auto_radar":     s.value("launch/auto_radar",     False,     type=bool),
            "auto_satellite": s.value("launch/auto_satellite", "",        type=str),
            "auto_obs_ok":    s.value("launch/auto_obs_ok",    False,     type=bool),
            "auto_obs_wtm":   s.value("launch/auto_obs_wtm",   False,     type=bool),
            "auto_obs_ks":    s.value("launch/auto_obs_ks",    False,     type=bool),
        }
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._build_ui(saved)
        self._start_update_check()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self, saved: dict):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 28)
        root.setSpacing(0)

        # Title
        title = QLabel("STORM")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        sub = QLabel("Severe Thunderstorm Observation and Reconnaissance Monitor")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(16)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: #1E1E2E;")
        div.setFixedHeight(1)
        root.addWidget(div)
        root.addSpacing(16)

        # ── Vehicle-only section (hidden in monitor/viewer mode) ───────────────
        self._vehicle_section = QWidget()
        vs = QVBoxLayout(self._vehicle_section)
        vs.setContentsMargins(0, 0, 0, 0)
        vs.setSpacing(0)

        # Vehicle ID
        vid_label = QLabel("VEHICLE ID")
        vid_label.setObjectName("fieldLabel")
        vs.addWidget(vid_label)
        vs.addSpacing(6)

        vid_row = QHBoxLayout()
        vid_row.setSpacing(6)
        self._vid_input = QLineEdit(saved.get("vehicle_id", ""))
        self._vid_input.setPlaceholderText("e.g.  lid1")
        vid_row.addWidget(self._vid_input)

        self._lock_btn = QPushButton("🔒")
        self._lock_btn.setObjectName("lockBtn")
        self._lock_btn.setFixedWidth(36)
        self._lock_btn.setToolTip("Unlock both fields")
        self._lock_btn.clicked.connect(self._toggle_fields_lock)
        vid_row.addWidget(self._lock_btn)
        vs.addLayout(vid_row)
        vs.addSpacing(14)

        # Data directory
        dir_label = QLabel("DATA DIRECTORY")
        dir_label.setObjectName("fieldLabel")
        vs.addWidget(dir_label)
        vs.addSpacing(6)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self._dir_input = QLineEdit(saved.get("data_dir", ""))
        self._dir_input.setPlaceholderText("Leave blank for GPS puck")
        dir_row.addWidget(self._dir_input)

        self._browse_btn = QPushButton("…")
        self._browse_btn.setObjectName("browseBtn")
        self._browse_btn.setFixedWidth(36)
        self._browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._browse_btn)

        vs.addLayout(dir_row)
        vs.addSpacing(14)

        # Vehicle icon picker
        icon_label = QLabel("VEHICLE ICON")
        icon_label.setObjectName("fieldLabel")
        vs.addWidget(icon_label)
        vs.addSpacing(6)

        icon_row = QHBoxLayout()
        icon_row.setSpacing(4)
        self._icon_btns: dict[str, QToolButton] = {}
        _icons = [
            ("car", "CAR"), ("drone", "DRONE"), ("mesonet", "MESO"),
            ("lidar", "LIDAR"), ("radar", "RADAR"), ("hailcam", "HAIL"),
        ]
        for key, label in _icons:
            btn = QToolButton()
            btn.setText(label)
            btn.setObjectName("iconBtn")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(22, 22))
            btn.setIcon(QIcon(_svg_pixmap(key, "#5A5B6A", 22)))
            btn.clicked.connect(lambda _checked, k=key: self._select_icon(k))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            icon_row.addWidget(btn)
            self._icon_btns[key] = btn
        vs.addLayout(icon_row)
        vs.addSpacing(12)

        root.addWidget(self._vehicle_section)

        self._set_icon_selected(saved.get("vehicle_icon", "car"))

        # Single lock controls all vehicle config fields; lock when either value exists.
        self._set_fields_locked(bool(saved.get("vehicle_id") or saved.get("data_dir")))

        # ── Mode selector ──────────────────────────────────────────────────────
        mode_label = QLabel("LAUNCH MODE")
        mode_label.setObjectName("fieldLabel")
        root.addWidget(mode_label)
        root.addSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self._mode_btns: dict[str, QPushButton] = {}
        for key, label in (("vehicle", "VEHICLE"), ("monitor", "MONITOR"), ("viewer", "VIEWER")):
            btn = QPushButton(label)
            btn.setStyleSheet(_MODE_BTN_STYLE)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._select_mode(k))
            mode_row.addWidget(btn)
            self._mode_btns[key] = btn
        root.addLayout(mode_row)
        root.addSpacing(12)

        # Passphrase row (hidden in viewer mode)
        self._passphrase_row = QWidget()
        pw_layout = QVBoxLayout(self._passphrase_row)
        pw_layout.setContentsMargins(0, 0, 0, 0)
        pw_layout.setSpacing(6)
        self._passphrase_label = QLabel("PASSPHRASE")
        self._passphrase_label.setObjectName("fieldLabel")
        pw_layout.addWidget(self._passphrase_label)
        self._passphrase_input = QLineEdit()
        self._passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._passphrase_input.setPlaceholderText("Enter passphrase")
        pw_layout.addWidget(self._passphrase_input)
        root.addWidget(self._passphrase_row)

        # Apply saved mode
        self._select_mode(saved.get("mode", "vehicle"))

        # ── Data on launch (collapsible) ───────────────────────────────────────
        root.addSpacing(14)
        div2 = QFrame()
        div2.setObjectName("divider")
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet("background-color: #1E1E2E;")
        div2.setFixedHeight(1)
        root.addWidget(div2)
        root.addSpacing(10)

        self._data_toggle_btn = QPushButton("▸  DATA ON LAUNCH")
        self._data_toggle_btn.setObjectName("dataToggleBtn")
        self._data_toggle_btn.setFixedHeight(18)
        self._data_toggle_btn.clicked.connect(self._toggle_data_section)
        root.addWidget(self._data_toggle_btn)

        # Collapsible container — hidden by default
        self._data_section = QWidget()
        ds = QVBoxLayout(self._data_section)
        ds.setContentsMargins(0, 8, 0, 0)
        ds.setSpacing(6)

        # Row 1: SPC / NWS / RADAR as multi-select button strip
        self._layer_btns: dict[str, QPushButton] = {}
        layer_row = QHBoxLayout()
        layer_row.setSpacing(6)
        for key, label in (("spc", "SPC"), ("nws", "NWS"), ("radar", "RADAR")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._toggle_layer(k))
            layer_row.addWidget(btn)
            self._layer_btns[key] = btn
        ds.addLayout(layer_row)

        # Row 2: satellite selector (exclusive)
        sat_row = QHBoxLayout()
        sat_row.setSpacing(6)
        sat_lbl = QLabel("SAT")
        sat_lbl.setObjectName("fieldLabel")
        sat_lbl.setFixedWidth(28)
        sat_row.addWidget(sat_lbl)
        self._sat_btns: dict[str, QPushButton] = {}
        for key, label in (("", "OFF"), ("conus", "CONUS"), ("auto_meso", "AUTO-MESO")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._select_satellite(k))
            sat_row.addWidget(btn)
            self._sat_btns[key] = btn
        ds.addLayout(sat_row)

        # Row 3: surface obs multi-select
        obs_row = QHBoxLayout()
        obs_row.setSpacing(6)
        obs_lbl = QLabel("OBS")
        obs_lbl.setObjectName("fieldLabel")
        obs_lbl.setFixedWidth(28)
        obs_row.addWidget(obs_lbl)
        self._obs_btns: dict[str, QPushButton] = {}
        for key, label in (("ok", "OK MESO"), ("wtm", "WTM"), ("ks", "KS MESO")):
            btn = QPushButton(label)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked, k=key: self._toggle_obs(k))
            obs_row.addWidget(btn)
            self._obs_btns[key] = btn
        ds.addLayout(obs_row)

        self._data_section.setVisible(False)
        root.addWidget(self._data_section)

        # Initialise multi-select state from saved settings
        self._selected_layers: set[str] = set()
        for key, setting in (("spc", "auto_spc"), ("nws", "auto_nws"), ("radar", "auto_radar")):
            if saved.get(setting, False):
                self._selected_layers.add(key)
        self._refresh_layer_styles()

        self._select_satellite(saved.get("auto_satellite", ""))

        self._selected_obs: set[str] = set()
        for key in ("ok", "wtm", "ks"):
            if saved.get(f"auto_obs_{key}", False):
                self._selected_obs.add(key)
        self._refresh_obs_styles()

        # Auto-expand if any data pref is set
        any_data = (
            bool(self._selected_layers)
            or bool(self._selected_satellite)
            or bool(self._selected_obs)
        )
        if any_data:
            self._toggle_data_section()

        root.addSpacing(20)

        # Launch button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        launch = QPushButton("LAUNCH STORM")
        launch.setObjectName("launchBtn")
        launch.clicked.connect(self._on_launch)
        launch.setDefault(True)
        self._launch_btn = launch
        btn_row.addWidget(launch)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # Update button (below launch, centered, smaller)
        root.addSpacing(10)
        upd_row = QHBoxLayout()
        upd_row.addStretch()
        self._update_btn = QPushButton("CHECKING FOR UPDATES...")
        self._update_btn.setEnabled(False)
        self._update_btn.setStyleSheet(_UPD_CHECKING)
        self._update_btn.clicked.connect(self._on_update_clicked)
        upd_row.addWidget(self._update_btn)
        upd_row.addStretch()
        root.addLayout(upd_row)

        # Log viewer links (very subtle, bottom of dialog)
        root.addSpacing(4)
        log_row = QHBoxLayout()
        log_row.addStretch()
        self._crash_log_btn = QPushButton("VIEW CRASH LOG")
        self._crash_log_btn.setStyleSheet(_LOG_BTN_STYLE)
        self._crash_log_btn.clicked.connect(self._on_view_crash_log_clicked)
        log_row.addWidget(self._crash_log_btn)
        log_row.addStretch()
        root.addLayout(log_row)

        # Defer adjustSize until the event loop starts so the full layout is
        # computed before the dialog is measured for the first time.
        QTimer.singleShot(0, self._post_layout_adjust)

    # ── Layout helpers ─────────────────────────────────────────────────────────

    def _post_layout_adjust(self):
        """Resize to content then re-center within the available screen area."""
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + max(0, (screen.width()  - self.width())  // 2)
        y = screen.y() + max(0, (screen.height() - self.height()) // 2)
        self.move(x, y)

    # ── Lock helpers ───────────────────────────────────────────────────────────

    def _set_fields_locked(self, locked: bool):
        self._vid_input.setReadOnly(locked)
        self._dir_input.setReadOnly(locked)
        self._browse_btn.setEnabled(not locked)
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._lock_btn.setToolTip("Unlock all fields" if locked else "Lock all fields")
        for btn in self._icon_btns.values():
            btn.setEnabled(not locked)

    def _toggle_fields_lock(self):
        self._set_fields_locked(not self._vid_input.isReadOnly())

    def _select_mode(self, mode: str):
        self._selected_mode = mode
        for key, btn in self._mode_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if key == mode else _MODE_BTN_STYLE)
        vehicle_mode = (mode == "vehicle")
        viewer_mode = (mode == "viewer")
        self._vehicle_section.setVisible(vehicle_mode)
        self._passphrase_row.setVisible(not viewer_mode)
        if not viewer_mode:
            lbl = "VEHICLE PASSPHRASE" if vehicle_mode else "MONITOR PASSPHRASE"
            self._passphrase_label.setText(lbl)
        self._passphrase_input.clear()
        if self.isVisible():
            self._post_layout_adjust()

    def _toggle_data_section(self):
        visible = not self._data_section.isVisible()
        self._data_section.setVisible(visible)
        self._data_toggle_btn.setText("▾  DATA ON LAUNCH" if visible else "▸  DATA ON LAUNCH")
        QTimer.singleShot(0, self.adjustSize)

    def _toggle_layer(self, key: str):
        self._selected_layers.discard(key) if key in self._selected_layers else self._selected_layers.add(key)
        self._refresh_layer_styles()

    def _refresh_layer_styles(self):
        for k, btn in self._layer_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k in self._selected_layers else _MODE_BTN_STYLE)

    def _select_satellite(self, key: str):
        self._selected_satellite = key if key in self._sat_btns else ""
        for k, btn in self._sat_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k == self._selected_satellite else _MODE_BTN_STYLE)

    def _toggle_obs(self, key: str):
        self._selected_obs.discard(key) if key in self._selected_obs else self._selected_obs.add(key)
        self._refresh_obs_styles()

    def _refresh_obs_styles(self):
        for k, btn in self._obs_btns.items():
            btn.setStyleSheet(_MODE_BTN_SELECTED_STYLE if k in self._selected_obs else _MODE_BTN_STYLE)

    def _select_icon(self, key: str):
        self._set_icon_selected(key)

    def _set_icon_selected(self, key: str):
        self._selected_icon = key if key in self._icon_btns else "car"
        for k, btn in self._icon_btns.items():
            selected = (k == self._selected_icon)
            btn.setStyleSheet(_ICON_SELECTED_STYLE if selected else "")
            btn.setIcon(QIcon(_svg_pixmap(k, "#00CFFF" if selected else "#5A5B6A", 22)))

    # ── Update check ───────────────────────────────────────────────────────────

    def _start_update_check(self):
        self._worker = _UpdateWorker()
        self._worker.check_done.connect(self._on_check_done)
        self._worker.pull_done.connect(self._on_pull_done)
        self._worker.start_check()

    def _on_check_done(self, commits_behind: int):
        if commits_behind == -3:
            self._update_btn.setText("⚠   NOT A GIT INSTALL — CLONE REPO FOR IN-APP UPDATES")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif commits_behind == -2:
            self._update_btn.setText("DEV BUILD")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        elif commits_behind < 0:
            self._update_btn.setText("⚠   UPDATE CHECK FAILED — PROCEED AND TRY AGAIN LATER")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif commits_behind == 0:
            self._update_btn.setText("✓   UP TO DATE")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        else:
            n = commits_behind
            label = "UPDATE AVAILABLE — CLICK TO UPDATE"
            self._update_btn.setText(label)
            self._update_btn.setStyleSheet(_UPD_AVAILABLE)
            self._update_btn.setEnabled(True)

    def _on_update_clicked(self):
        self._update_btn.setEnabled(False)
        self._update_btn.setText("UPDATING...")
        self._update_btn.setStyleSheet(_UPD_CHECKING)
        self._worker.start_pull()

    def _on_pull_done(self, success: bool, deps_changed: bool):
        if success and deps_changed:
            self._update_btn.setText("⚠   DEPS CHANGED — RUN conda env update THEN RESTART")
            self._update_btn.setStyleSheet(_UPD_WARNING)
        elif success:
            self._update_btn.setText("✓   UPDATED — RESTARTING...")
            self._update_btn.setStyleSheet(_UPD_SUCCESS)
            QTimer.singleShot(800, self._restart_app)
        else:
            self._update_btn.setText("UPDATE FAILED")
            self._update_btn.setStyleSheet(_UPD_ERROR)

    def _restart_app(self):
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _on_view_crash_log_clicked(self):
        log_path = os.path.join(self._project_root, "storm_fault.log")
        dlg = _LogViewerDialog(log_path, parent=self)
        dlg.exec()

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _browse_dir(self):
        current = self._dir_input.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Select data directory", current
        )
        if chosen:
            self._dir_input.setText(chosen)

    def _on_launch(self):
        mode = getattr(self, "_selected_mode", "vehicle")

        # Validate passphrase for vehicle and monitor modes
        if mode != "viewer":
            passphrase = self._passphrase_input.text()
            entered_hash = hashlib.sha256(passphrase.encode()).hexdigest()
            expected_hash = (
                _config.VEHICLE_PASSPHRASE_HASH if mode == "vehicle"
                else _config.MONITOR_PASSPHRASE_HASH
            )
            if entered_hash != expected_hash:
                QMessageBox.warning(
                    self,
                    "Incorrect Passphrase",
                    "The passphrase you entered is incorrect.",
                )
                self._passphrase_input.clear()
                self._passphrase_input.setFocus()
                return

        # Vehicle ID is required in vehicle mode
        if mode == "vehicle":
            vid = self._vid_input.text().strip()
            if not vid:
                QMessageBox.warning(
                    self,
                    "Vehicle ID Required",
                    "Please enter a vehicle ID before launching.",
                )
                self._vid_input.setFocus()
                return

        self._do_accept()

    def _do_accept(self):
        s = QSettings()
        s.setValue("launch/vehicle_id",     self._vid_input.text().strip())
        s.setValue("launch/data_dir",       self._dir_input.text().strip())
        s.setValue("launch/mode",           getattr(self, "_selected_mode", "vehicle"))
        s.setValue("launch/vehicle_icon",   getattr(self, "_selected_icon", "car"))
        layers = getattr(self, "_selected_layers", set())
        s.setValue("launch/auto_spc",   "spc"   in layers)
        s.setValue("launch/auto_nws",   "nws"   in layers)
        s.setValue("launch/auto_radar", "radar" in layers)
        s.setValue("launch/auto_satellite", getattr(self, "_selected_satellite", ""))
        obs = getattr(self, "_selected_obs", set())
        s.setValue("launch/auto_obs_ok",  "ok"  in obs)
        s.setValue("launch/auto_obs_wtm", "wtm" in obs)
        s.setValue("launch/auto_obs_ks",  "ks"  in obs)
        self.accept()

    # ── Accessors (read by main.py after accept) ───────────────────────────────

    def vehicle_id(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._vid_input.text().strip()

    def data_dir(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return ""
        return self._dir_input.text().strip()

    def monitor(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "monitor"

    def viewer(self) -> bool:
        return getattr(self, "_selected_mode", "vehicle") == "viewer"

    def vehicle_icon(self) -> str:
        if getattr(self, "_selected_mode", "vehicle") != "vehicle":
            return "car"
        return getattr(self, "_selected_icon", "car")

    def auto_spc(self) -> bool:
        return "spc" in getattr(self, "_selected_layers", set())

    def auto_nws(self) -> bool:
        return "nws" in getattr(self, "_selected_layers", set())

    def auto_radar(self) -> bool:
        return "radar" in getattr(self, "_selected_layers", set())

    def auto_satellite(self) -> str:
        return getattr(self, "_selected_satellite", "")

    def auto_obs_ok(self) -> bool:
        return "ok" in getattr(self, "_selected_obs", set())

    def auto_obs_wtm(self) -> bool:
        return "wtm" in getattr(self, "_selected_obs", set())

    def auto_obs_ks(self) -> bool:
        return "ks" in getattr(self, "_selected_obs", set())
