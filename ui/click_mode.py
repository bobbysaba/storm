"""The one map click mode that is active at a time.

Sounding, measure, route pick, annotation placement, drawing, the storm-motion
fix and the ASOS box all capture map clicks. Each kind registers an exit
callback (turn its map mode off, uncheck its button, clear partial state) and,
if plain map clicks belong to it, a click handler. Entering a mode exits the
previous one through its callback, which is told the mode being entered (NONE
when just exiting), so tools that share a widget can tell a switch from a stop.

Modes are strings: a kind (``measure``), optionally with a detail after a colon
(``annotation:road_closure``, ``drawing:cold_front``). ``none`` means no mode.

The controller drops a mode *before* calling its exit callback, so a callback
that unchecks a button whose handler calls ``exit`` again is harmless.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

NONE = "none"


def kind_of(mode: str) -> str:
    return mode.split(":", 1)[0]


class ClickModeController(QObject):
    mode_changed = pyqtSignal(str)      # new mode, NONE when cleared
    prompt_changed = pyqtSignal(str)    # banner text for the current mode, "" for none

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = NONE
        self._prompt = ""
        self._exits: dict[str, Callable[[str, str], None]] = {}
        self._clicks: dict[str, Callable[[float, float], None]] = {}

    def register(self, kind: str, on_exit: Callable[[str, str], None],
                 on_click: Callable[[float, float], None] | None = None) -> None:
        self._exits[kind] = on_exit
        if on_click is not None:
            self._clicks[kind] = on_click

    # ── state ─────────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def kind(self) -> str:
        return kind_of(self._mode)

    @property
    def prompt(self) -> str:
        return self._prompt

    def is_active(self, kind: str | None = None) -> bool:
        if kind is None:
            return self._mode != NONE
        return self.kind == kind

    # ── transitions ───────────────────────────────────────
    def enter(self, mode: str, prompt: str = "") -> None:
        """Make ``mode`` the active mode, exiting any other mode first.

        A different detail of the same kind (another annotation type) is still a
        change: the old mode's exit callback runs, with ``mode`` as the next mode.
        """
        kind = kind_of(mode)
        if kind not in self._exits:
            raise KeyError(f"click mode {kind!r} is not registered")
        if mode == self._mode:
            if prompt:
                self.set_prompt(prompt)
            return
        if self._mode != NONE:
            self._drop_and_exit(mode)
        self._mode = mode
        self._prompt = prompt
        self.mode_changed.emit(mode)
        self.prompt_changed.emit(prompt)

    def exit(self, kind: str | None = None) -> bool:
        """Leave the current mode (only if it is ``kind``, when given). True if one was left."""
        if self._mode == NONE or (kind is not None and self.kind != kind):
            return False
        self._drop_and_exit(NONE)
        self.mode_changed.emit(NONE)
        self.prompt_changed.emit("")
        return True

    def set_prompt(self, prompt: str) -> None:
        if self._mode == NONE or prompt == self._prompt:
            return
        self._prompt = prompt
        self.prompt_changed.emit(prompt)

    def click(self, lat: float, lon: float) -> bool:
        """Send a plain map click to the current mode. False if no mode takes it."""
        handler = self._clicks.get(self.kind)
        if handler is None:
            return False
        handler(lat, lon)
        return True

    def _drop_and_exit(self, next_mode: str) -> None:
        old = self._mode
        self._mode = NONE
        self._prompt = ""
        self._exits[kind_of(old)](old, next_mode)
