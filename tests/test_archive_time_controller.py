"""Tests for archive clock stepping."""

from datetime import datetime, timezone

from archive.time_controller import TimeController


def _utc(second: int) -> datetime:
    return datetime(2026, 4, 16, 0, 1, second, tzinfo=timezone.utc)


def test_arbitrary_step_pauses_and_moves_clock():
    controller = TimeController(_utc(20))
    controller.play()

    controller.step(-10)

    assert controller.is_playing is False
    assert controller.current_time == _utc(10)


def test_arbitrary_step_clamps_to_utc_day():
    controller = TimeController(
        datetime(2026, 4, 16, 0, 0, 3, tzinfo=timezone.utc)
    )

    controller.step(-10)

    assert controller.current_time == datetime(
        2026, 4, 16, 0, 0, 0, tzinfo=timezone.utc
    )


def test_precision_playback_uses_whole_second_ticks():
    controller = TimeController(
        datetime(2026, 4, 16, 0, 0, 3, 500000, tzinfo=timezone.utc)
    )

    controller.set_precision_playback(True)
    controller._on_tick()

    assert controller.speed == 1
    assert controller._timer.interval() == 1000
    assert controller.current_time == datetime(
        2026, 4, 16, 0, 0, 4, tzinfo=timezone.utc
    )
