"""Traffic tap for the console (View > Traffic console).

Every source calls ``emit(source, text)`` where its raw data arrives (RBN /
cluster lines, N1MM UDP packets in and out, the HTTP feed, the team, the
phone page). While the console is closed ``_sink`` is None and ``emit`` is a
single attribute check - nothing is formatted or kept. Opening the console
calls ``start()``; it drains the lines a few times a second and ``stop()``s
when closed.
"""

from __future__ import annotations

import collections
import time

MAX_LINES = 5000          # kept between two console refreshes at most
_sink: "collections.deque | None" = None
GROUPS = ("RBN", "SSB", "HTTP", "N1MM", "TEAM", "WEB")


def active() -> bool:
    return _sink is not None


def emit(source: str, text) -> None:
    sink = _sink
    if sink is None:
        return
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    sink.append((time.time(), source, str(text)))   # deque.append is thread-safe


def start(maxlen: int = MAX_LINES) -> None:
    global _sink
    _sink = collections.deque(maxlen=maxlen)


def stop() -> None:
    global _sink
    _sink = None


def drain() -> list[tuple[float, str, str]]:
    sink = _sink
    out = []
    if sink is None:
        return out
    while True:
        try:
            out.append(sink.popleft())
        except IndexError:
            return out


def squeeze(text: str, limit: int = 400) -> str:
    """One line for the console: whitespace collapsed, long XML / JSON cut."""
    t = " ".join(str(text).split())
    return t if len(t) <= limit else t[:limit] + " ..."


def group_of(source: str) -> str:
    s = source.upper()
    if s.startswith("N1MM"):
        return "N1MM"
    if s.startswith("TEAM"):
        return "TEAM"
    if s.startswith("WEB"):
        return "WEB"
    if s.startswith("HTTP"):
        return "HTTP"
    if "CLUSTER" in s or s.startswith("SSB"):
        return "SSB"
    return "RBN"
