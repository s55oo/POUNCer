"""Flow layout: place items left to right, wrapping onto new rows.

Used by the Windows (Tk) window so its buttons and checkboxes move onto a
second / third row when the window is made narrow, instead of being cut
off at the right edge. Pure geometry, no tkinter import, so it is testable
anywhere.
"""

from __future__ import annotations


def flow_positions(
    sizes: list[tuple[int, int]],
    width: int,
    padx: int = 6,
    pady: int = 4,
    gaps: list[int] | None = None,
) -> tuple[list[tuple[int, int]], int]:
    """Top-left (x, y) for each (w, h) item, and the total height.

    Items go left to right; one that would pass ``width`` starts a new row
    (an item wider than ``width`` gets a row of its own). ``gaps`` adds extra
    space before an item when it is not first on its row (to separate
    groups). Rows are as tall as their tallest item.
    """
    gaps = gaps or [0] * len(sizes)
    out: list[tuple[int, int]] = []
    x = y = row_h = 0
    for (w, h), gap in zip(sizes, gaps):
        if x > 0:
            x += gap
            if x + w > width:
                x = 0
                y += row_h + pady
                row_h = 0
        out.append((x, y))
        x += w + padx
        row_h = max(row_h, h)
    return out, (y + row_h if sizes else 0)
