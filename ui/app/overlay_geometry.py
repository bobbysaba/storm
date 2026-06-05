"""Small geometry helpers for collision-free floating overlays."""

from __future__ import annotations


def rectangles_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    """Return whether two x, y, width, height rectangles overlap."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return (
        ax < bx + bw
        and ax + aw > bx
        and ay < by + bh
        and ay + ah > by
    )


def bottom_left_y_avoiding(
    container_height: int,
    margin: int,
    width: int,
    height: int,
    obstacle: tuple[int, int, int, int] | None,
    gap: int = 6,
) -> int:
    """Place a bottom-left overlay above an obstacle only when they collide."""
    y = container_height - height - margin
    if obstacle is None:
        return y
    candidate = (margin, y, width, height)
    if rectangles_overlap(candidate, obstacle):
        return max(margin, obstacle[1] - gap - height)
    return y
