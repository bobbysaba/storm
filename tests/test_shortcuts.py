import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton, QWidget

from ui import shortcuts
from ui.shortcuts import ARCHIVE, LIVE, SHORTCUTS, ShortcutRegistry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _keys(mode):
    return [QKeySequence(k).toString() for s in shortcuts.for_mode(mode) for k in s.keys]


@pytest.mark.parametrize("mode", [LIVE, ARCHIVE])
def test_no_key_is_bound_twice_in_a_mode(mode):
    keys = _keys(mode)
    assert len(keys) == len(set(keys))


def test_space_and_brackets_change_meaning_in_archive():
    live = {k: s.action for s in shortcuts.for_mode(LIVE) for k in s.keys}
    archive = {k: s.action for s in shortcuts.for_mode(ARCHIVE) for k in s.keys}
    assert (live["Space"], live["["], live["]"]) == ("loop", "frame_prev", "frame_next")
    assert (archive["Space"], archive["["], archive["]"]) == ("play", "step_back", "step_forward")
    assert "Left" not in live and "Home" not in live


def test_every_group_is_listed():
    assert {s.group for s in SHORTCUTS} <= set(shortcuts.GROUPS)


def test_types_text():
    assert shortcuts.types_text("R")
    assert shortcuts.types_text("Space")
    assert shortcuts.types_text("?")
    assert shortcuts.types_text("Left")
    assert not shortcuts.types_text("Esc")
    assert not shortcuts.types_text("Ctrl+S")


def test_is_text_input(app):
    assert shortcuts.is_text_input(QLineEdit())
    ro = QLineEdit()
    ro.setReadOnly(True)
    assert not shortcuts.is_text_input(ro)
    combo = QComboBox()
    assert not shortcuts.is_text_input(combo)
    combo.setEditable(True)
    assert shortcuts.is_text_input(combo)
    assert not shortcuts.is_text_input(QPushButton())
    assert not shortcuts.is_text_input(None)


def test_bind_skips_actions_from_the_other_mode(app):
    reg = ShortcutRegistry(QWidget(), LIVE)
    reg.bind("radar", lambda: None)
    reg.bind("play", lambda: None)          # archive only
    reg.bind("step_back", lambda: None)     # archive only
    assert set(reg.bound) == {"radar"}


def test_archive_step_binds_every_listed_key(app):
    reg = ShortcutRegistry(QWidget(), ARCHIVE)
    reg.bind("step_back", lambda: None)
    assert [sc.key().toString() for sc in reg.bound["step_back"]] == ["Left", "[", "A"]


def test_typing_disables_only_text_keys(app):
    reg = ShortcutRegistry(QWidget(), LIVE)
    for action in ("radar", "escape", "screenshot", "help"):
        reg.bind(action, lambda: None)
    reg.set_typing(True)
    enabled = {a: scs[0].isEnabled() for a, scs in reg.bound.items()}
    assert enabled == {"radar": False, "escape": True, "screenshot": True, "help": False}
    reg.set_typing(False)
    assert all(scs[0].isEnabled() for scs in reg.bound.values())


def test_focus_on_a_text_box_disables_text_keys(app):
    win = QWidget()
    box = QLineEdit(win)
    other = QPushButton(win)
    reg = ShortcutRegistry(win, LIVE)
    reg.bind("radar", lambda: None)
    reg._on_focus_changed(None, box)
    assert not reg.bound["radar"][0].isEnabled()
    reg._on_focus_changed(box, other)
    assert reg.bound["radar"][0].isEnabled()


def test_display_key_uses_plain_names_without_modifiers():
    assert shortcuts.display_key("Esc") == "Esc"
    assert shortcuts.display_key("Home") == "Home"
    assert shortcuts.display_key("Left") == "←"
    assert shortcuts.display_key("Ctrl+S") in ("⌘S", "Ctrl+S")


def test_tooltip_appends_the_key(app):
    reg = ShortcutRegistry(QWidget(), LIVE)
    assert reg.tooltip("radar", "Show/hide radar controls") == "Show/hide radar controls (R)"
    assert reg.tooltip("play", "Play") == "Play"    # not a live shortcut
