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
GROUPS = ("RBN", "TELNET", "SSB", "HTTP", "N1MM", "TEAM", "WEB", "PSKR", "SERVER", "FT8", "BEACON")
DIGITAL = ("FT8", "FT4")


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


def kept_out(group: str, where: str, spot, why: str) -> None:
    """One line in the console's FT8 / BEACON group: a spot POUNCer keeps
    out of the list and of the cluster server (port 10000)."""
    if _sink is None:
        return
    spotter = getattr(spot, "spotter", "") or "?"
    emit(f"{group} {where}", f"{getattr(spot, 'dxcall', '?'):<10} "
                             f"{float(getattr(spot, 'freq_khz', 0) or 0):>8.1f} "
                             f"{(getattr(spot, 'mode', '') or '-'):<4} de {spotter:<10} {why}")


def digi_out(where: str, spot, why: str) -> None:
    """One line in the console's FT8 group: an FT8 / FT4 spot POUNCer keeps
    out (of the list and of the cluster server on port 10000), or holds
    back until PSK Reporter answers. ``where``: the feed or step (RBN,
    cluster, server, PSKR ...)."""
    kept_out("FT8", where, spot, why)


def group_of(source: str) -> str:
    s = source.upper()
    if s.startswith("FT8"):             # FT8 / FT4 spots kept out / held (digi_out)
        return "FT8"
    if s.startswith("BEACON"):          # beacons kept out (kept_out)
        return "BEACON"
    if s.startswith("N1MM"):
        return "N1MM"
    if s.startswith("TEAM"):
        return "TEAM"
    if s.startswith(("SERVER", "RELAY")):   # POUNCer's own cluster server (N1MM connects)
        return "SERVER"
    if s.startswith("TELNET"):          # the CW feed from the telnet server (azure)
        return "TELNET"
    if s.startswith("SELF"):            # the spots-of-my-call connection: same server
        return "TELNET"
    if s.startswith("WEB"):
        return "WEB"
    if s.startswith(("HTTP", "HAMQTH")):
        return "HTTP"
    if s.startswith("PSKR"):
        return "PSKR"
    if "CLUSTER" in s or s.startswith("SSB"):
        return "SSB"
    return "RBN"
