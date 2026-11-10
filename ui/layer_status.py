"""Per-layer data health: when the data is valid, when it last arrived, and
whether the last fetch failed.

One ``LayerStatus`` per data layer (radar, satellite, hazards, ...). Fetch
handlers call ``mark_success`` / ``mark_error``; the map layer toggles feed
``set_source_active``. A ``LayerStatusBoard`` owns the statuses and re-checks
staleness on a 15 s timer, so labels and toolbar dots age on their own.

States:
    off      no source of this layer is shown
    loading  shown, but no data has arrived yet
    ok       data is younger than the warn threshold
    warn     data is older than the warn threshold
    stale    data is older than the stale threshold
    error    the last fetch failed; stays until the next success
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QAbstractButton, QLabel

OFF = "off"
LOADING = "loading"
OK = "ok"
WARN = "warn"
STALE = "stale"
ERROR = "error"

REFRESH_MS = 15_000

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_utc(value: object) -> datetime | None:
    """ISO 8601 string (``...Z`` or offset) → aware UTC datetime, else None."""
    text = str(value or "")
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def format_age(age: timedelta) -> str:
    minutes = max(0, int(age.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    return f"{hours // 24}d"


def _minutes(value: float | None) -> timedelta | None:
    return None if value is None else timedelta(minutes=value)


class LayerStatus(QObject):
    """Health of one data layer. Emits ``changed`` when the state or the
    displayed age changes."""

    changed = pyqtSignal()

    def __init__(
        self,
        name: str,
        label: str,
        warn_after: timedelta | None = None,
        stale_after: timedelta | None = None,
        clock: Clock = utc_now,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.name = name
        self.label = label
        self.warn_after = warn_after
        self.stale_after = stale_after
        self._clock = clock
        # archive mode: valid_time follows the time controller back and forth,
        # and fetch time says nothing about data age
        self.archive = False
        self.valid_time: datetime | None = None
        self.last_success: datetime | None = None
        self.last_error = ""
        self.last_error_time: datetime | None = None
        self._sources: set[str] = set()
        self._state = OFF
        self._age_text = ""

    # ── inputs ───────────────────────────────────────────────
    def set_clock(self, clock: Clock, archive: bool = False) -> None:
        self._clock = clock
        self.archive = archive
        self.refresh()

    def set_source_active(self, source: str, active: bool) -> None:
        was_active = self.active
        if active:
            self._sources.add(source)
        else:
            self._sources.discard(source)
        if was_active and not self.active:
            # turned off: forget old data and errors so turning it back on
            # starts from "loading" rather than a stale or red badge
            self._clear()
        self.refresh()

    def restart(self) -> None:
        """Forget data from the previous source (e.g. a new radar site)."""
        self._clear()
        self.refresh()

    def mark_success(self, valid_time: datetime | None = None) -> None:
        self.last_success = self._clock()
        if valid_time is not None:
            valid_time = as_utc(valid_time)
            if self.archive or self.valid_time is None or valid_time > self.valid_time:
                self.valid_time = valid_time
        self.last_error = ""
        self.last_error_time = None
        self.refresh(force=True)

    def mark_error(self, message: str) -> None:
        self.last_error = str(message)
        self.last_error_time = self._clock()
        self.refresh(force=True)

    # ── outputs ──────────────────────────────────────────────
    @property
    def active(self) -> bool:
        return bool(self._sources)

    @property
    def state(self) -> str:
        return self._state

    def age(self, when: datetime | None = None) -> timedelta | None:
        """Age of ``when`` (default: the newest data) against the clock."""
        ref = when or self.valid_time
        if ref is None and not self.archive:
            ref = self.last_success
        if ref is None:
            return None
        return max(timedelta(0), self._clock() - as_utc(ref))

    def age_text(self, when: datetime | None = None) -> str:
        age = self.age(when)
        return format_age(age) if age is not None else ""

    def tooltip(self) -> str:
        lines = []
        if self._state == ERROR:
            when = self.last_error_time.strftime("%H:%M:%SZ") if self.last_error_time else ""
            lines.append(f"{self.label} error at {when}: {self.last_error}")
            lines.append("Retrying automatically. Click to open the drawer.")
        elif self._state in (WARN, STALE):
            lines.append(f"{self.label} data is {self.age_text()} old")
        elif self._state == LOADING:
            lines.append(f"{self.label}: waiting for data")
        if self.valid_time is not None:
            lines.append(f"Newest data {self.valid_time.strftime('%H:%MZ')} ({self.age_text()} old)")
        elif self.last_success is not None and not self.archive:
            lines.append(f"Last updated {self.age_text()} ago")
        return "\n".join(lines)

    def refresh(self, force: bool = False) -> None:
        state, age_text = self._evaluate(), self.age_text()
        if force or state != self._state or age_text != self._age_text:
            self._state, self._age_text = state, age_text
            self.changed.emit()

    def _evaluate(self) -> str:
        if not self.active:
            return OFF
        if self.last_error_time is not None:
            return ERROR
        age = self.age()
        if age is None:
            return LOADING
        if self.stale_after is not None and age >= self.stale_after:
            return STALE
        if self.warn_after is not None and age >= self.warn_after:
            return WARN
        return OK

    def _clear(self) -> None:
        self.valid_time = None
        self.last_success = None
        self.last_error = ""
        self.last_error_time = None


class LayerStatusBoard(QObject):
    """All layer statuses, a shared clock and the staleness timer."""

    def __init__(self, parent: QObject | None = None, refresh_ms: int = REFRESH_MS):
        super().__init__(parent)
        self._statuses: dict[str, LayerStatus] = {}
        self._by_source: dict[str, LayerStatus] = {}
        self._clock: Clock = utc_now
        self._archive = False
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def add(
        self,
        name: str,
        label: str,
        sources: tuple[str, ...],
        thresholds_min: tuple[float | None, float | None] = (None, None),
    ) -> LayerStatus:
        """``sources`` are the map layer keys (as passed to the layer pill)
        that make this layer active; ``thresholds_min`` is (warn, stale)."""
        warn, stale = thresholds_min
        status = LayerStatus(name, label, _minutes(warn), _minutes(stale), self._clock, self)
        status.archive = self._archive
        self._statuses[name] = status
        for source in sources:
            self._by_source[source] = status
        return status

    def __getitem__(self, name: str) -> LayerStatus:
        return self._statuses[name]

    def get(self, name: str) -> LayerStatus | None:
        return self._statuses.get(name)

    def set_clock(self, clock: Clock, archive: bool = False) -> None:
        self._clock = clock
        self._archive = archive
        for status in self._statuses.values():
            status.set_clock(clock, archive)

    def set_source_active(self, source: str, active: bool) -> None:
        status = self._by_source.get(source)
        if status is not None:
            status.set_source_active(source, active)

    def refresh(self) -> None:
        for status in self._statuses.values():
            status.refresh()


def _repolish(widget, state: str) -> None:
    if widget.property("layerState") == state:
        return
    widget.setProperty("layerState", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class StatusTimeLabel(QLabel):
    """Frame time plus its age, colored by the layer state: ``14:32Z · 6m``."""

    EMPTY = "--:--Z"

    def __init__(self, parent=None):
        super().__init__(self.EMPTY, parent)
        self._status: LayerStatus | None = None
        self._time_str = self.EMPTY
        self._when: datetime | None = None

    def bind(self, status: LayerStatus) -> None:
        self._status = status
        status.changed.connect(self._render)
        self._render()

    def set_frame(self, time_str: str, when: datetime | None = None) -> None:
        self._time_str = time_str
        self._when = when
        self._render()

    def clear_frame(self) -> None:
        self.set_frame(self.EMPTY)

    def _render(self) -> None:
        status = self._status
        if status is None or self._when is None or not status.active:
            self.setText(self._time_str)
            _repolish(self, OFF)
            return
        age = status.age_text(self._when)
        self.setText(f"{self._time_str} · {age}" if age else self._time_str)
        self.setToolTip(status.tooltip())
        _repolish(self, status.state)


class LayerStatusDot(QLabel):
    """Small dot in the top-right corner of a toolbar button: amber when the
    layer is stale, red on error. Clicking a red dot emits ``activated``."""

    SIZE = 8
    INSET = 3

    activated = pyqtSignal()

    def __init__(self, button: QAbstractButton, status: LayerStatus):
        super().__init__(button)
        self.setObjectName("layerStatusDot")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button = button
        self._status = status
        self._allowed = True
        button.installEventFilter(self)
        status.changed.connect(self._sync)
        self._sync()

    def eventFilter(self, obj, event):
        if obj is self._button and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place()
        return False

    def _place(self) -> None:
        self.move(self._button.width() - self.SIZE - self.INSET, self.INSET)
        self.raise_()

    def _sync(self) -> None:
        state = self._status.state
        _repolish(self, state)
        self.setToolTip(self._status.tooltip())
        self.setVisible(self._allowed and state in (STALE, ERROR))
        self._place()

    def set_allowed(self, allowed: bool) -> None:
        """Gate the dot, e.g. one on the toolbar ⋯ button for a layer in its menu."""
        self._allowed = allowed
        self._sync()

    def mousePressEvent(self, event):
        if self._status.state == ERROR and event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            event.accept()
            return
        # let the button underneath handle it
        event.ignore()
