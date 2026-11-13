"""Keyboard shortcuts: one table, bound once per window.

``SHORTCUTS`` is the only place keys are assigned. Each entry names the modes it
applies to (``live`` or ``archive``), so Space and ``[`` / ``]`` mean radar loop
and frame step in live modes but playback and step in ARCHIVE, with no key bound
twice. The ``?`` overlay and the toolbar tooltips are generated from it.

Keys that type text (no Ctrl/Alt/Cmd, anything but Esc) are switched off while
a text input has focus, so typing an address in the routing box toggles nothing.

An action that is also a menu-bar entry is bound on that QAction, so the menu
shows its key and the key isn't bound twice (Qt ignores an ambiguous key).
For the same reason a panel with keys of its own (the Video Studio) suspends
the table's bindings of them while it's open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QWidget,
)

LIVE = "live"
ARCHIVE = "archive"
BOTH = frozenset({LIVE, ARCHIVE})


@dataclass(frozen=True)
class Shortcut:
    action: str
    keys: tuple[str, ...]          # QKeySequence portable text; the first is shown
    label: str
    group: str
    modes: frozenset = BOTH


SHORTCUTS: tuple[Shortcut, ...] = (
    # layers and drawers
    Shortcut("radar", ("R",), "Radar", "Layers"),
    Shortcut("satellite", ("S",), "Satellite", "Layers"),
    Shortcut("hazards", ("H",), "Hazards", "Layers"),
    Shortcut("surface", ("O",), "Surface obs", "Layers", frozenset({LIVE})),
    Shortcut("vehicles", ("V",), "Vehicles", "Layers"),
    # map tools
    Shortcut("sounding", ("K",), "Sounding mode", "Map tools"),
    Shortcut("measure", ("M",), "Measure", "Map tools"),
    Shortcut("annotate", ("N",), "Annotate", "Map tools"),
    Shortcut("escape", ("Esc",), "Exit map mode / close drawer", "Map tools"),
    # storm track (archive): the editing keys act only while TRACK is open
    Shortcut("track", ("T",), "Storm track", "Storm track", frozenset({ARCHIVE})),
    Shortcut("track_point_at_marker", ("P",), "Track point at the marker", "Storm track",
             frozenset({ARCHIVE})),
    Shortcut("track_delete", ("Delete", "Backspace"), "Delete selected track point", "Storm track",
             frozenset({ARCHIVE})),
    Shortcut("track_undo", ("Ctrl+Z",), "Undo track edit", "Storm track", frozenset({ARCHIVE})),
    Shortcut("track_redo", ("Ctrl+Shift+Z",), "Redo track edit", "Storm track", frozenset({ARCHIVE})),
    # radar loop (live) / playback (archive)
    Shortcut("loop", ("Space",), "Play / pause radar loop", "Playback", frozenset({LIVE})),
    Shortcut("frame_prev", ("[",), "Previous radar frame", "Playback", frozenset({LIVE})),
    Shortcut("frame_next", ("]",), "Next radar frame", "Playback", frozenset({LIVE})),
    Shortcut("play", ("Space",), "Play / pause playback", "Playback", frozenset({ARCHIVE})),
    Shortcut("step_back", ("Left", "[", "A"), "Step back", "Playback", frozenset({ARCHIVE})),
    Shortcut("step_forward", ("Right", "]", "D"), "Step forward", "Playback", frozenset({ARCHIVE})),
    Shortcut("skip_start", ("Home",), "Jump to start", "Playback", frozenset({ARCHIVE})),
    Shortcut("skip_end", ("End",), "Jump to end", "Playback", frozenset({ARCHIVE})),
    # app
    Shortcut("palette", ("Ctrl+K",), "Command palette", "App"),
    Shortcut("case_open", ("Ctrl+O",), "Open case package", "App", frozenset({ARCHIVE})),
    Shortcut("case_export", ("Ctrl+Shift+E",), "Export case package", "App", frozenset({ARCHIVE})),
    Shortcut("studio", ("Ctrl+Shift+V",), "Video Studio", "App", frozenset({ARCHIVE})),
    Shortcut("screenshot", ("Ctrl+S",), "Save map screenshot", "App"),
    Shortcut("help", ("?",), "Show / hide this list", "App"),
    Shortcut("debug", ("Ctrl+D",), "Debug panel", "App"),
    Shortcut("error_log", ("Ctrl+E",), "Error log", "App"),
)

GROUPS = ("Layers", "Map tools", "Storm track", "Playback", "App")

_MODIFIERS = ("ctrl+", "alt+", "meta+")
_TEXT_INPUTS = (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)


def for_mode(mode: str) -> list[Shortcut]:
    return [s for s in SHORTCUTS if mode in s.modes]


_PLAIN_NAMES = {"Left": "←", "Right": "→"}


def display_key(keys: str) -> str:
    """Key as shown to the user: ⌘S on macOS / Ctrl+S elsewhere, plain names otherwise.

    macOS native text turns Esc, Home and End into glyphs (⎋ ↖ ↘) that read worse.
    """
    if types_text(keys) or keys == "Esc":
        return _PLAIN_NAMES.get(keys, keys)
    return QKeySequence(keys).toString(QKeySequence.SequenceFormat.NativeText) or keys


def types_text(keys: str) -> bool:
    """True for keys a text input would consume (switched off while typing)."""
    k = keys.lower()
    return k != "esc" and not any(m in k for m in _MODIFIERS)


def is_text_input(widget: QWidget | None) -> bool:
    if widget is None:
        return False
    if isinstance(widget, _TEXT_INPUTS):
        return not getattr(widget, "isReadOnly", lambda: False)()
    return isinstance(widget, QComboBox) and widget.isEditable()


def _check_unique() -> None:
    for mode in (LIVE, ARCHIVE):
        seen: dict[str, str] = {}
        for s in for_mode(mode):
            for key in s.keys:
                norm = QKeySequence(key).toString()
                if norm in seen:
                    raise ValueError(f"{key!r} bound to both {seen[norm]} and {s.action} in {mode}")
                seen[norm] = s.action


_check_unique()


class ShortcutRegistry(QObject):
    """Binds the table's shortcuts for one window and mode."""

    def __init__(self, window: QWidget, mode: str):
        super().__init__(window)   # parented, so the focusChanged hookup dies with the window
        self._window = window
        self._mode = mode
        self._by_action = {s.action: s for s in for_mode(mode)}
        self._typing_keys: list[QShortcut] = []
        self._typing = False
        self._suspended: set[QShortcut] = set()
        self.bound: dict[str, list[QShortcut]] = {}
        self.callbacks: dict[str, Callable[[], None]] = {}   # the command palette runs these
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    @property
    def mode(self) -> str:
        return self._mode

    def shortcut(self, action: str) -> Shortcut | None:
        return self._by_action.get(action)

    def bind(self, action: str, callback: Callable[[], None], menu_action: QAction | None = None) -> None:
        """Bind ``action`` if it applies to this mode; otherwise do nothing.
        With ``menu_action`` (its menu-bar entry) the keys go on that QAction."""
        spec = self._by_action.get(action)
        if spec is None:
            return
        if menu_action is not None:
            menu_action.setShortcuts([QKeySequence(keys) for keys in spec.keys])
            menu_action.triggered.connect(lambda _checked=False: callback())
            self.bound[action] = [menu_action]
            self.callbacks[action] = callback
            return
        shortcuts = []
        for keys in spec.keys:
            sc = QShortcut(QKeySequence(keys), self._window)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(callback)
            if types_text(keys):
                self._typing_keys.append(sc)
            shortcuts.append(sc)
        self.bound[action] = shortcuts
        self.callbacks[action] = callback

    def tooltip(self, action: str, text: str) -> str:
        """``text`` with the action's key appended: "Show/hide radar controls (R)"."""
        spec = self._by_action.get(action)
        return f"{text} ({display_key(spec.keys[0])})" if spec else text

    def set_typing(self, typing: bool) -> None:
        self._typing = typing
        for sc in self._typing_keys:
            sc.setEnabled(not typing and sc not in self._suspended)

    def suspend(self, actions) -> None:
        """Switch these actions' keys off until resume() (menu entries keep theirs)."""
        for action in actions:
            for sc in self.bound.get(action, []):
                if isinstance(sc, QShortcut):
                    self._suspended.add(sc)
                    sc.setEnabled(False)

    def resume(self, actions) -> None:
        for action in actions:
            for sc in self.bound.get(action, []):
                if sc in self._suspended:
                    self._suspended.discard(sc)
                    sc.setEnabled(not (self._typing and sc in self._typing_keys))

    def _on_focus_changed(self, _old, new) -> None:
        self.set_typing(is_text_input(new))
