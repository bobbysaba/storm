"""Tests for collision-free floating overlay placement."""

from ui.app.overlay_geometry import (
    bottom_left_y_avoiding,
    bar_beside,
    drawers_to_close,
    rectangles_overlap,
    stack_extent,
)


def test_bar_stays_centred_when_clear_of_left_overlay():
    assert bar_beside(1600, 8, 680, 420, left_clear=177) == (460, 680)


def test_bar_shifts_right_of_left_overlay_instead_of_stacking():
    assert bar_beside(900, 8, 680, 420, left_clear=177) == (185, 680)


def test_bar_narrows_to_fit_beside_left_overlay():
    assert bar_beside(700, 8, 680, 420, left_clear=177) == (185, 507)


def test_bar_that_cannot_fit_beside_left_overlay_is_left_to_stack():
    assert bar_beside(600, 8, 680, 420, left_clear=177) is None


def test_bottom_left_overlay_stays_at_bottom_when_clear():
    archive = (400, 700, 680, 72)

    y = bottom_left_y_avoiding(
        container_height=800,
        margin=8,
        width=320,
        height=70,
        obstacle=archive,
    )

    assert y == 722
    assert not rectangles_overlap((8, y, 320, 70), archive)


def test_bottom_left_overlay_moves_above_colliding_archive_bar():
    archive = (172, 720, 680, 72)

    y = bottom_left_y_avoiding(
        container_height=800,
        margin=8,
        width=360,
        height=70,
        obstacle=archive,
    )

    assert y == 644
    assert not rectangles_overlap((8, y, 360, 70), archive)


def test_bottom_left_overlay_uses_normal_position_without_obstacle():
    assert bottom_left_y_avoiding(800, 8, 360, 70, None) == 722


def test_stack_extent_gap_shrinks_with_closing_drawer():
    assert stack_extent([40, 30]) == 40 + 6 + 30 + 6
    assert stack_extent([40, 3]) == 40 + 6 + 3 + 3
    assert stack_extent([0, 0]) == 0


def test_nothing_closes_when_stack_fits():
    assert drawers_to_close([("radar", 64, True), ("hazards", 36, True)], available=200) == []


def test_oldest_closable_drawers_close_first():
    # 1366x768: about 650px under the toolbar
    stack = [
        ("radar", 64, True),
        ("vehicles", 300, False),
        ("hazards", 120, True),
        ("route", 280, False),
        ("surface", 56, False),   # newest, never closed
    ]
    assert drawers_to_close(stack, available=650) == ["radar", "hazards"]


def test_returns_every_closable_drawer_when_stack_cannot_fit():
    stack = [("radar", 64, True), ("route", 900, False), ("map", 37, True)]
    assert drawers_to_close(stack, available=650) == ["radar", "map"]
