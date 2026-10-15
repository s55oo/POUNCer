"""N1MM's CW speed (WPM), read from the speed box of the Entry Window.

N1MM doesn't broadcast its keyer speed (RadioInfo has none), but every
Entry Window shows it in a small spin box. POUNCer reads that box's text
(WM_GETTEXT, like the callsign box) once a second - for radio A and B -
and reports changes, so the main window, the compact bar and the phone show
the speed N1MM really has, and a popup shows the new speed when it changes.

Finding the box: the Entry Window has many text boxes. Candidates are the
visible ones holding just a number (5 - 99). A spin box (box + arrows) wins
over a plain box (a zone typed in the exchange box is a plain box). If
that isn't clear, POUNCer learns it: after its own PgUp / PgDn (WPM + / -)
the box whose number moved that way is the speed box. What was found is
written to ``cache/cwspeed.txt`` (to check / report).
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time

log = logging.getLogger(__name__)

MIN_WPM, MAX_WPM = 5, 99
POLL_S = 1.0
FAST_POLL_S = 0.25          # just after WPM + / -: look more often ...
FAST_FOR_S = 2.5            # ... for this long


def pick(cands: list[dict]) -> dict | None:
    """The speed box among the candidates, when it's clear: the only spin
    box; else the only number box at all. None = not clear (learn it)."""
    cands = [c for c in cands if MIN_WPM <= c.get("value", -1) <= MAX_WPM]
    spins = [c for c in cands if c.get("updown")]
    if len(spins) == 1:
        return spins[0]
    if not spins and len(cands) == 1:
        return cands[0]
    return None


def learn(before: dict[int, int], after: list[dict], direction: int) -> dict | None:
    """After WPM + (direction 1) / - (-1): the one box whose number moved
    that way (by 1 - 10). None if none or several moved."""
    moved = []
    for c in after:
        old = before.get(c["hwnd"])
        if old is None or not MIN_WPM <= c["value"] <= MAX_WPM:
            continue
        step = (c["value"] - old) * direction
        if 1 <= step <= 10:
            moved.append(c)
    return moved[0] if len(moved) == 1 else None


class SpeedReader(threading.Thread):
    """Polls the speed box of each radio's Entry Window.

    ``windows()`` -> {radio nr: Entry Window hwnd}. Changes go to
    ``changes`` as (nr, old wpm or None, new wpm); ``wpm`` holds the current
    speeds. The win32 functions are passed in (tests use fakes)."""

    def __init__(self, windows, number_boxes, window_text, is_window,
                 report_path: str = "", poll_s: float = POLL_S, describe=None) -> None:
        super().__init__(daemon=True, name="cw-speed")
        self.windows = windows
        self._boxes = number_boxes
        self._text = window_text
        self._is_window = is_window
        self.report_path = report_path
        self.poll_s = poll_s
        self._describe = describe       # all controls of a window (for the report)
        self.changes: "queue.Queue[tuple[int, int | None, int]]" = queue.Queue()
        self.wpm: dict[int, int] = {}
        self.box: dict[int, int] = {}            # radio -> speed box hwnd
        self.how: dict[int, str] = {}            # radio -> "spin box" / "learned" ...
        self._entry: dict[int, int] = {}         # radio -> Entry Window it was found in
        self._expect: dict[int, tuple[int, dict[int, int], float]] = {}
        self._fast_until = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._reported: set[tuple] = set()

    # ---- control ----------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def expect(self, nr: int, direction: int) -> None:
        """POUNCer just pressed PgUp (1) / PgDn (-1) for radio ``nr``: look
        again soon, and learn the box if it isn't known yet."""
        hwnd = self.windows().get(nr)
        if nr not in self.box and hwnd:
            before = {c["hwnd"]: c["value"] for c in self._boxes(hwnd)}
            self._expect[nr] = (direction, before, time.time() + FAST_FOR_S)
        self._fast_until = time.time() + FAST_FOR_S
        self._wake.set()

    def status(self, nr: int) -> str:
        if nr in self.wpm:
            return f"{self.wpm[nr]} WPM"
        if self.windows().get(nr) and nr in self._entry and nr not in self.box:
            return "speed box not found yet (press WPM + once)"
        return ""

    # ---- polling ----------------------------------------------------------

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - never stop reading
                log.exception("CW speed read failed")
            fast = time.time() < self._fast_until
            self._wake.wait(FAST_POLL_S if fast else self.poll_s)
            self._wake.clear()

    def poll_once(self) -> None:
        wins = self.windows()
        for nr in list(self.wpm):
            if nr not in wins:
                self._forget(nr)
        for nr, entry in wins.items():
            if not entry:
                continue
            if self._entry.get(nr) != entry or (nr in self.box and not self._is_window(self.box[nr])):
                self._forget(nr)          # N1MM restarted / other window
                self._entry[nr] = entry
            if nr not in self.box:
                self._find(nr, entry)
            box = self.box.get(nr)
            if box is None:
                continue
            text = self._text(box)
            if text is None:
                continue                  # N1MM busy: try again next time
            text = text.strip()
            if not text.isdigit() or not MIN_WPM <= int(text) <= MAX_WPM:
                continue
            self._set(nr, int(text))

    def _set(self, nr: int, wpm: int) -> None:
        old = self.wpm.get(nr)
        if old != wpm:
            self.wpm[nr] = wpm
            self.changes.put((nr, old, wpm))

    def _forget(self, nr: int) -> None:
        self.box.pop(nr, None)
        self.wpm.pop(nr, None)
        self.how.pop(nr, None)
        self._entry.pop(nr, None)

    def _find(self, nr: int, entry: int) -> None:
        cands = self._boxes(entry)
        got, how = pick(cands), "spin box"
        exp = self._expect.get(nr)
        if got is None and exp is not None:
            direction, before, until = exp
            got, how = learn(before, cands, direction), "learned from WPM +/-"
            if got is None and time.time() > until:
                self._expect.pop(nr, None)
        if got is None:
            self._report(nr, entry, cands, None, "")
            return
        self._expect.pop(nr, None)
        self.box[nr] = got["hwnd"]
        self.how[nr] = how
        self._report(nr, entry, cands, got, how)
        log.info("CW speed box of radio %s: %s (%s)", nr, got.get("hwnd"), how)

    def _report(self, nr, entry, cands, got, how) -> None:
        """What was found, to ``cache/cwspeed.txt`` (only when it changes)."""
        if not self.report_path:
            return
        key = (nr, entry, got["hwnd"] if got else None,
               tuple(sorted((c["hwnd"], c.get("updown")) for c in cands)))
        if key in self._reported:
            return
        self._reported.add(key)
        lines = [time.strftime("%Y-%m-%d %H:%M:%S"),
                 f"radio {'AB'[nr - 1] if nr in (1, 2) else nr}, Entry Window {entry}: "
                 + (f"speed box {got['hwnd']} = {got['value']} WPM ({how})" if got
                    else "speed box NOT found yet")]
        for c in cands:
            lines.append(
                f"  box {c['hwnd']}: {c['value']:>3}  {'spin box' if c.get('updown') else 'plain box'}"
                f"  {c.get('cls', '')} in {c.get('parent_cls', '')}  at {c.get('rect', '')}")
        if not cands:
            lines.append("  (no box with only a number in this Entry Window)")
        if got is None and self._describe is not None:
            lines.append("  all controls of the Entry Window:")
            try:
                lines += ["    " + x for x in self._describe(entry)]
            except Exception:  # noqa: BLE001 - only a report
                pass
        try:
            os.makedirs(os.path.dirname(self.report_path) or ".", exist_ok=True)
            big = os.path.exists(self.report_path) and os.path.getsize(self.report_path) > 200_000
            with open(self.report_path, "w" if big else "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n\n")
        except OSError:
            pass
