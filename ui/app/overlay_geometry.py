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


def stack_gap(height: int, gap: int = 6) -> int:
    """Gap below a stacked overlay; shrinks with a drawer that is sliding closed."""
    return min(height, gap)


def stack_extent(heights: list[int], gap: int = 6) -> int:
    """Total height of overlays stacked top-down with ``stack_gap`` spacing."""
    return sum(h + stack_gap(h, gap) for h in heights)


def drawers_to_close(
    stack: list[tuple[str, int, bool]],
    available: int,
    gap: int = 6,
) -> list[str]:
    """Pick drawers to close so the stack fits in ``available`` pixels.

    ``stack`` is ``(key, height, closable)`` in the order the drawers were
    opened, oldest first. Closable drawers are closed oldest first until the
    rest fits. If it cannot fit even with every closable drawer closed, all
    closable drawers are returned.
    """
    total = stack_extent([h for _key, h, _closable in stack], gap)
    out = []
    for key, h, closable in stack:
        if total <= available:
            break
        if closable:
            out.append(key)
            total -= h + stack_gap(h, gap)
    return out
