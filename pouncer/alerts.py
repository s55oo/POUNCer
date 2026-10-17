"""Rare-CQ + new-multiplier alert for the window (the phone page has its own).

A station gets one alert per band: the first time it shows up in the list as
a rare CQ (⚡) that is also a new multiplier. What shows up in the first
``warmup_s`` after the start (the old spots of SH/DX, the run history coming
in) is taken as known - no burst of alerts at start, like the phone page
does when it opens.
"""

from __future__ import annotations

import sys
import threading
import time


class AlertWatch:
    def __init__(self, warmup_s: float = 30.0, clock=time.monotonic) -> None:
        self._seen: set = set()
        self._clock = clock
        self._warmup_s = warmup_s
        self._start = clock()

    def fresh(self, items: list[tuple[tuple, object]]) -> list:
        """``items`` = [(key, spot), ...] of the alert-worthy rows now; returns
        the spots not alerted before (none while warming up)."""
        out = []
        for key, spot in items:
            if key not in self._seen:
                self._seen.add(key)
                out.append(spot)
        if self._clock() - self._start < self._warmup_s:
            return []
        return out


def beep(root=None) -> None:
    """Two short 1 kHz beeps; never blocks the window."""
    if sys.platform == "win32":
        def run() -> None:
            try:
                import winsound
                for _ in range(2):
                    winsound.Beep(1000, 120)
            except Exception:  # noqa: BLE001 - no sound device: just no beep
                pass
        threading.Thread(target=run, name="alert-beep", daemon=True).start()
    elif root is not None:
        try:
            root.bell()
        except Exception:  # noqa: BLE001
            pass
