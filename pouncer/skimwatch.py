"""Is our own skimmer still connected? (S53WW at home, TK0C on an expedition)

Every spot of the whole feed passes ``seen(spotter)``. ``quiet()`` names the
calls listed in ``[rbn] spotter`` (TK0C = any of TK0C-1, TK0C-2 ...) that have
sent nothing for ``quiet_s`` while the feed itself is alive: the skimmer is
offline or its connection to RBN is down - a crashed SDR / CW Skimmer at
night shows at once instead of in the morning. The app puts it in the check
line (amber, also ⚠ on the phone) and the log. Zones (ZONE14) aren't watched.
"""

from __future__ import annotations

import threading
import time


class SkimmerWatch:
    def __init__(self, calls, quiet_s: float = 180, clock=time.time) -> None:
        self.calls = tuple(c.upper() for c in calls if c)
        self.quiet_s = quiet_s
        self.clock = clock
        self.started = clock()           # give them time to speak after start
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}   # listed call (TK0C) -> last spot

    def seen(self, spotter: str) -> None:
        sp = (spotter or "").upper()
        for c in self.calls:
            if sp.startswith(c):
                with self._lock:
                    self._last[c] = self.clock()
                return

    def quiet(self, feed_alive: bool = True) -> list[tuple[str, float]]:
        """[(skimmer, seconds quiet)] while the feed works (else [] - then it's
        RBN, not the skimmer)."""
        if not feed_alive or not self.calls:
            return []
        now = self.clock()
        with self._lock:
            last = dict(self._last)
        return [(c, now - last.get(c, self.started)) for c in self.calls
                if now - last.get(c, self.started) >= self.quiet_s]

    @staticmethod
    def text(items: list[tuple[str, float]]) -> str:
        return ", ".join(f"{name} sends nothing ({int(s // 60)} min)" for name, s in items)
