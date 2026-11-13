"""The ⌘K palette widget: keys, running, closing, typing suppression and place search."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMainWindow, QWidget

from ui import shortcuts
from ui.command_registry import Command, CommandRegistry
from ui.widgets import command_palette
from ui.widgets.command_palette import CommandPalette


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(app, monkeypatch):
    monkeypatch.setattr(command_palette, "PLACE_DEBOUNCE_MS", 10)
    ran = []
    reg = CommandRegistry()
    reg.add_provider("p", lambda: [
        Command("radar", "Radar", "Drawer", lambda: ran.append("radar"), keys="R"),
        Command("sat", "Satellite CONUS", "Satellite", lambda: ran.append("sat")),
        Command("site", "KTLX Oklahoma City", "Radar site", lambda: ran.append("site")),
    ])
    win = QMainWindow()
    container = QWidget()
    win.setCentralWidget(container)
    win.resize(900, 700)
    win.show()
    searches = []
    online = {"value": True}

    def search(q):
        searches.append(q)
        return [("Norman, Cleveland County, Oklahoma", 35.22, -97.44)]

    flown = []
    palette = CommandPalette(reg, container, place_search=search,
                             is_online=lambda: online["value"],
                             fly_to=lambda lat, lon: flown.append((lat, lon)))
    yield palette, ran, win, searches, online, flown
    palette.close_palette()
    win.close()
    win.deleteLater()
    QApplication.processEvents()


def _type(palette, text):
    QTest.keyClicks(palette._input, text)


def test_opens_with_every_command_and_closes_on_esc(setup):
    palette, _ran, *_ = setup
    palette.open_palette()
    assert palette.isVisible()
    assert [c.id for c in palette.rows] == ["radar", "sat", "site"]
    QTest.keyClick(palette._input, Qt.Key.Key_Escape)
    assert palette.isHidden()


def test_typing_filters_and_enter_runs_the_top_row(setup):
    palette, ran, *_ = setup
    palette.open_palette()
    _type(palette, "ktlx")
    assert [c.id for c in palette.rows] == ["site"]
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    assert palette.isHidden()
    QTest.qWait(20)
    assert ran == ["site"]


def test_arrows_move_the_selection(setup):
    palette, ran, *_ = setup
    palette.open_palette()
    QTest.keyClick(palette._input, Qt.Key.Key_Down)
    QTest.keyClick(palette._input, Qt.Key.Key_Down)
    QTest.keyClick(palette._input, Qt.Key.Key_Down)      # stops at the last row
    assert palette.current_row == 2
    QTest.keyClick(palette._input, Qt.Key.Key_Up)
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    QTest.qWait(20)
    assert ran == ["sat"]


def test_recent_command_is_listed_first_next_time(setup):
    palette, ran, *_ = setup
    palette.open_palette()
    _type(palette, "sat")
    QTest.keyClick(palette._input, Qt.Key.Key_Return)
    QTest.qWait(20)
    palette.open_palette()
    assert palette.rows[0].id == "sat"


def test_typing_in_the_palette_triggers_no_window_shortcut(setup):
    palette, _ran, win, *_ = setup
    hits = []
    reg = shortcuts.ShortcutRegistry(win, shortcuts.LIVE)
    reg.bind("radar", lambda: hits.append("R"))
    reg.bind("escape", lambda: hits.append("Esc"))
    palette.open_palette()
    QApplication.processEvents()
    assert shortcuts.is_text_input(QApplication.focusWidget())
    _type(palette, "r")
    QTest.keyClick(palette._input, Qt.Key.Key_Escape)
    assert hits == []
    assert palette.isHidden()


def test_focus_elsewhere_closes_the_palette(setup):
    palette, _ran, win, *_ = setup
    other = QLineEdit(win.centralWidget())
    other.show()
    palette.open_palette()
    QApplication.processEvents()
    other.setFocus()
    QApplication.processEvents()
    assert palette.isHidden()


def test_places_are_searched_after_three_chars_and_fly_there(setup):
    palette, _ran, _win, searches, _online, flown = setup
    palette.open_palette()
    _type(palette, "no")
    QTest.qWait(50)
    assert searches == []
    _type(palette, "r")
    for _ in range(50):
        QTest.qWait(10)
        if any(c.category == "Place" for c in palette.rows):
            break
    assert searches == ["nor"]
    place = next(c for c in palette.rows if c.category == "Place")
    assert place.title.startswith("Norman")
    palette._run_row(palette.rows.index(place))
    QTest.qWait(20)
    assert flown == [(35.22, -97.44)]


def test_no_place_search_offline(setup):
    palette, _ran, _win, searches, online, _flown = setup
    online["value"] = False
    palette.open_palette()
    _type(palette, "norman")
    QTest.qWait(60)
    assert searches == []


def test_stale_place_results_are_dropped(setup):
    palette, *_ = setup
    palette.open_palette()
    _type(palette, "norman")
    palette._place_request = 7
    palette._on_places(6, "norman", [("Old", 1.0, 2.0)])
    palette._on_places(7, "norma", [("Other query", 1.0, 2.0)])
    assert not any(c.category == "Place" for c in palette.rows)
