import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QElapsedTimer
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout

from ui.widgets.drawer import Drawer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Sample(Drawer):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.rows = [QLabel(f"row {i}") for i in range(3)]
        for row in self.rows:
            layout.addWidget(row)
        self.rows[2].setVisible(False)


def _settle(app, drawer, ms=600):
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()
        if not drawer.is_animating() and not drawer._open_pending and not drawer._content_pending:
            break
    app.processEvents()


def _height(drawer):
    drawer.adjustSize()
    return drawer.height()


def test_starts_closed_and_collapsed(app):
    d = _Sample()
    assert not d.is_open()
    assert not d.occupies_space()
    assert d.maximumHeight() == 0


def test_open_animates_to_natural_height_then_lifts_cap(app):
    d = _Sample()
    d.show()
    steps = []
    d.geometry_changed.connect(lambda: steps.append(d.maximumHeight()))
    d.toggle_drawer(True)
    assert d.is_open() and d.occupies_space()
    _settle(app, d)
    assert len(steps) > 1
    assert d.maximumHeight() == 16777215
    assert _height(d) == d.sizeHint().height()


def test_close_occupies_space_until_animation_ends(app):
    d = _Sample()
    d.show()
    d.toggle_drawer(True)
    _settle(app, d)
    d.toggle_drawer(False)
    assert not d.is_open()
    assert d.occupies_space()
    _settle(app, d)
    assert not d.occupies_space()
    assert d.maximumHeight() == 0


def test_content_changed_refits_when_open(app):
    d = _Sample()
    d.show()
    d.toggle_drawer(True)
    _settle(app, d)
    before = _height(d)
    d.rows[2].setVisible(True)
    d.content_changed()
    d.content_changed()  # coalesced
    _settle(app, d)
    assert _height(d) > before
    assert d.maximumHeight() == 16777215


def test_content_changed_ignored_when_closed(app):
    d = _Sample()
    d.rows[2].setVisible(True)
    d.content_changed()
    _settle(app, d)
    assert d.maximumHeight() == 0


def test_reopen_while_closing_reverses(app):
    d = _Sample()
    d.show()
    d.toggle_drawer(True)
    _settle(app, d)
    d.toggle_drawer(False)
    app.processEvents()
    d.toggle_drawer(True)
    _settle(app, d)
    assert d.is_open()
    assert d.maximumHeight() == 16777215
