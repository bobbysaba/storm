import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QToolButton

from ui.layer_status import (
    ERROR, LOADING, OFF, OK, STALE, WARN,
    LayerStatus, LayerStatusBoard, LayerStatusDot, StatusTimeLabel,
    format_age, parse_utc,
)

T0 = datetime(2026, 5, 20, 22, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def _radar(clock):
    return LayerStatus("radar", "Radar", timedelta(minutes=8), timedelta(minutes=12), clock)


def test_off_until_a_source_is_active(app):
    status = _radar(FakeClock())
    assert status.state == OFF
    status.set_source_active("radar", True)
    assert status.state == LOADING


def test_ages_through_warn_and_stale(app):
    clock = FakeClock()
    status = _radar(clock)
    status.set_source_active("radar", True)
    status.mark_success(T0 - timedelta(minutes=2))
    assert status.state == OK
    assert status.age_text() == "2m"

    clock.advance(6)          # 8 min old
    status.refresh()
    assert status.state == WARN
    clock.advance(4)          # 12 min old
    status.refresh()
    assert status.state == STALE


def test_error_sticks_until_next_success(app):
    clock = FakeClock()
    status = _radar(clock)
    status.set_source_active("radar", True)
    status.mark_success(T0)
    status.mark_error("timed out")
    assert status.state == ERROR
    clock.advance(30)
    status.refresh()
    assert status.state == ERROR
    assert "timed out" in status.tooltip()

    status.mark_success(clock.now)
    assert status.state == OK
    assert status.last_error == ""


def test_valid_time_never_goes_backwards_live(app):
    clock = FakeClock()
    status = _radar(clock)
    status.set_source_active("radar", True)
    status.mark_success(T0 - timedelta(minutes=1))
    status.mark_success(T0 - timedelta(minutes=20))   # late backfill
    assert status.valid_time == T0 - timedelta(minutes=1)
    assert status.state == OK


def test_without_valid_time_age_is_time_since_fetch(app):
    clock = FakeClock()
    status = LayerStatus("hazards", "Hazards", timedelta(minutes=6), timedelta(minutes=10), clock)
    status.set_source_active("nws_warnings", True)
    status.mark_success()
    assert status.state == OK
    clock.advance(10)
    status.refresh()
    assert status.state == STALE


def test_turning_off_forgets_data_and_errors(app):
    status = _radar(FakeClock())
    status.set_source_active("radar", True)
    status.mark_error("boom")
    status.set_source_active("radar", False)
    assert status.state == OFF
    status.set_source_active("radar", True)
    assert status.state == LOADING


def test_restart_returns_to_loading(app):
    status = _radar(FakeClock())
    status.set_source_active("radar", True)
    status.mark_success(T0)
    status.restart()
    assert status.state == LOADING


def test_no_thresholds_never_stale(app):
    clock = FakeClock()
    status = LayerStatus("sfcoa", "SFCOA", None, None, clock)
    status.set_source_active("sfcoa", True)
    status.mark_success(T0 - timedelta(hours=5))
    assert status.state == OK


def test_changed_emits_only_on_visible_change(app):
    clock = FakeClock()
    status = _radar(clock)
    status.set_source_active("radar", True)
    status.mark_success(T0)
    seen = []
    status.changed.connect(lambda: seen.append(status.state))
    clock.now += timedelta(seconds=20)
    status.refresh()
    assert seen == []          # still "0m", still ok
    clock.advance(1)
    status.refresh()
    assert seen == [OK]        # age text moved to "1m"


def test_board_routes_sources_and_archive_clock(app):
    board = LayerStatusBoard(refresh_ms=60_000)
    hazards = board.add("hazards", "Hazards", ("spc_watches", "nws_warnings"), (6, 10))
    radar = board.add("radar", "Radar", ("radar",), (8, 12))

    board.set_source_active("spc_watches", True)
    board.set_source_active("nws_warnings", True)
    board.set_source_active("spc_watches", False)
    assert hazards.active
    board.set_source_active("unknown_layer", True)    # ignored

    playback = FakeClock(T0)
    board.set_clock(playback, archive=True)
    board.set_source_active("radar", True)
    radar.mark_success(T0 - timedelta(minutes=3))
    assert radar.age_text() == "3m"
    # scrubbing backwards moves valid_time back too
    playback.now = T0 - timedelta(hours=1)
    radar.mark_success(playback.now - timedelta(minutes=1))
    assert radar.age_text() == "1m"
    # fetch time is meaningless against playback time
    hazards.mark_success()
    assert hazards.state == LOADING


def test_status_time_label(app):
    clock = FakeClock()
    status = _radar(clock)
    label = StatusTimeLabel()
    label.bind(status)
    label.set_frame("21:54Z", T0 - timedelta(minutes=6))
    assert label.text() == "21:54Z"            # layer off: no age
    status.set_source_active("radar", True)
    status.mark_success(T0 - timedelta(minutes=6))
    assert label.text() == "21:54Z · 6m"
    assert label.property("layerState") == OK
    clock.advance(6)
    status.refresh()
    assert label.text() == "21:54Z · 12m"
    assert label.property("layerState") == STALE
    label.clear_frame()
    assert label.text() == "--:--Z"


def test_status_dot_visibility_and_click(app):
    clock = FakeClock()
    status = _radar(clock)
    btn = QToolButton()
    btn.setText("RADAR")
    btn.resize(80, 28)
    dot = LayerStatusDot(btn, status)
    opened = []
    dot.activated.connect(lambda: opened.append(True))

    status.set_source_active("radar", True)
    status.mark_success(T0)
    assert dot.isHidden()
    clock.advance(12)
    status.refresh()
    assert not dot.isHidden()
    assert dot.property("layerState") == STALE

    status.mark_error("HTTP 503")
    assert dot.property("layerState") == ERROR
    assert "HTTP 503" in dot.toolTip()


def test_helpers():
    assert format_age(timedelta(seconds=59)) == "0m"
    assert format_age(timedelta(minutes=75)) == "1h15m"
    assert format_age(timedelta(days=2, hours=3)) == "2d"
    assert parse_utc("2026-05-20T22:00:00Z") == T0
    assert parse_utc("") is None
    assert parse_utc("not a time") is None
