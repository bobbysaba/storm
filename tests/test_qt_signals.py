"""A worker thread finishing after its window closed (Change Day) emits nothing."""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.qt_signals import emit_alive


@pytest.fixture(autouse=True)
def app():
    return QApplication.instance() or QApplication([])


class Worker(QObject):
    done = pyqtSignal(str)


def test_emits_while_the_object_lives():
    w, got = Worker(), []
    w.done.connect(got.append)
    assert emit_alive(w, "done", "x") is True
    assert got == ["x"]


def test_a_deleted_object_is_skipped_quietly_even_from_a_thread():
    w = Worker()
    sip.delete(w)
    with pytest.raises(RuntimeError):
        w.done.emit("x")                      # what a thread used to hit
    results = []
    t = threading.Thread(target=lambda: results.append(emit_alive(w, "done", "x")))
    t.start()
    t.join(2)
    assert results == [False]


def test_a_fetcher_deleted_with_its_window_finishes_its_download_quietly(monkeypatch, caplog):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from archive.fetchers import hazard_archive_fetcher as hz
    when = datetime(2024, 4, 27, 20, tzinfo=timezone.utc)
    window = QObject()
    fetcher = hz.ArchiveHazardFetcher(when, parent=window)
    monkeypatch.setattr(hz.package_sources, "requests_get", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, text='{"type": "FeatureCollection", "features": []}'))
    sip.delete(window)                         # Change Day: the window and its children go
    assert sip.isdeleted(fetcher)
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
    t = threading.Thread(target=fetcher._fetch_sbw, args=(when,))
    t.start()
    t.join(5)
    assert errors == [] and "deleted" not in caplog.text
