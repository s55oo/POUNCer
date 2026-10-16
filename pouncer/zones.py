"""Who hears me, what I hear - per band and CQ zone (1-40).

Fed with every live spot of the whole feed (RBN telnet, HTTP backup, SSB
cluster - the same ``on_raw`` hook as the band-open lights):

- **hears me**: a spot OF MY CALL - the skimmer's CQ zone heard me on that
  band (best SNR, how many skimmers, how long ago). Needs you calling CQ
  (skimmers only spot CQ / TEST calls).
- **I hear**: a spot BY A SKIMMER ON MY CONTINENT - the spotted station's
  CQ zone is coming in here on that band (best SNR, how many stations).

Zones come from the country file: a big country (W, VE, UA, VK, BY ...)
gets the zone cty.dat gives the callsign, which may be the country's main
zone unless cty lists the call. Thread-safe; ``table(window_s)`` for the
page.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .bandopen import spotter_call
from .bands import CONTEST_BANDS, band_from_khz

ZONES = 40
KEEP_S = 3600            # an hour kept; the page shows 15 min (or 30 / 60)
MAX_EVENTS = 50000


def _base(call: str) -> str:
    """'S50XX/P' -> 'S50XX' (portable / mobile suffixes don't matter here)."""
    parts = [p for p in str(call or "").upper().split("/") if p]
    return max(parts, key=len) if parts else ""


class ZoneMap:
    """``entity(call)`` -> cty Entity (.cq, .continent) or None;
    ``home()`` -> our continent; ``own()`` -> our callsign."""

    def __init__(self, entity, home, own, clock=time.time) -> None:
        self._entity = entity
        self._home = home
        self._own = own
        self._clock = clock
        self._lock = threading.Lock()
        # (t, band, zone, snr, other call) - other = skimmer / spotted station
        self._me: deque = deque()
        self._heard: deque = deque()
        self._skimmer_cache: dict[str, tuple[int | None, str | None]] = {}

    def _skimmer(self, call: str) -> tuple[int | None, str | None]:
        got = self._skimmer_cache.get(call)
        if got is None:
            ent = self._entity(call)
            got = (getattr(ent, "cq", None), getattr(ent, "continent", None)) if ent else (None, None)
            if len(self._skimmer_cache) > 5000:
                self._skimmer_cache.clear()
            self._skimmer_cache[call] = got
        return got

    def add(self, spot) -> None:
        band = band_from_khz(spot.freq_khz)
        if band not in CONTEST_BANDS:
            return
        sk = spotter_call(spot.spotter)
        if not sk:
            return
        t = getattr(spot, "received", None) or self._clock()
        snr = getattr(spot, "snr", None)
        own = _base(self._own() or "")
        dx = _base(spot.dxcall)
        sk_zone, sk_cont = self._skimmer(sk)
        if own and dx == own:
            if sk_zone:
                self._push(self._me, (t, band, int(sk_zone), snr, sk))
            return
        if sk_cont and sk_cont == self._home():
            ent = self._entity(spot.dxcall)
            zone = getattr(ent, "cq", None) if ent else None
            if zone:
                self._push(self._heard, (t, band, int(zone), snr, dx))

    def _push(self, q: deque, item: tuple) -> None:
        with self._lock:
            q.append(item)
            if len(q) > MAX_EVENTS:
                q.popleft()

    @staticmethod
    def _sum(items, cutoff: float, now: float) -> dict[str, dict[int, dict]]:
        out: dict[str, dict[int, dict]] = {}
        for t, band, zone, snr, other in items:
            if t < cutoff:
                continue
            cell = out.setdefault(band, {}).setdefault(zone, {"snr": None, "n": 0, "calls": set(),
                                                              "age": None})
            cell["n"] += 1
            cell["calls"].add(other)
            if snr is not None and (cell["snr"] is None or snr > cell["snr"]):
                cell["snr"] = snr
            age = max(0, int((now - t) / 60))
            if cell["age"] is None or age < cell["age"]:
                cell["age"] = age
        for band in out.values():
            for cell in band.values():
                calls = sorted(cell["calls"])
                cell["calls"] = len(calls)
                cell["who"] = calls[:8]
        return out

    def table(self, window_s: float = 900) -> dict:
        """{"hears_me": {band: {zone: {snr, n, calls, who, age}}}, "i_hear": ...}."""
        now = self._clock()
        cutoff = now - window_s
        with self._lock:
            for q in (self._me, self._heard):
                while q and q[0][0] < now - KEEP_S:
                    q.popleft()
            me, heard = list(self._me), list(self._heard)
        return {"hears_me": self._sum(me, cutoff, now), "i_hear": self._sum(heard, cutoff, now)}
