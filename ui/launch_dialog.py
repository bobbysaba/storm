# ui/launch_dialog.py
# Startup dialog — shown on every launch to confirm vehicle ID and data
# directory.  Persists settings via QSettings so they survive across sessions.

import os
import sys
import hashlib
import threading
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QFileDialog, QFrame,
    QTextEdit, QApplication,
)
from PyQt6.QtCore import Qt, QSettings, QObject, QTimer, pyqtSignal

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


class _UpdateWorker(QObject):
    """Runs git operations on a daemon thread and signals results back."""

    check_done = pyqtSignal(int)         # commits behind origin/main; -1 = error
    pull_done  = pyqtSignal(bool, bool)  # success, deps_changed

    def __init__(self):
        super().__init__()
        self._root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def start_check(self):
        threading.Thread(target=self._do_check, daemon=True).start()

    def start_pull(self):
        threading.Thread(target=self._do_pull, daemon=True).start()

    def _do_check(self):
        try:
            subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=self._root, timeout=10,
                capture_output=True, check=True,
            )
            # Local uncommitted changes?
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            # Local commits not yet pushed?
            ahead = subprocess.run(
                ["git", "rev-list", "origin/main..HEAD", "--count"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if dirty or int(ahead) > 0:
                self.check_done.emit(-2)  # dev build
                return
            r = subprocess.run(
                ["git", "rev-list", "HEAD..origin/main", "--count"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True,
            )
            self.check_done.emit(int(r.stdout.strip()))
        except Exception:
            self.check_done.emit(-1)

    def _env_hash(self) -> str:
        """SHA-256 of the platform-appropriate conda env file, or '' on error."""
        fname = "storm_windows.yml" if sys.platform == "win32" else "storm_mac.yml"
        path = os.path.join(self._root, "envs", fname)
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""

    def _do_pull(self):
        try:
            hash_before = self._env_hash()
            subprocess.run(
                ["git", "pull"],
                cwd=self._root, timeout=30,
                capture_output=True, check=True,
            )
            hash_after = self._env_hash()
            deps_changed = bool(hash_before) and hash_before != hash_after
            self.pull_done.emit(True, deps_changed)
        except Exception:
            self.pull_done.emit(False, False)


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
            "vehicle_id":  s.value("launch/vehicle_id",  "",    type=str),
            "data_dir":    s.value("launch/data_dir",    "",    type=str),
            "monitor_mode": s.value("launch/monitor_mode", False, type=bool),
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
        root.addLayout(vid_row)
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

        self._browse_btn = QPushButton("…")
        self._browse_btn.setObjectName("browseBtn")
        self._browse_btn.setFixedWidth(36)
        self._browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(self._browse_btn)

        root.addLayout(dir_row)

        hint = QLabel(
            "Leave blank if no mesonet instrument is connected — "
            "a GPS puck will be used instead."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addSpacing(20)

        # Single lock controls both fields; lock when either value exists.
        self._set_fields_locked(bool(saved.get("vehicle_id") or saved.get("data_dir")))

        # Monitor mode
        self._monitor_cb = QCheckBox("Monitor mode — no local data")
        self._monitor_cb.setChecked(bool(saved.get("monitor_mode", False)))
        self._monitor_cb.stateChanged.connect(self._on_monitor_toggled)
        root.addWidget(self._monitor_cb)

        # Apply initial monitor state
        if self._monitor_cb.isChecked():
            self._set_fields_monitor_disabled(True)
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

    # ── Lock helpers ───────────────────────────────────────────────────────────

    def _set_fields_locked(self, locked: bool):
        self._vid_input.setReadOnly(locked)
        self._dir_input.setReadOnly(locked)
        self._browse_btn.setEnabled(not locked)
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._lock_btn.setToolTip("Unlock both fields" if locked else "Lock both fields")

    def _toggle_fields_lock(self):
        self._set_fields_locked(not self._vid_input.isReadOnly())

    def _set_fields_monitor_disabled(self, disabled: bool):
        for w in (self._vid_input, self._lock_btn, self._dir_input, self._browse_btn):
            w.setEnabled(not disabled)

    def _on_monitor_toggled(self):
        self._set_fields_monitor_disabled(self._monitor_cb.isChecked())

    # ── Update check ───────────────────────────────────────────────────────────

    def _start_update_check(self):
        self._worker = _UpdateWorker()
        self._worker.check_done.connect(self._on_check_done)
        self._worker.pull_done.connect(self._on_pull_done)
        self._worker.start_check()

    def _on_check_done(self, commits_behind: int):
        if commits_behind == -2:
            self._update_btn.setText("DEV BUILD")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        elif commits_behind < 0:
            self._update_btn.setText("COULD NOT CHECK FOR UPDATES")
            self._update_btn.setStyleSheet(_UPD_ERROR)
        elif commits_behind == 0:
            self._update_btn.setText("✓   UP TO DATE")
            self._update_btn.setStyleSheet(_UPD_CURRENT)
        else:
            n = commits_behind
            label = f"{n} UPDATE{'S' if n > 1 else ''} AVAILABLE — CLICK TO UPDATE"
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
        s = QSettings()
        s.setValue("launch/vehicle_id",   self._vid_input.text().strip())
        s.setValue("launch/data_dir",     self._dir_input.text().strip())
        s.setValue("launch/monitor_mode", self._monitor_cb.isChecked())
        self.accept()

    # ── Accessors (read by main.py after accept) ───────────────────────────────

    def vehicle_id(self) -> str:
        if self._monitor_cb.isChecked():
            return ""
        return self._vid_input.text().strip()

    def data_dir(self) -> str:
        if self._monitor_cb.isChecked():
            return ""
        return self._dir_input.text().strip()

    def monitor(self) -> bool:
        return self._monitor_cb.isChecked()
