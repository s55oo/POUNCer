"""cache/nonPublishedSpots.log (v0.7.218): spots that passed your filters but
never went out on port 10000 - to learn from and tune the spot collector.

Every live spot is noted as it comes in (``seen``, from the feed threads),
every spot sent to port 10000 too (``published``). ``WAIT_S`` later each
spot is looked at once (``flush``, on the UI thread): it counts as sent when
a spot of the same station went out (the same call within ``KHZ``, or the
same skimmer within ``BUSTED_KHZ`` under a corrected call) from ``WAIT_S``
before it to ``WAIT_S`` after it - a merged copy counts as sent. A spot your
own filters keep out (Spotters / Spotted, modes, FT8 / FT4, beacons, WARC),
a spot of your own call and an old one (sh/dx history) are not written:
those are by design. What is left is written with the last reason known for
it (the collector's evaluation, the PSK Reporter check) - "no reason noted"
= the collector dropped it without saying why.

One line per spot, tab separated:
received UTC, source, call, kHz, mode, spotter, reason, raw line.
Nothing is written while the cluster server is off or Relay is off (then
nothing is published at all). The file is kept below ``MAX_BYTES`` (the
older half moves to nonPublishedSpots.log.1).
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

FILE = "nonPublishedSpots.log"
WAIT_S = 120            # a spot is judged this long after it came in
KHZ = 1.0               # a spot of the same call this close went out = sent
BUSTED_KHZ = 0.2        # the same skimmer this close under another call = sent (corrected)
MAX_BYTES = 5_000_000
MAX_PENDING = 20000
HEADER = ("# POUNCer - spots that passed your filters but were not sent to port 10000\n"
          "# received UTC\tsource\tcall\tkHz\tmode\tspotter\treason\traw line\n")


class UnpublishedLog:
    def __init__(self, folder: str, clock=time.time) -> None:
        self.path = os.path.join(folder, FILE) if folder else ""
        self.clock = clock
        self._seen: collections.deque = collections.deque(maxlen=MAX_PENDING)
        self._pubs: collections.deque = collections.deque()
        self._by_call: dict = {}       # call -> [(t, kHz)]
        self._by_spotter: dict = {}    # spotter -> [(t, kHz)]
        self._why: dict = {}
        self._lock = threading.Lock()
        self.written = 0

    # ---- input --------------------------------------------------------------
    def seen(self, spot) -> None:
        """A live spot came in (any thread)."""
        if getattr(spot, "history", False):
            return
        self._seen.append((self.clock(), spot))

    def published(self, spot) -> None:
        """A spot went out on port 10000 (UI thread)."""
        t, call = self.clock(), (spot.dxcall or "").upper()
        khz, spotter = float(spot.freq_khz), (spot.spotter or "").upper()
        self._pubs.append((t, call, spotter))
        self._by_call.setdefault(call, []).append((t, khz))
        self._by_spotter.setdefault(spotter, []).append((t, khz))

    def note(self, spot, why: str) -> None:
        """The latest known reason for this spot (collector, PSK check)."""
        key = ((spot.dxcall or "").upper(), round(float(spot.freq_khz), 1),
               (spot.spotter or "").upper())
        with self._lock:
            self._why[key] = why
            if len(self._why) > MAX_PENDING:
                for k in list(self._why)[:MAX_PENDING // 2]:
                    del self._why[k]

    # ---- judging ------------------------------------------------------------
    def was_sent(self, t: float, call: str, khz: float, spotter: str) -> bool:
        for pt, pkhz in self._by_call.get(call, ()):
            if abs(pt - t) <= WAIT_S and abs(pkhz - khz) <= KHZ:
                return True
        for pt, pkhz in self._by_spotter.get(spotter, ()):
            if 0 <= pt - t <= WAIT_S and abs(pkhz - khz) <= BUSTED_KHZ:
                return True
        return False

    def flush(self, wanted, active: bool = True, now: float | None = None) -> int:
        """Judge the spots older than WAIT_S; write the unsent wanted ones.
        ``wanted(spot) -> bool``: your filters (and not your own call).
        ``active``: the cluster server + Relay are on (else drop all)."""
        now = self.clock() if now is None else now
        out = []
        while self._seen and now - self._seen[0][0] >= WAIT_S:
            t, spot = self._seen.popleft()
            if not active:
                continue
            call = (spot.dxcall or "").upper()
            spotter = (spot.spotter or "").upper()
            khz = float(spot.freq_khz)
            if self.was_sent(t, call, khz, spotter):
                continue
            try:
                if not wanted(spot):
                    continue
            except Exception:  # noqa: BLE001 - a bad spot must not stop the log
                continue
            with self._lock:
                why = self._why.get((call, round(khz, 1), spotter), "no reason noted")
            raw = " ".join(str(getattr(spot, "raw_line", "") or "").split())
            out.append("\t".join((
                time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(t)),
                str(getattr(spot, "source", "") or "?"), call, f"{khz:.1f}",
                (spot.mode or "-"), spotter, why, raw)))
        while self._pubs and now - self._pubs[0][0] > 3 * WAIT_S:
            pt, call, spotter = self._pubs.popleft()
            for index, key in ((self._by_call, call), (self._by_spotter, spotter)):
                rows = index.get(key)
                if rows:
                    rows.pop(0)
                    if not rows:
                        del index[key]
        if out:
            self._write(out)
        return len(out)

    def _write(self, lines: list) -> None:
        if not self.path:
            return
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                os.replace(self.path, self.path + ".1")
            new = not os.path.exists(self.path)
            with open(self.path, "a", encoding="utf-8") as fh:
                if new:
                    fh.write(HEADER)
                fh.write("\n".join(lines) + "\n")
            self.written += len(lines)
        except OSError as exc:
            log.warning("%s: %s", FILE, exc)
