"""Tests for collision-free floating overlay placement."""

from ui.app.overlay_geometry import bottom_left_y_avoiding, rectangles_overlap


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
