"""Remember the main window between starts: size and position, maximised,
column widths and the sort - in ``cache/window.json`` (this PC only).

Pure helpers (no tkinter) so they are testable anywhere; ``app.py`` reads
and applies them at start and saves them when POUNCer closes or restarts.
"""

from __future__ import annotations

import json
import os
import re

_GEOM = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")
MIN_W, MIN_H = 420, 300
COL_MIN, COL_MAX = 20, 1200


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: str, data: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _offset(text: str) -> int:
    """Tk position "+40", or "+-4" = 4 px left of / above the screen (how
    Tk reports a window pushed a bit off the edge). A plain "-40" (older
    window.json files) is read as -40 too."""
    sign = -1 if text.startswith("-") else 1
    return sign * int(text[1:])


def scale_geometry(geom: str, factor: float) -> str:
    """"WxH+X+Y" with the size times ``factor`` (saved at another display
    scaling, e.g. before POUNCer drew sharp at 150 %). Position kept."""
    m = _GEOM.match(str(geom or "").strip())
    if not m or abs(factor - 1.0) < 0.05:
        return geom
    w, h = round(int(m.group(1)) * factor), round(int(m.group(2)) * factor)
    return f"{w}x{h}{m.group(3)}{m.group(4)}"


def fit_geometry(geom: str, screen_w: int, screen_h: int) -> str | None:
    """A saved "WxH+X+Y" that still fits this screen (a second monitor may
    be gone): size limited to the screen, window moved back onto it so at
    least its title bar can be grabbed. None if not usable."""
    m = _GEOM.match(str(geom or "").strip())
    if not m or screen_w <= 0 or screen_h <= 0:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    x, y = _offset(m.group(3)), _offset(m.group(4))
    w = max(MIN_W, min(w, screen_w))
    h = max(MIN_H, min(h, screen_h))
    # keep the title bar on screen (multi-monitor negative x is fine if
    # part of the window stays visible on the main screen)
    x = max(-(w - 100), min(x, screen_w - 100))
    y = max(0, min(y, screen_h - 60))
    # always "+x+y": in Tk "-4" would mean 4 px from the RIGHT edge, "+-4"
    # is 4 px left of the screen
    return f"{w}x{h}+{x}+{y}"


def column_widths(saved, columns) -> dict[str, int]:
    """Saved widths of known columns, within sane limits."""
    out = {}
    if isinstance(saved, dict):
        for col in columns:
            try:
                w = int(saved.get(col))
            except (TypeError, ValueError):
                continue
            out[col] = max(COL_MIN, min(w, COL_MAX))
    return out


def sort_choice(data: dict, columns, default: tuple[str, bool]) -> tuple[str, bool]:
    col = data.get("sort")
    if col in columns:
        return col, bool(data.get("reverse"))
    return default
