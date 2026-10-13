"""Diagnostic log: what POUNCer did, step by step, with timings.

Each tap (tile, NEXT, main window) becomes one line such as::

    14:20:01.234 TAP HB9CBR/P 14031.0 radio A | focus ok +12 | qsy udp +15 |
      n1mm 14031.0 +480 | wipe +735 | set "HB9CBR/P" +936 | box "HB9CBR/P" ok +1088

(+ms since the tap), plus key presses, RBN connection changes and the
settings that matter for N1MM. Kept in memory (last 500 lines) and in
``cache/diag.log``; shown at ``/diag`` (phone / browser) with a Copy button
so a problem from a contest can simply be sent on. No passwords, only
callsigns, frequencies and timings.
"""

from __future__ import annotations

import collections
import os
import threading
import time

MAX_LINES = 500
MAX_FILE = 2_000_000        # bytes; at start a bigger file keeps only its end


def _stamp(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


class Trace:
    """One action (a tap) and its steps; written as one line by ``done``."""

    def __init__(self, log: "DiagLog", kind: str, text: str, clock=time.time) -> None:
        self._log, self._clock = log, clock
        self.t0 = clock()
        self.head = f"{_stamp(self.t0)} {kind} {text}"
        self.steps: list[str] = []
        self.closed = False

    def step(self, name: str, detail: str = "") -> None:
        ms = int(round((self._clock() - self.t0) * 1000))
        self.steps.append(f"{name}{' ' + detail if detail else ''} +{ms}")

    def done(self, result: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        if result:
            self.step(result)
        self._log.add(" | ".join([self.head, *self.steps]))


class DiagLog:
    def __init__(self, path: str = "", clock=time.time) -> None:
        self.path = path
        self._clock = clock
        self._lines: collections.deque[str] = collections.deque(maxlen=MAX_LINES)
        self._lock = threading.Lock()
        if path:
            self._trim_file()

    def _trim_file(self) -> None:
        try:
            if os.path.getsize(self.path) > MAX_FILE:
                with open(self.path, "rb") as fh:
                    fh.seek(-MAX_FILE // 2, os.SEEK_END)
                    tail = fh.read()
                with open(self.path, "wb") as fh:
                    fh.write(tail[tail.find(b"\n") + 1:])
        except OSError:
            pass

    def add(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def event(self, kind: str, text: str) -> None:
        self.add(f"{_stamp(self._clock())} {kind} {text}")

    def trace(self, kind: str, text: str) -> Trace:
        return Trace(self, kind, text, self._clock)

    def text(self) -> str:
        with self._lock:
            return "\n".join(self._lines)
