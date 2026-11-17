"""ARCHIVE status: a small chip at the bottom left of the map and the popover
it opens.

The chip shows the cursor coordinates and one health dot (red: a layer or the
network failed, amber: something is loading or warned, green: all well). The
popover holds what the archive bar and the two-row status pill used to show:
radar, satellite and OBS status, vehicles, network, recent activity, and the
version / update button. Keeping it off the bar lets the bar and the chip sit
side by side on narrow windows.

MainWindow points its usual status members (``coord_label``,
``status_msg_label``, ``net_indicator``, ...) at widgets in here, so their
callers don't change; archive code reports layer status through the
``set_*_status`` methods.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.theme import set_pill_status

COORD_PROBE = "-90.0000, -180.0000"
POPOVER_WIDTH = 300
HISTORY = 6

# chip dot: worst state wins
ERROR, BUSY, OK = "error", "warn", "ok"
_RANK = {OK: 0, BUSY: 1, ERROR: 2}


def format_coordinates(lat: float, lon: float) -> str:
    return f"{lat:.4f}, {lon:.4f}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%MZ")


class ActivityLabel(QLabel):
    """The status message line. Every ``setText`` is kept in a short history
    (newest last), since a message is otherwise gone once the next arrives."""

    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.history: deque[tuple[str, str]] = deque(maxlen=HISTORY)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt override)
        super().setText(text)
        if text and (not self.history or self.history[-1][1] != text):
            self.history.append((_now(), text))
        self.changed.emit(text)


class ArchiveStatusChip(QWidget):
    """Bottom-left chip: coordinates and the health dot; click to toggle the popover."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusOverlayLeft")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(8)

        self.coord_label = QLabel("---.----, ---.----")
        self.coord_label.setMinimumWidth(
            self.coord_label.fontMetrics().horizontalAdvance(COORD_PROBE) + 8)
        row.addWidget(self.coord_label)

        self.dot = QLabel("●")
        self.dot.setProperty("pillRole", "badge")
        row.addWidget(self.dot)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ArchiveStatusPopover(QWidget):
    """Status detail, opened from the chip."""

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusOverlayLeft")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(POPOVER_WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 8, 12, 8)
        col.setSpacing(4)

        col.addWidget(self._title("DATA"))
        self.radar_label = self._value("Radar: --")
        self.sat_label = self._value("Sat: --")
        self.obs_label = self._value("OBS: --")
        self.vehicle_count_label = self._value("VEHICLES: 0")
        for w in (self.radar_label, self.sat_label, self.obs_label, self.vehicle_count_label):
            col.addWidget(w)

        col.addWidget(self._title("CONNECTION"))
        net_row = QHBoxLayout()
        net_row.setSpacing(8)
        self.net_indicator = QLabel("")
        self.net_indicator.setProperty("pillRole", "badge")
        self.net_indicator.setVisible(False)
        net_row.addWidget(self.net_indicator)
        self.loop_slot = net_row      # MainWindow adds its loop chip here
        net_row.addStretch()
        col.addLayout(net_row)

        col.addWidget(self._title("ACTIVITY"))
        self.idle_label = QLabel("Nothing loading")
        set_pill_status(self.idle_label, "muted")
        col.addWidget(self.idle_label)
        self.status_msg_label = ActivityLabel()
        self.status_msg_label.setWordWrap(True)
        set_pill_status(self.status_msg_label, "label")
        col.addWidget(self.status_msg_label)
        self.history_label = QLabel("")
        self.history_label.setWordWrap(True)
        set_pill_status(self.history_label, "muted")
        col.addWidget(self.history_label)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        version_label = QLabel(f"STORM v{version}")
        set_pill_status(version_label, "muted")
        foot.addWidget(version_label)
        self.update_slot = foot       # MainWindow adds its update button here
        foot.addStretch()
        col.addSpacing(4)
        col.addLayout(foot)

        self.status_msg_label.changed.connect(self._render_history)
        self._render_history("")

    def _render_history(self, _text: str) -> None:
        # the current message is shown above, so list only the earlier ones
        past = list(self.status_msg_label.history)
        if past and past[-1][1] == self.status_msg_label.text():
            past = past[:-1]
        self.history_label.setText("\n".join(f"{t}  {m}" for t, m in reversed(past)))
        self.history_label.setVisible(bool(past))
        self.status_msg_label.setVisible(bool(self.status_msg_label.text()))
        self.idle_label.setVisible(not self.status_msg_label.text() and not past)
        self.adjustSize()

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("pillRole", "action")
        set_pill_status(label, "muted")
        return label

    @staticmethod
    def _value(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        set_pill_status(label, "label")
        return label


class ArchiveStatus(QObject):
    """Owns the chip and popover and keeps the chip's dot current."""

    layout_requested = pyqtSignal()

    def __init__(self, version: str, parent_widget: QWidget):
        super().__init__(parent_widget)
        self.chip = ArchiveStatusChip(parent_widget)
        self.popover = ArchiveStatusPopover(version, parent_widget)
        self.popover.hide()
        self._radar_error = False
        self._sat_error = False
        self._net_state = ""
        self.chip.clicked.connect(self.toggle)
        self.popover.status_msg_label.changed.connect(lambda _t: self._refresh())
        QApplication.instance().installEventFilter(self)
        self._refresh()

    # ── layer status (was on the archive bar) ─────────────
    def set_radar_status(self, text: str, error: bool = False) -> None:
        self._radar_error = error
        self._set(self.popover.radar_label, text, "error" if error else "label")

    def set_satellite_status(self, text: str, error: bool = False, tooltip: str = "") -> None:
        self._sat_error = error
        self.popover.sat_label.setToolTip(tooltip)
        self._set(self.popover.sat_label, text, "error" if error else "label")

    def set_obs_status(self, text: str, active: bool = False) -> None:
        self._set(self.popover.obs_label, text, "ok" if active else "label")

    def set_net_state(self, state: str) -> None:
        """``ok`` / ``slow`` / anything else (offline), as _NetChecker reports."""
        self._net_state = state
        self._refresh()

    def _set(self, label: QLabel, text: str, status: str) -> None:
        label.setText(text)
        set_pill_status(label, status)
        self._refresh()

    # ── chip dot ──────────────────────────────────────────
    def state(self) -> str:
        msg = self.popover.status_msg_label.text()
        states = [OK]
        if self._radar_error or self._sat_error or self._net_state not in ("", "ok", "slow"):
            states.append(ERROR)
        if self._net_state == "slow" or "…" in msg or "error" in msg.lower():
            states.append(BUSY)
        return max(states, key=_RANK.__getitem__)

    def _problems(self) -> list[str]:
        out = []
        if self._radar_error:
            out.append(self.popover.radar_label.text())
        if self._sat_error:
            out.append(self.popover.sat_label.text())
        if self._net_state not in ("", "ok"):
            out.append(self.popover.net_indicator.text().lstrip("● "))
        msg = self.popover.status_msg_label.text()
        if msg:
            out.append(msg)
        return out

    def _refresh(self) -> None:
        set_pill_status(self.chip.dot, self.state())
        lines = self._problems() + ["Click for archive status"]
        self.chip.setToolTip("\n".join(lines))

    # ── popover ───────────────────────────────────────────
    def toggle(self) -> None:
        self.set_open(not self.popover.isVisible())

    def set_open(self, on: bool) -> None:
        self.popover.setVisible(on)
        if on:
            self.popover.adjustSize()
        self.layout_requested.emit()

    def eventFilter(self, obj, event):
        # a click anywhere outside the popover and chip closes it
        if (event.type() == QEvent.Type.MouseButtonPress and self.popover.isVisible()
                and isinstance(obj, QWidget)
                and not (self._within(obj, self.popover) or self._within(obj, self.chip))):
            self.set_open(False)
        return False

    @staticmethod
    def _within(obj: QWidget, ancestor: QWidget) -> bool:
        return obj is ancestor or ancestor.isAncestorOf(obj)
