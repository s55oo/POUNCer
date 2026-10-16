"""Band opening timeline: spots per band in 10-minute boxes, 48 hours back.

Counts the same spots as the band-open lights (every live spot by a skimmer
on your continent): per band and 10 minutes the spots, the different
stations and the different DX stations (other continents). Kept on disk in
``cache/timeline.json`` (saved every few minutes and at exit), so a restart
doesn't lose the picture. ``grid(hours)`` gives the page its boxes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from .bands import CONTEST_BANDS

log = logging.getLogger(__name__)

SLOT_S = 600             # 10 minutes
KEEP_S = 48 * 3600       # two days (a 48 h contest)
SAVE_EVERY_S = 300


class Timeline:
    def __init__(self, path: str = "", clock=time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        # slot start (epoch s) -> band -> [spots, calls, dx]; calls / dx are
        # sets while the slot is running, counts once it is over (small file)
        self._slots: dict[int, dict[str, list]] = {}
        self._saved = 0.0
        self._load()

    @staticmethod
    def slot_of(t: float) -> int:
        return int(t // SLOT_S * SLOT_S)

    def add(self, band: str, t: float, call: str, dx: bool) -> None:
        if band not in CONTEST_BANDS:
            return
        slot = self.slot_of(t)
        with self._lock:
            cell = self._slots.setdefault(slot, {}).setdefault(band, [0, set(), set()])
            cell[0] += 1
            if isinstance(cell[1], set):
                cell[1].add(call)
                if dx:
                    cell[2].add(call)
        if self.path and self._clock() - self._saved > SAVE_EVERY_S:
            self.save()

    def _prune(self, now: float) -> None:
        cutoff = now - KEEP_S
        current = self.slot_of(now)
        for slot in [s for s in self._slots if s < cutoff]:
            del self._slots[slot]
        for slot, bands in self._slots.items():        # finished: keep counts only
            if slot < current:
                for c in bands.values():
                    if isinstance(c[1], set):
                        c[1], c[2] = len(c[1]), len(c[2])

    @staticmethod
    def _n(v) -> int:
        return len(v) if isinstance(v, set) else int(v)

    def grid(self, hours: int = 12) -> dict:
        """{"slots": [start, ...] (oldest first, 10 min each, up to now),
        "bands": [...], "cells": {band: [[spots, stations, dx], ...]}}."""
        hours = max(1, min(48, int(hours)))
        now = self._clock()
        last = self.slot_of(now)
        first = last - (hours * 3600 // SLOT_S - 1) * SLOT_S
        slots = list(range(first, last + 1, SLOT_S))
        with self._lock:
            self._prune(now)
            cells = {b: [] for b in CONTEST_BANDS}
            for s in slots:
                row = self._slots.get(s, {})
                for b in CONTEST_BANDS:
                    c = row.get(b)
                    cells[b].append([c[0], self._n(c[1]), self._n(c[2])] if c else [0, 0, 0])
        return {"slots": slots, "slot_s": SLOT_S, "bands": list(CONTEST_BANDS), "cells": cells,
                "now": now}

    # ---- disk -------------------------------------------------------------
    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.info("timeline: %s", exc)
            return
        for slot, bands in (data.get("slots") or {}).items():
            try:
                s = int(slot)
            except ValueError:
                continue
            for b, v in (bands or {}).items():
                if b in CONTEST_BANDS and isinstance(v, list) and len(v) == 3:
                    self._slots.setdefault(s, {})[b] = [
                        int(v[0]), set(v[1]) if isinstance(v[1], list) else int(v[1]),
                        set(v[2]) if isinstance(v[2], list) else int(v[2])]
        self._prune(self._clock())

    def save(self) -> None:
        if not self.path:
            return
        self._saved = self._clock()
        with self._lock:
            self._prune(self._saved)
            data = {"slots": {str(s): {b: [c[0], sorted(c[1]) if isinstance(c[1], set) else c[1],
                                               sorted(c[2]) if isinstance(c[2], set) else c[2]]
                                           for b, c in bands.items()}
                              for s, bands in self._slots.items()}}
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as exc:
            log.info("timeline not saved: %s", exc)
