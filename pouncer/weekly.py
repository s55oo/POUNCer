"""When the weekly downloads are due: every Friday (v0.7.187).

The country file (cty.dat) and the Super Check Partial list (MASTER.DTA)
come from S53M's server, which refreshes them for the weekend's contests.
A download is due when the last one was before the most recent Friday
00:00 UTC - so a PC that was off on Friday catches up at its next start.
"""

from __future__ import annotations

import time

FRIDAY = 4                      # time.gmtime().tm_wday: Monday = 0


def last_friday(now: float) -> float:
    """The most recent Friday 00:00 UTC at or before ``now`` (seconds)."""
    t = time.gmtime(now)
    midnight = now - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec) - (now % 1)
    return midnight - ((t.tm_wday - FRIDAY) % 7) * 86400


def due(last_done: float, now: float) -> bool:
    """True when nothing was fetched since the most recent Friday."""
    return last_done < last_friday(now)


def next_friday(now: float) -> float:
    return last_friday(now) + 7 * 86400


def read_stamp(path: str) -> float:
    try:
        with open(path, encoding="ascii") as fh:
            return float(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def write_stamp(path: str, when: float) -> None:
    import os
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(f"{when:.0f}")
    except OSError:
        pass
