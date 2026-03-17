import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QTextEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QFont


def _reflow(text: str) -> str:
    """Reflow pre-formatted NWS/SPC text for display at arbitrary width.

    Processes line by line:
    - Blank lines flush the current prose buffer as a paragraph.
    - Indented lines and NWS structural tokens (lines starting with . / &&)
      are emitted as-is after flushing any buffered prose.
    - All other lines are accumulated and joined with spaces so the widget
      can word-wrap at its own width without double-wrapping.
    """
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    out: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            out.append(' '.join(buf))
            buf.clear()

    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            flush()
            out.append('')
        elif line[:1] in (' ', '\t') or stripped.startswith(('.', '/')) or stripped == '&&':
            flush()
            out.append(line.rstrip())
        else:
            buf.append(stripped)

    flush()

    # Collapse runs of more than one blank line
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(out))
    return result.strip()


class OutlookPanel(QWidget):
    closed = pyqtSignal()

    PANEL_WIDTH = 340

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("outlookPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMaximumWidth(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        # Title row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        self._title_label = QLabel("DAY 1 CONVECTIVE OUTLOOK")
        self._title_label.setObjectName("outlookPanelTitle")
        title_row.addWidget(self._title_label)
        title_row.addStretch()

        self._close_btn = QToolButton()
        self._close_btn.setText("×")
        self._close_btn.setObjectName("outlookPanelClose")
        self._close_btn.clicked.connect(self.close_panel)
        title_row.addWidget(self._close_btn)

        layout.addLayout(title_row)

        # Tab row — only shown when multiple products are loaded
        self._tab_row = QWidget()
        self._tab_row.setObjectName("outlookTabRow")
        self._tab_layout = QHBoxLayout(self._tab_row)
        self._tab_layout.setContentsMargins(0, 0, 0, 0)
        self._tab_layout.setSpacing(4)
        layout.addWidget(self._tab_row)
        self._tab_row.hide()

        # Text area
        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("outlookPanelText")
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Courier New", 11))
        self._text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._text_edit)

        # Internal state: title → text content
        self._tabs: dict[str, str] = {}
        self._active_tab: str | None = None
        self._tab_buttons: dict[str, QToolButton] = {}
        self._animation = None

    def show_loading(self, titles: list[str]):
        """Set up the panel for one or more products, all starting as 'Loading…'."""
        self._tabs = {t: "Loading…" for t in titles}
        self._active_tab = titles[0]
        self._rebuild_tabs()
        self._title_label.setText(titles[0])
        self._text_edit.setPlainText("Loading…")
        if self.maximumWidth() == 0:
            self._open_animated()

    def show_text(self, title: str, text: str):
        """Update the text for a specific product tab."""
        if title not in self._tabs:
            return
        self._tabs[title] = text
        if title == self._active_tab:
            self._text_edit.setPlainText(_reflow(text))

    def close_panel(self):
        self._close_animated()

    def is_open(self) -> bool:
        return self.maximumWidth() != 0

    # ── Tab helpers ───────────────────────────────────────────────────────────

    def _short_title(self, title: str) -> str:
        t = title.upper()
        if "CONVECTIVE OUTLOOK" in t:
            return "Day 1"
        if "MESOSCALE DISCUSSION" in t:
            return "MD " + title.split()[-1]
        if "TORNADO WATCH" in t:
            return "Tor Watch " + title.split()[-1]
        if "SEVERE THUNDERSTORM WATCH" in t:
            return "SVR Watch " + title.split()[-1]
        if "WARNING" in t or "Warning" in title:
            dash = title.find("—")
            if dash != -1:
                return title[dash + 1:].strip()
            return title[:14]
        return title[:14]

    def _rebuild_tabs(self):
        while self._tab_layout.count():
            item = self._tab_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tab_buttons.clear()

        if len(self._tabs) <= 1:
            self._tab_row.hide()
            return

        self._tab_row.show()
        for title in self._tabs:
            btn = QToolButton()
            btn.setText(self._short_title(title))
            btn.setObjectName("outlookTabBtn")
            btn.setCheckable(True)
            btn.setChecked(title == self._active_tab)
            btn.clicked.connect(lambda _checked, t=title: self._switch_tab(t))
            self._tab_layout.addWidget(btn)
            self._tab_buttons[title] = btn
        self._tab_layout.addStretch()

    def _switch_tab(self, title: str):
        self._active_tab = title
        self._title_label.setText(title)
        self._text_edit.setPlainText(_reflow(self._tabs.get(title, "Loading…")))
        for t, btn in self._tab_buttons.items():
            btn.setChecked(t == title)

    # ── Animation ─────────────────────────────────────────────────────────────

    def _open_animated(self):
        self._animation = QPropertyAnimation(self, b"maximumWidth")
        self._animation.setDuration(200)
        self._animation.setStartValue(0)
        self._animation.setEndValue(self.PANEL_WIDTH)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.finished.connect(lambda: self.setMaximumWidth(16777215))
        self._animation.start()

    def _close_animated(self):
        self._animation = QPropertyAnimation(self, b"maximumWidth")
        self._animation.setDuration(200)
        self._animation.setStartValue(self.width())
        self._animation.setEndValue(0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.finished.connect(self.closed.emit)
        self._animation.start()
