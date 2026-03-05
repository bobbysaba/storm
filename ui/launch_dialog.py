# ui/launch_dialog.py
# Startup dialog — shown on every launch to confirm vehicle ID and data
# directory.  Persists settings via QSettings so they survive across sessions.

from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QFileDialog, QFrame,
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QFont

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
QCheckBox {
    color: #8E97AB;
    font-size: 11px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: none;
    background: transparent;
    image: url(static/indicator_off.svg);
}
QCheckBox::indicator:checked {
    image: url(static/indicator_on.svg);
}
QFrame#divider {
    color: #1E1E2E;
}
"""


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
            "vehicle_id":  s.value("launch/vehicle_id",  "",    type=str),
            "data_dir":    s.value("launch/data_dir",    "",    type=str),
            "monitor_mode": s.value("launch/monitor_mode", False, type=bool),
        }
        self._build_ui(saved)

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
        root.addSpacing(24)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: #1E1E2E;")
        div.setFixedHeight(1)
        root.addWidget(div)
        root.addSpacing(24)

        # Vehicle ID
        vid_label = QLabel("VEHICLE ID")
        vid_label.setObjectName("fieldLabel")
        root.addWidget(vid_label)
        root.addSpacing(6)

        self._vid_input = QLineEdit(saved.get("vehicle_id", ""))
        self._vid_input.setPlaceholderText("e.g.  lid1")
        root.addWidget(self._vid_input)
        root.addSpacing(20)

        # Data directory
        dir_label = QLabel("DATA DIRECTORY")
        dir_label.setObjectName("fieldLabel")
        root.addWidget(dir_label)
        root.addSpacing(6)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self._dir_input = QLineEdit(saved.get("data_dir", ""))
        self._dir_input.setPlaceholderText("Leave blank for GPS puck")
        dir_row.addWidget(self._dir_input)

        browse = QPushButton("…")
        browse.setObjectName("browseBtn")
        browse.setFixedWidth(36)
        browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse)
        root.addLayout(dir_row)

        hint = QLabel(
            "Leave blank if no mesonet instrument is connected — "
            "a GPS puck will be used instead."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addSpacing(20)

        # Monitor mode
        self._monitor_cb = QCheckBox("Monitor mode — no local data")
        self._monitor_cb.setChecked(bool(saved.get("monitor_mode", False)))
        root.addWidget(self._monitor_cb)
        root.addSpacing(28)

        # Launch button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        launch = QPushButton("LAUNCH STORM")
        launch.setObjectName("launchBtn")
        launch.clicked.connect(self._on_launch)
        launch.setDefault(True)
        btn_row.addWidget(launch)
        btn_row.addStretch()
        root.addLayout(btn_row)

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _browse_dir(self):
        current = self._dir_input.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Select data directory", current
        )
        if chosen:
            self._dir_input.setText(chosen)

    def _on_launch(self):
        s = QSettings()
        s.setValue("launch/vehicle_id",   self._vid_input.text().strip())
        s.setValue("launch/data_dir",     self._dir_input.text().strip())
        s.setValue("launch/monitor_mode", self._monitor_cb.isChecked())
        self.accept()

    # ── Accessors (read by main.py after accept) ───────────────────────────────

    def vehicle_id(self) -> str:
        return self._vid_input.text().strip()

    def data_dir(self) -> str:
        return self._dir_input.text().strip()

    def monitor(self) -> bool:
        return self._monitor_cb.isChecked()


