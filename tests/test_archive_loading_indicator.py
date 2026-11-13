"""ARCHIVE startup indicator: the line of text that replaced the modal dialog."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ui.widgets.archive_loading_indicator import ArchiveLoadingIndicator


@pytest.fixture(autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def test_shows_the_first_task_and_lets_clicks_through():
    ind = ArchiveLoadingIndicator(["hazards", "satellite", "radar"])
    assert ind.text() == "Loading hazards…"
    assert not ind.isHidden()
    assert ind.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_shows_the_first_outstanding_task_whatever_order_they_finish_in():
    ind = ArchiveLoadingIndicator(["hazards", "satellite", "radar"])
    ind.set_task_done("radar")
    assert ind.text() == "Loading hazards…"
    ind.set_task_done("hazards")
    assert ind.text() == "Loading satellite…"


def test_hides_and_emits_all_done_once():
    ind = ArchiveLoadingIndicator(["hazards", "satellite"])
    fired = []
    ind.all_done.connect(lambda: fired.append(True))
    ind.set_task_done("hazards")
    assert fired == [] and ind.startup_pending()
    ind.set_task_error("satellite")                  # a failure still finishes the task
    ind.set_task_done("satellite")
    assert fired == [True] and ind.isHidden() and not ind.startup_pending()


def test_status_override_lasts_until_its_task_finishes():
    ind = ArchiveLoadingIndicator(["radar", "extra"])
    ind.set_status("Fetching first radar scan…")
    assert ind.text() == "Fetching first radar scan…"
    ind.set_task_done("radar")
    assert ind.text() == "Loading extra…"


def test_no_tasks_starts_hidden():
    assert ArchiveLoadingIndicator([]).isHidden()


def test_later_background_work_shows_after_startup():
    ind = ArchiveLoadingIndicator(["radar"])
    ind.MIN_SHOWN_S = 0
    done = []
    ind.all_done.connect(lambda: done.append(True))
    ind.begin("lidar", "Loading lidar…")
    assert ind.text() == "Loading radar…"            # startup comes first
    ind.set_task_done("radar")
    assert ind.text() == "Loading lidar…" and not ind.isHidden()
    ind.begin("stop", "Loading stop 2 of 2…")
    assert ind.text() == "Loading stop 2 of 2…"      # the latest
    ind.end("stop")
    assert ind.text() == "Loading lidar…"
    ind.end("lidar")
    ind.end("lidar")                                 # ending twice is harmless
    assert ind.isHidden() and done == [True]


def test_a_quick_activity_stays_up_long_enough_to_be_seen():
    ind = ArchiveLoadingIndicator([])
    ind.MIN_SHOWN_S = 0.2
    ind.begin("stop", "Loading stop 2 of 2…")
    ind.end("stop")
    assert not ind.isHidden()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not ind.isHidden():
        QApplication.processEvents()
        time.sleep(0.02)
    assert ind.isHidden()
