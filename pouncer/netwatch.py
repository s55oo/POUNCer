"""Is the internet there? One clear answer instead of every connection
failing on its own.

A thread tries a plain TCP connect to a few public DNS servers by IP
(1.1.1.1, 8.8.8.8, 9.9.9.9 port 53 - no login, nothing sent) every
``every_s`` (``down_every_s`` while down). Up = any answers within
``timeout_s``. Down only after ``fails_needed`` rounds in a row with no
answer (one lost round is not an outage). ``down_since`` / ``back_after``
tell the app what to show; ``on_change(up)`` is called from the thread.

A spot that arrived over the internet is proof too: ``alive()`` marks the
internet up at once (no waiting for the next probe round).
"""

from __future__ import annotations

import logging
import socket
import threading
import time

log = logging.getLogger(__name__)

PROBES = (("1.1.1.1", 53), ("8.8.8.8", 53), ("9.9.9.9", 53))


def _tcp_ok(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class NetWatch:
    def __init__(self, probes=PROBES, every_s: float = 30, down_every_s: float = 10,
                 timeout_s: float = 4, fails_needed: int = 2, on_change=None,
                 connect=_tcp_ok) -> None:
        self.probes = tuple(probes)
        self.every_s, self.down_every_s = every_s, down_every_s
        self.timeout_s, self.fails_needed = timeout_s, fails_needed
        self.on_change = on_change
        self._connect = connect
        self.up = True
        self.down_since: float | None = None   # time.time() when it went down
        self.back_after: float = 0.0           # how long the last outage was (s)
        self.back_at: float = 0.0
        self._fails = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---- the answer -----------------------------------------------------

    def probe_once(self) -> bool:
        """One round: any probe answers? (Also called by the thread.)"""
        ok = any(self._connect(h, p, self.timeout_s) for h, p in self.probes)
        self._note(ok)
        return ok

    def alive(self) -> None:
        """Data came in over the internet: it is up."""
        if not self.up:
            self._note(True)

    def _note(self, ok: bool) -> None:
        changed = None
        with self._lock:
            if ok:
                self._fails = 0
                if not self.up:
                    now = time.time()
                    self.back_after = now - (self.down_since or now)
                    self.back_at = now
                    self.up, self.down_since = True, None
                    changed = True
            else:
                self._fails += 1
                if self.up and self._fails >= self.fails_needed:
                    # it went down at the first failed round, not the second
                    self.down_since = time.time() - (self._fails - 1) * self.down_every_s
                    self.up = False
                    changed = False
        if changed is True:
            log.info("internet back after %s", minutes_text(self.back_after))
        elif changed is False:
            log.warning("internet down: no answer from %s",
                        ", ".join(h for h, _ in self.probes))
        if changed is not None and self.on_change is not None:
            try:
                self.on_change(changed)
            except Exception:  # noqa: BLE001
                log.exception("internet change callback failed")

    # ---- the thread -----------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="netwatch", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.probe_once()
            wait = self.every_s if (self.up and not self._fails) else self.down_every_s
            self._stop.wait(wait)


def minutes_text(seconds: float) -> str:
    """3 s / 4 min / 1 h 12 min."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"
