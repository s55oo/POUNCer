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


def is_skimmer(spotter: str) -> bool:
    """RBN / skimmer nodes end in -# (CW, RTTY) or -@ (digital); a person
    spotting on a cluster has a plain call (DL1ABC, DL1ABC-2)."""
    s = (spotter or "").strip().upper()
    return s.endswith("-#") or s.endswith("-@") or s.endswith("#")


def base_call(call: str) -> str:
    """S55OO/P, EA8/S55OO -> the longest part (the call itself)."""
    parts = [p for p in (call or "").upper().split("/") if p]
    return max(parts, key=len) if parts else ""


class SelfSpotWatch:
    """MY call spotted on a DX cluster by a person, not a skimmer (my own
    self-spot too - handy to test it): tell me once per spotter and
    frequency (``repeat_s``). Thread-safe."""

    @staticmethod
    def is_self(spotter: str, my_call: str) -> bool:
        return bool(my_call) and base_call((spotter or "").split("-")[0]) == base_call(my_call)

    def __init__(self, repeat_s: float = 900, clock=time.time) -> None:
        self.repeat_s = repeat_s
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, int], float] = {}

    def check(self, dxcall: str, spotter: str, freq_khz: float, my_call: str) -> bool:
        me = base_call(my_call)
        if not me or base_call(dxcall) != me or is_skimmer(spotter):
            return False
        key = ((spotter or "").upper(), int(round(float(freq_khz))))
        now = self._clock()
        with self._lock:
            if now - self._seen.get(key, float("-inf")) < self.repeat_s:
                return False
            self._seen[key] = now
            if len(self._seen) > 500:
                for k in [k for k, t in self._seen.items() if now - t > self.repeat_s]:
                    del self._seen[k]
        return True


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
