"""Band open? What skimmers on your own continent hear, per band.

Every live spot of the whole RBN feed (all skimmers, not only the ones the
list uses), the HTTP backup and the SSB cluster is counted when its
spotter is on your continent. Per band, over the last 10 minutes:

- ``dx``    different stations from OTHER continents heard (the band is
            open to DX from here),
- ``calls`` different stations heard at all,
- ``spots`` spots.

State: "open" = at least ``OPEN_DX`` DX stations; "local" = spots, but
(almost) no DX - only your continent is heard; "closed" = nothing.
A team slave gets the master's counts (it has no RBN connection itself).
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .bands import CONTEST_BANDS, band_from_khz

WINDOW_S = 600          # last 10 minutes
OPEN_DX = 5             # different DX stations = "open"


def spotter_call(spotter: str) -> str:
    """'DK9IP-#' / 'OH6BG-2' -> 'DK9IP'."""
    return str(spotter or "").split("-")[0].strip().upper()


HUE_LOCAL, HUE_DX = 28, 125    # orange (only our continent) ... green (all DX)


def dx_pct(counts: dict | None) -> int:
    """DX stations as a % of all different stations heard on the band."""
    if not counts or not counts.get("calls"):
        return 0
    return max(0, min(100, round(100 * counts.get("dx", 0) / counts["calls"])))


def share_color(pct: float) -> tuple[str, str]:
    """(background, text) from orange (0 % DX: only our continent) through
    yellow to green (100 % DX), in proportion; the text black or white,
    whichever reads better on it."""
    import colorsys
    t = max(0.0, min(1.0, pct / 100.0))
    hue = (HUE_LOCAL + (HUE_DX - HUE_LOCAL) * t) / 360.0
    rgb = [round(x * 255) for x in colorsys.hls_to_rgb(hue, 0.40, 0.75)]
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return "#%02x%02x%02x" % tuple(rgb), ("#1f1f1f" if lum > 140 else "#ffffff")


def state_of(counts: dict | None) -> str:
    if not counts or not counts.get("spots"):
        return "closed"
    return "open" if counts.get("dx", 0) >= OPEN_DX else "local"


class BandActivity:
    """Thread-safe counter. ``continent(call)`` -> 'EU' / 'NA' ... or None;
    ``home()`` -> our continent."""

    def __init__(self, continent, home, window_s: float = WINDOW_S, clock=time.time) -> None:
        self._continent = continent
        self._home = home
        self.window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._spots: dict[str, deque] = {}     # band -> (t, dx call, from another continent)
        self._skimmer_cont: dict[str, str | None] = {}
        self.on_count = None      # (band, t, call, dx) for every spot counted (timeline)

    def add(self, spot) -> None:
        """One live spot (any skimmer) - counted when its spotter is near."""
        band = band_from_khz(spot.freq_khz)
        if band not in CONTEST_BANDS:
            return
        sk = spotter_call(spot.spotter)
        if not sk:
            return
        cont = self._skimmer_cont.get(sk, "?")
        if cont == "?":
            cont = self._continent(sk)
            if len(self._skimmer_cont) > 5000:
                self._skimmer_cont.clear()
            self._skimmer_cont[sk] = cont
        home = self._home()
        if not cont or cont != home:
            return
        dx_cont = self._continent(spot.dxcall)
        t = getattr(spot, "received", None) or self._clock()
        far = bool(dx_cont and dx_cont != home)
        with self._lock:
            q = self._spots.setdefault(band, deque())
            q.append((t, spot.dxcall.upper(), far))
            if len(q) > 20000:
                q.popleft()
        if self.on_count is not None:
            try:
                self.on_count(band, t, spot.dxcall.upper(), far)
            except Exception:  # noqa: BLE001 - the lights must keep counting
                pass

    def summary(self) -> dict[str, dict]:
        """{band: {spots, calls, dx, state}} for every contest band."""
        cutoff = self._clock() - self.window_s
        out = {}
        with self._lock:
            for band in CONTEST_BANDS:
                q = self._spots.get(band)
                while q and q[0][0] < cutoff:
                    q.popleft()
                items = list(q or ())
                calls = {c for _, c, _ in items}
                dx = {c for _, c, far in items if far}
                c = {"spots": len(items), "calls": len(calls), "dx": len(dx)}
                c["state"] = state_of(c)
                c["pct"] = dx_pct(c)
                out[band] = c
        return out
