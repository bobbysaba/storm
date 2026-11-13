"""Small, non-blocking line of text for ARCHIVE startup loading.

It replaces the modal loading dialog: the map and controls work as soon as
the session opens, and clicks pass through the indicator. It shows the first
startup task still outstanding ("Loading radar…"), moves on as tasks finish,
and hides once none are left.

After startup the same line can show later background work (begin / end), the
most recent first. startup_pending(), not isVisible(), says whether startup
itself is still going.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel

from ui import palette as P


class ArchiveLoadingIndicator(QLabel):
    all_done = pyqtSignal()
    MIN_SHOWN_S = 1.0     # a later activity's line stays up at least this long, so it's seen

    def __init__(self, tasks: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("archiveLoadingIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(P.qss(
            "QLabel#archiveLoadingIndicator { color: $text_label; font-size: 11px; font-weight: 500;"
            " background: $bg_panel@0.88; border: 1px solid $border_soft_2@0.5;"
            " border-radius: 6px; padding: 4px 9px; }"
        ))
        self._tasks = list(tasks)
        self._done: set[str] = set()
        self._status_override: str | None = None
        self._finished = False
        self._activities: dict[str, str] = {}      # key -> text, in the order begun
        self._begun_at: dict[str, float] = {}
        self._refresh()

    def _current_task(self) -> str | None:
        return next((t for t in self._tasks if t not in self._done), None)

    def startup_pending(self) -> bool:
        return self._current_task() is not None

    def set_task_done(self, task: str) -> None:
        """Mark a startup task finished and move on to the next outstanding one."""
        self._done.add(task)
        self._status_override = None
        self._refresh()

    def set_task_error(self, task: str) -> None:
        """A failed task counts as finished, so it can't hold the indicator up;
        the layer's own status shows the error."""
        self.set_task_done(task)

    def set_status(self, message: str) -> None:
        """More specific text for the current task (e.g. "Fetching first radar
        scan…"), cleared when that task finishes."""
        self._status_override = message
        self._refresh()

    def begin(self, key: str, text: str) -> None:
        """Show background work after startup (or once startup finishes)."""
        self._activities.pop(key, None)
        self._activities[key] = text
        self._begun_at[key] = time.monotonic()
        self._refresh()

    def end(self, key: str) -> None:
        if key not in self._activities:
            return
        begun = self._begun_at.get(key, 0.0)
        left = self.MIN_SHOWN_S - (time.monotonic() - begun)
        if left > 0:
            # unless it was begun again meanwhile
            QTimer.singleShot(int(left * 1000),
                              lambda: self._begun_at.get(key) == begun and self._finish(key))
            return
        self._finish(key)

    def _finish(self, key: str) -> None:
        if self._activities.pop(key, None) is not None:
            self._begun_at.pop(key, None)
            self._refresh()

    def _refresh(self) -> None:
        task = self._current_task()
        if task is None and not self._finished:
            self._finished = True
            self.all_done.emit()
        if task is not None:
            text = self._status_override or f"Loading {task}…"
        elif self._activities:
            text = list(self._activities.values())[-1]
        else:
            self.hide()
            return
        self.setText(text)
        self.adjustSize()
        self.show()
