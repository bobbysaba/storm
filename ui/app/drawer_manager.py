"""Which toolbar drawers stay open together.

One at a time: opening a drawer closes the other open drawers, except pinned
ones and child drawers (MAP → ROUTE). Pinned drawers stack with it; when that
stack would run past the bottom overlays, the oldest unpinned drawers close.

Drawers are always closed through their toolbar button, so the button's own
side effects (e.g. PREV LOCS hiding its layer) still run.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QSettings, pyqtSignal

from ui.app.overlay_geometry import drawers_to_close

DEFAULT_PINNED: tuple[str, ...] = ()

_PINNED_KEY = "ui/pinned_drawers_v2"
# v1 pinned PREV LOCS and SOUNDING by default; migrating drops those two and
# keeps any pins the user added
_PINNED_KEY_V1 = "ui/pinned_drawers"
_V1_DEFAULT = {"prev_locs", "sounding"}


@dataclass
class DrawerEntry:
    key: str
    drawer: object          # ui.widgets.drawer.Drawer
    button: object          # checkable QToolButton that opens it
    child: bool = False     # opened from inside another drawer (MAP → ROUTE)


class DrawerManager(QObject):
    pins_changed = pyqtSignal()

    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings("NSSL", "STORM")
        self._entries: dict[str, DrawerEntry] = {}
        self._open_order: list[str] = []
        self._pinned: set[str] = self._load_pins()

    def _load_pins(self) -> set[str]:
        s = self._settings
        if s.contains(_PINNED_KEY):
            return set(s.value(_PINNED_KEY, [], type=list) or [])
        pinned = set(DEFAULT_PINNED)
        if s.contains(_PINNED_KEY_V1):
            pinned = set(s.value(_PINNED_KEY_V1, [], type=list) or []) - _V1_DEFAULT
            s.remove(_PINNED_KEY_V1)
        s.setValue(_PINNED_KEY, sorted(pinned))
        return pinned

    # ── registration ──────────────────────────────────────
    def add(self, key: str, drawer, button, child: bool = False) -> None:
        self._entries[key] = DrawerEntry(key, drawer, button, child)
        drawer.opened.connect(lambda k=key: self._on_opened(k))
        drawer.closed.connect(lambda k=key: self._on_closed(k))

    def entry(self, key: str) -> DrawerEntry:
        return self._entries[key]

    def keys(self) -> list[str]:
        return list(self._entries)

    # ── pins ──────────────────────────────────────────────
    def is_pinned(self, key: str) -> bool:
        return key in self._pinned

    def set_pinned(self, key: str, pinned: bool) -> None:
        if pinned == (key in self._pinned):
            return
        if pinned:
            self._pinned.add(key)
        else:
            self._pinned.discard(key)
        self._settings.setValue(_PINNED_KEY, sorted(self._pinned))
        self.pins_changed.emit()

    def reset(self) -> None:
        """Default pins (settings → RESET LAYOUT)."""
        if self._pinned != set(DEFAULT_PINNED):
            self._pinned = set(DEFAULT_PINNED)
            self._settings.setValue(_PINNED_KEY, sorted(self._pinned))
            self.pins_changed.emit()

    def _exempt(self, key: str) -> bool:
        """Never auto-closed, and opening it closes nothing."""
        return key in self._pinned or self._entries[key].child

    # ── open/close bookkeeping ────────────────────────────
    def _on_opened(self, key: str) -> None:
        if key in self._open_order:
            self._open_order.remove(key)
        self._open_order.append(key)
        if not self._exempt(key):
            self._close_others(key)

    def _on_closed(self, key: str) -> None:
        if key in self._open_order:
            self._open_order.remove(key)

    def _close_others(self, keep: str) -> None:
        for key in list(self._open_order):
            if key != keep and not self._exempt(key):
                self._close(key)

    def open_keys(self) -> list[str]:
        """Open toolbar drawers, oldest first (children like ROUTE left out)."""
        return [k for k in self._open_order if not self._entries[k].child]

    def close_all(self) -> None:
        for key in list(self._open_order):
            self._close(key)

    def close_newest(self, skip: tuple[str, ...] = ()) -> bool:
        """Close the most recently opened drawer, pinned or not (Esc). False if none is open."""
        order = [k for k in self._open_order if k not in skip]
        if not order:
            return False
        self._close(order[-1])
        return True

    def _close(self, key: str) -> None:
        button = self._entries[key].button
        if button.isChecked():
            button.setChecked(False)
        else:
            # opened without its toolbar button (e.g. the nav pill)
            self._entries[key].drawer.toggle_drawer(False)

    # ── fit ───────────────────────────────────────────────
    def enforce_fit(self, available: int, fixed_heights: list[int] = (),
                    skip: tuple[str, ...] = ()) -> list[str]:
        """Close the oldest unpinned drawers until the stack fits ``available``.

        ``fixed_heights`` are other overlays stacked with the drawers (vehicle
        panels) that can't be closed. ``skip`` drawers are open but placed
        elsewhere. The newest drawer is never closed. Returns closed keys.
        """
        order = [k for k in self._open_order if k not in skip]
        newest = order[-1] if order else None
        stack = [("", h, False) for h in fixed_heights]
        for key in order:
            h = self._entries[key].drawer.sizeHint().height()
            stack.append((key, h, key != newest and not self._exempt(key)))
        closing = drawers_to_close(stack, available)
        for key in closing:
            self._close(key)
        return closing
