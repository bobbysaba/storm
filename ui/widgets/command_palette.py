"""⌘K / Ctrl+K: a search box over the map that runs any command.

Type to filter, ↑/↓ to move, Enter to run, Esc to close. Clicking a row runs it,
and clicking anywhere else closes the palette. The list comes from a
``CommandRegistry`` snapshot taken when the palette opens.

Places are looked up through ``search_places`` (Nominatim) only when online and
the query has 3 or more characters, 400 ms after the last keystroke. A result
that arrives after the query changed is dropped.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

from ui import palette as P
from ui.command_registry import Command, CommandRegistry

log = logging.getLogger(__name__)

PLACE_MIN_CHARS = 3
PLACE_DEBOUNCE_MS = 400
MAX_ROWS = 50
_WIDTH = 560
_TOP = 64
_GUTTER = 16


class PlaceSearch(QObject):
    """Runs a blocking place search on a daemon thread; results come back by signal."""
    results_ready = pyqtSignal(int, str, list)     # request id, query, [(name, lat, lon)]

    def __init__(self, search: Callable[[str], list], parent=None):
        super().__init__(parent)
        self._search = search
        self._next_id = 0

    def start(self, query: str) -> int:
        self._next_id += 1
        request_id = self._next_id
        threading.Thread(target=self._run, args=(request_id, query), daemon=True).start()
        return request_id

    def _run(self, request_id: int, query: str) -> None:
        try:
            found = list(self._search(query))
        except Exception as e:
            log.info("place search for %r failed: %s", query, e)
            found = []
        self.results_ready.emit(request_id, query, found)


class _SearchBox(QLineEdit):
    """Keeps Esc and the list keys for the palette instead of window shortcuts."""
    navigate = pyqtSignal(int)      # -1 up, +1 down
    escape = pyqtSignal()

    _OWN_KEYS = (Qt.Key.Key_Escape, Qt.Key.Key_Up, Qt.Key.Key_Down,
                 Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in self._OWN_KEYS:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.escape.emit()
        elif key == Qt.Key.Key_Up:
            self.navigate.emit(-1)
        elif key == Qt.Key.Key_Down:
            self.navigate.emit(1)
        elif key == Qt.Key.Key_PageUp:
            self.navigate.emit(-8)
        elif key == Qt.Key.Key_PageDown:
            self.navigate.emit(8)
        else:
            super().keyPressEvent(event)


class CommandPalette(QFrame):
    closed = pyqtSignal()

    def __init__(self, registry: CommandRegistry, parent: QWidget,
                 place_search: Callable[[str], list] | None = None,
                 is_online: Callable[[], bool] = lambda: True,
                 fly_to: Callable[[float, float], None] | None = None):
        super().__init__(parent)
        self.setObjectName("commandPalette")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._registry = registry
        self._is_online = is_online
        self._fly_to = fly_to
        self._rows: list[Command] = []
        self._places: list[Command] = []
        self._place_query = ""
        self._place_request = 0
        self._places_pending = False

        self._place_search = PlaceSearch(place_search, self) if place_search else None
        if self._place_search is not None:
            self._place_search.results_ready.connect(self._on_places)
        self._place_timer = QTimer(self)
        self._place_timer.setSingleShot(True)
        self._place_timer.setInterval(PLACE_DEBOUNCE_MS)
        self._place_timer.timeout.connect(self._start_place_search)

        self._build()
        parent.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self.hide()

    def _build(self) -> None:
        self.setStyleSheet(P.qss("""
            QFrame#commandPalette {
                background-color: $bg_panel@0.98;
                border: 1px solid $border_soft@0.7;
                border-radius: 12px;
            }
            QFrame#commandPalette QLineEdit {
                background: $bg_sunken;
                border: 1px solid $border;
                border-radius: 8px;
                color: $text;
                font-size: 15px;
                padding: 7px 10px;
            }
            QFrame#commandPalette QLineEdit:focus { border-color: $accent; }
            QFrame#commandPalette QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QFrame#commandPalette QListWidget::item { border-radius: 6px; }
            QFrame#commandPalette QListWidget::item:selected { background: $bg_hover; }
            QFrame#commandPalette QLabel { background: transparent; }
            QFrame#commandPalette QLabel#paletteTitle { color: $text; font-size: 13px; }
            QFrame#commandPalette QLabel#paletteHint { color: $text_muted; font-size: 11px; }
            QFrame#commandPalette QLabel#paletteCategory {
                color: $text_label; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;
            }
            QFrame#commandPalette QLabel#paletteKey {
                color: $accent; font-size: 11px; font-weight: 700;
            }
            QFrame#commandPalette QLabel#paletteStatus { color: $text_muted; font-size: 11px; }
        """))
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 8)
        col.setSpacing(6)

        self._input = _SearchBox()
        self._input.setPlaceholderText("Search commands, radar sites, places…")
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self.run_current)
        self._input.navigate.connect(self._move)
        self._input.escape.connect(self.close_palette)
        col.addWidget(self._input)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._list.itemClicked.connect(lambda item: self._run_row(self._list.row(item)))
        col.addWidget(self._list)

        self._status = QLabel("")
        self._status.setObjectName("paletteStatus")
        col.addWidget(self._status)

    # ── open / close ──────────────────────────────────────
    def open_palette(self) -> None:
        self._registry.refresh()
        self._places = []
        self._place_query = ""
        self._input.blockSignals(True)
        self._input.clear()
        self._input.blockSignals(False)
        self._refresh_rows()
        self._place_geometry()
        self.show()
        self.raise_()
        self._input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_palette(self) -> None:
        if self.isHidden():
            return
        self._place_timer.stop()
        self.hide()
        self.closed.emit()

    def toggle(self) -> None:
        if self.isHidden():
            self.open_palette()
        else:
            self.close_palette()

    def _on_focus_changed(self, _old, new) -> None:
        # clicking the map or another control closes the palette
        if self.isHidden() or new is None:
            return
        if new is not self and not self.isAncestorOf(new):
            self.close_palette()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place_geometry()
        return False

    def _place_geometry(self) -> None:
        parent = self.parentWidget()
        width = max(240, min(_WIDTH, parent.width() - 2 * _GUTTER))
        rows = max(1, min(len(self._rows), 9))
        row_h = self._list.sizeHintForRow(0) if self._rows else 40
        list_h = rows * max(row_h, 1) + 4
        height = min(self._input.sizeHint().height() + list_h + 48,
                     max(160, parent.height() - _TOP - _GUTTER))
        self.setGeometry((parent.width() - width) // 2, _TOP, width, height)

    # ── list ──────────────────────────────────────────────
    @property
    def rows(self) -> list[Command]:
        return list(self._rows)

    @property
    def current_row(self) -> int:
        return self._list.currentRow()

    def set_query(self, text: str) -> None:
        self._input.setText(text)

    def _on_text_changed(self, text: str) -> None:
        self._place_timer.stop()
        query = text.strip()
        if query != self._place_query:
            self._places = []
        self._refresh_rows()
        if (self._place_search is not None and len(query) >= PLACE_MIN_CHARS
                and self._is_online()):
            self._places_pending = True
            self._place_timer.start()
        else:
            self._places_pending = False
        self._update_status()

    def _refresh_rows(self) -> None:
        query = self._input.text()
        self._rows = self._registry.search(query, MAX_ROWS, extra=self._places)
        self._list.clear()
        for cmd in self._rows:
            item = QListWidgetItem()
            row = self._row_widget(cmd)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        if self._rows:
            self._list.setCurrentRow(0)
        self._update_status()
        if self.isVisible():
            self._place_geometry()

    @staticmethod
    def _row_widget(cmd: Command) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(cmd.title)
        title.setObjectName("paletteTitle")
        text.addWidget(title)
        if cmd.hint:
            hint = QLabel(cmd.hint)
            hint.setObjectName("paletteHint")
            text.addWidget(hint)
        row.addLayout(text, 1)
        if cmd.keys:
            key = QLabel(cmd.keys)
            key.setObjectName("paletteKey")
            row.addWidget(key)
        category = QLabel(cmd.category.upper())
        category.setObjectName("paletteCategory")
        row.addWidget(category)
        return w

    def _update_status(self) -> None:
        if self._places_pending:
            self._status.setText("Searching places…")
        elif not self._rows:
            self._status.setText("No matches")
        else:
            self._status.setText("↑↓ move · Enter run · Esc close")

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        row = max(0, min(len(self._rows) - 1, self._list.currentRow() + delta))
        self._list.setCurrentRow(row)

    def run_current(self) -> None:
        self._run_row(self._list.currentRow())

    def _run_row(self, row: int) -> None:
        if not 0 <= row < len(self._rows):
            return
        cmd = self._rows[row]
        self.close_palette()
        # after the palette has closed, so a dialog the command opens gets focus
        QTimer.singleShot(0, lambda: self._registry.run(cmd))

    # ── places ────────────────────────────────────────────
    def _start_place_search(self) -> None:
        query = self._input.text().strip()
        if len(query) < PLACE_MIN_CHARS or self._place_search is None:
            return
        self._place_query = query
        self._place_request = self._place_search.start(query)

    def _on_places(self, request_id: int, query: str, found: list) -> None:
        if request_id != self._place_request or query != self._input.text().strip():
            return
        self._places_pending = False
        self._places = [self._place_command(name, lat, lon) for name, lat, lon in found]
        self._refresh_rows()

    def _place_command(self, name: str, lat: float, lon: float) -> Command:
        short = name if len(name) <= 70 else name[:69] + "…"
        fly = self._fly_to

        def run() -> None:
            if fly is not None:
                fly(lat, lon)

        # the query is a keyword so the result always matches what was typed
        return Command(id=f"place:{lat:.4f},{lon:.4f}", title=short, category="Place",
                       run=run, hint=f"{lat:.4f}, {lon:.4f}", keywords=self._place_query)
