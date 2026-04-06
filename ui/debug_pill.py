# ui/debug_pill.py
# Floating debug pill — collapses to a small toggle button at the bottom of the
# screen, expands upward to reveal STATUS and LOG tabs.
# Mirrors the LayerOrderPill expand-upward pattern.

import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QToolButton,
    QTabWidget, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QFont


class DebugPill(QWidget):
    """Floating debug pill.  Collapsed by default; expands upward on click.

    Signals:
        size_changed — emitted whenever the pill resizes so _layout_overlays
                       can reposition it.
    """

    size_changed = pyqtSignal()

    # Fixed expanded dimensions
    PANEL_W = 560
    PANEL_H = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("debugPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._expanded = False

        # ── Toggle button (always visible) ────────────────────────────────
        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setStyleSheet(
            "QToolButton {"
            "  background: rgba(15,15,26,0.95);"
            "  border: 1px solid rgba(57,217,138,0.45);"
            "  border-radius: 8px;"
            "  color: #39D98A;"
            "  font-size: 10px;"
            "  font-weight: 600;"
            "  letter-spacing: 0.8px;"
            "  padding: 5px 12px;"
            "}"
            "QToolButton:hover { border-color: #39D98A; color: #5CFFA8; }"
        )
        self._toggle_btn.clicked.connect(self._toggle_expanded)
        self._refresh_label()

        # ── Expanded panel (hidden by default, grows upward) ──────────────
        self._panel = QWidget(self)
        self._panel.setObjectName("debugPillPanel")
        self._panel.setStyleSheet(
            "#debugPillPanel {"
            "  background: rgba(12,13,22,0.97);"
            "  border: 1px solid rgba(74,83,108,0.55);"
            "  border-radius: 10px;"
            "}"
        )

        panel_vbox = QVBoxLayout(self._panel)
        panel_vbox.setContentsMargins(0, 4, 0, 0)
        panel_vbox.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("debugTabs")
        self._tabs.setStyleSheet("""
            QTabWidget#debugTabs::pane {
                border: none;
                background: transparent;
            }
            QTabWidget#debugTabs QTabBar {
                background: transparent;
                border-bottom: 1px solid rgba(74,83,108,0.3);
            }
            QTabWidget#debugTabs QTabBar::tab {
                background: transparent;
                color: #4A5068;
                padding: 5px 14px;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.8px;
                border: none;
                border-bottom: 2px solid transparent;
                margin-right: 2px;
            }
            QTabWidget#debugTabs QTabBar::tab:hover {
                color: #8A93A8;
            }
            QTabWidget#debugTabs QTabBar::tab:selected {
                color: #39D98A;
                border-bottom: 2px solid #39D98A;
            }
        """)

        # STATUS tab ───────────────────────────────────────────────────────
        status_wrap = QWidget()
        status_wrap.setStyleSheet("background: transparent;")
        status_vbox = QVBoxLayout(status_wrap)
        status_vbox.setContentsMargins(10, 8, 10, 8)
        status_vbox.setSpacing(0)

        self._status_label = QLabel("initializing…")
        self._status_label.setFont(QFont("Courier New", 9))
        self._status_label.setStyleSheet("color: #39D98A; background: transparent;")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        status_vbox.addWidget(self._status_label)
        status_vbox.addStretch()
        self._tabs.addTab(status_wrap, "STATUS")

        # LOG tab ──────────────────────────────────────────────────────────
        log_wrap = QWidget()
        log_wrap.setStyleSheet("background: transparent;")
        log_vbox = QVBoxLayout(log_wrap)
        log_vbox.setContentsMargins(10, 8, 10, 8)
        log_vbox.setSpacing(0)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Courier New", 9))
        self._log_view.setStyleSheet(
            "QPlainTextEdit {"
            "  color: #C8D0DE;"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "}"
        )
        self._log_view.setMaximumBlockCount(2000)
        log_vbox.addWidget(self._log_view)
        self._tabs.addTab(log_wrap, "LOG")

        panel_vbox.addWidget(self._tabs)
        self._panel.hide()

        # ── Logging handler ───────────────────────────────────────────────
        # Use a signal to marshal log records onto the main thread — calling
        # Qt widget methods directly from background threads causes segfaults.
        _widget = self._log_view

        class _Bridge(QObject):
            message = pyqtSignal(str)

        self._log_bridge = _Bridge()
        self._log_bridge.message.connect(
            lambda msg: (
                _widget.appendPlainText(msg),
                _widget.verticalScrollBar().setValue(_widget.verticalScrollBar().maximum()),
            )
        )

        _bridge = self._log_bridge

        class _QtLogHandler(logging.Handler):
            def emit(self_, record):
                try:
                    _bridge.message.emit(self_.format(record))
                except Exception:
                    pass

        self._log_handler = _QtLogHandler()
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        )
        root = logging.getLogger()
        self._pre_log_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self._log_handler)

        self._relayout()

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _relayout(self):
        """Size and position children; resize the pill itself."""
        self._toggle_btn.adjustSize()
        btn_w = self._toggle_btn.sizeHint().width()
        btn_h = self._toggle_btn.sizeHint().height()

        GAP = 4

        if self._expanded:
            total_w = max(btn_w, self.PANEL_W)
            total_h = self.PANEL_H + GAP + btn_h
            # Panel grows upward from the toggle button
            self._panel.setGeometry(0, 0, total_w, self.PANEL_H)
            # Toggle button is centered under the panel
            btn_x = (total_w - btn_w) // 2
            self._toggle_btn.setGeometry(btn_x, self.PANEL_H + GAP, btn_w, btn_h)
        else:
            total_w = btn_w
            total_h = btn_h
            self._toggle_btn.setGeometry(0, 0, btn_w, btn_h)

        prev = self.size()
        self.setFixedSize(total_w, total_h)
        if self.width() != prev.width() or self.height() != prev.height():
            self.size_changed.emit()

    # ── Interaction ───────────────────────────────────────────────────────────

    def _toggle_expanded(self):
        self._expanded = not self._expanded
        self._refresh_label()
        if self._expanded:
            self._panel.show()
        else:
            self._panel.hide()
        self._relayout()

    def _refresh_label(self):
        arrow = "▲" if self._expanded else "▼"
        self._toggle_btn.setText(f"DEBUG  {arrow}")
        self._toggle_btn.adjustSize()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_status(self, text: str):
        """Update the STATUS tab content."""
        self._status_label.setText(text)

    def toggle(self):
        """Programmatically toggle expand/collapse (Ctrl+D shortcut)."""
        self._toggle_expanded()

    def cleanup(self):
        """Remove the log handler and restore the root logger level."""
        logging.getLogger().removeHandler(self._log_handler)
        logging.getLogger().setLevel(self._pre_log_level)
