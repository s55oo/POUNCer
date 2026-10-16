"""Start your other programs together with POUNCer.

Two ways, both in order, each after its own delay (seconds after the one
before; the first after POUNCer's start):

1. ``[autostart] app1`` ... ``app8`` in config.ini / Settings > Autostart:
   the full path of an .exe, .bat, .cmd or shortcut, with ``app1_delay`` ...
   ``app8_delay``, and ``appN_wait_n1mm = true`` = start that one only once
   N1MM is up (it broadcasts; at most ``n1mm_timeout`` s). Arguments: put the path in quotes and the arguments after
   it - ``"C:\\My Apps\\tool.exe" --port 9000``.
2. The ``autostart`` folder next to config.ini: every .exe / .bat / .cmd /
   .lnk / .vbs / .ps1 in it, in name order (``1 rotor.lnk``, ``2 cw.bat``),
   ``folder_delay`` seconds apart.

An .exe that was already running before POUNCer started its list is not
started again (listing the same program 4 times starts 4 copies), and when POUNCer
restarts itself (update, File > Restart) nothing is started - it all runs
already. Every program starts in its own folder, like a double-click.

N1MM first: when the first program is N1MM Logger+ (``N1MMLogger.net.exe``,
wherever it is installed) and it isn't running yet, POUNCer starts it and
WAITS until N1MM is up - its first RadioInfo or AppInfo broadcast - and
only then starts the rest of itself (RBN, log, team, phone page, ...) and
the other programs (``[autostart] wait_n1mm``; ``n1mm_timeout`` seconds at
most). ``N1mmWait`` is that decision, without tkinter.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)

APPS = 8
FOLDER_TYPES = (".exe", ".bat", ".cmd", ".lnk", ".vbs", ".ps1", ".url")
RESTART_ENV = "POUNCER_RESTARTED"     # set by POUNCer when it restarts itself
N1MM_EXE_PREFIX = "n1mmlogger"        # N1MMLogger.net.exe (any install folder)
N1MM_SETTLE_S = 3        # after N1MM's first broadcast: let it finish opening
N1MM_NO_PROCESS_S = 45   # N1MM still not running after this long: it didn't start


def split_command(text: str) -> tuple[str, str]:
    """'"C:\\A B\\x.exe" --a 1' -> ('C:\\A B\\x.exe', '--a 1'); without
    quotes the whole text is the path (spaces allowed)."""
    text = (text or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end].strip(), text[end + 1:].strip()
        return text.strip('"').strip(), ""
    return text, ""


def folder_entries(folder: str) -> list[str]:
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if os.path.splitext(n)[1].lower() in FOLDER_TYPES
            and os.path.isfile(os.path.join(folder, n))]


def plan(apps: list[tuple[str, int]], folder: str = "", folder_delay: int = 3) -> list[dict]:
    """What to start, in order: [{path, args, delay, from}]."""
    out = []
    for n, app in enumerate(apps, 1):
        text, delay = app[0], app[1]
        wait = bool(app[2]) if len(app) > 2 else False
        path, args = split_command(text)
        if path:
            out.append({"path": path, "args": args, "delay": max(0, int(delay)),
                        "from": f"app{n}", "wait": wait})
    for path in folder_entries(folder) if folder else ():
        out.append({"path": path, "args": "", "delay": max(0, int(folder_delay)),
                    "from": "folder", "wait": False})
    return out


def is_n1mm(path: str) -> bool:
    """'C:\\HAM\\N1MM Logger+\\N1MMLogger.net.exe' (any folder) -> True."""
    name = os.path.basename(str(path or "").replace("\\", "/")).lower().replace(" ", "")
    return name.startswith(N1MM_EXE_PREFIX) and name.endswith(".exe")


def n1mm_first(items: list[dict]) -> bool:
    """Is the first program of the plan N1MM Logger+?"""
    return bool(items) and is_n1mm(items[0]["path"])


class N1mmWait:
    """Has N1MM started properly? ``check`` -> (done, text).

    Ready = a RadioInfo or AppInfo broadcast from N1MM after we started it
    (its Entry Window / log is open), then ``settle_s`` more. Gives up -
    and POUNCer goes on anyway - after ``timeout_s`` (N1MM's broadcasts not
    switched on?), or when N1MM still isn't running after ``no_process_s``
    (it didn't start), or when you click "don't wait"."""

    def __init__(self, started: float, timeout_s: float = 180, settle_s: float = N1MM_SETTLE_S,
                 no_process_s: float = N1MM_NO_PROCESS_S) -> None:
        self.started = started
        self.timeout_s = timeout_s
        self.settle_s = settle_s
        self.no_process_s = no_process_s
        self.heard: float | None = None
        self.result = ""

    def check(self, now: float, radio_t: float = 0.0, app_t: float = 0.0,
              running: bool | None = None) -> tuple[bool, str]:
        waited = now - self.started
        last = max(radio_t or 0, app_t or 0)
        if self.heard is None and last > 0 and last >= self.started:
            self.heard = now
        if self.heard is not None:
            if now - self.heard >= self.settle_s:
                self.result = f"N1MM is up after {waited:.0f} s"
                return True, self.result
            return False, "N1MM is up - starting the rest ..."
        if running is False and waited >= self.no_process_s:
            self.result = f"N1MM didn't start in {waited:.0f} s - going on without it"
            return True, self.result
        if waited >= self.timeout_s:
            self.result = (f"no word from N1MM in {waited:.0f} s (Config > Broadcast Data: "
                           "Radio / App Info?) - going on")
            return True, self.result
        return False, f"waiting for N1MM to start ({waited:.0f} s)"


def _launch(path: str, args: str) -> None:
    cwd = os.path.dirname(path) or None
    if sys.platform == "win32":
        os.startfile(path, arguments=args, cwd=cwd)   # like a double-click
    else:                                              # (tests / macOS)
        subprocess.Popen([path, *args.split()] if args else [path], cwd=cwd)


class Autostart:
    """Runs the plan in a background thread; ``status`` = what happened."""

    def __init__(self, items: list[dict], running=None, launch=None, sleep=time.sleep,
                 on_status=None, n1mm_up=None, wait_s: float = 180, clock=time.time) -> None:
        self.items = items
        self._running = running            # -> set of lower-case exe names, or None
        self._launch = launch or _launch
        self._sleep = sleep
        self.on_status = on_status
        self._n1mm_up = n1mm_up            # -> True once N1MM broadcasts (per-item wait)
        self.wait_s = wait_s
        self._clock = clock
        self.results: list[tuple[str, str]] = []   # (path, "started" / "running" / error)
        self.status = ""

    def start(self) -> None:
        if self.items:
            threading.Thread(target=self.run, name="autostart", daemon=True).start()

    def _say(self, text: str) -> None:
        self.status = text
        log.info("autostart: %s", text)
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception:  # noqa: BLE001
                pass

    def _up(self) -> bool:
        try:
            return bool(self._n1mm_up())
        except Exception:  # noqa: BLE001
            return False

    def run(self) -> None:
        total = len(self.items)
        # what ran BEFORE this list started: the same program listed twice
        # (e.g. 4 x the same router.exe) starts 4 times, but a second POUNCer start
        # doesn't add 4 more
        before: set[str] = set()
        if self._running is not None:
            try:
                before = set(self._running() or ())
            except Exception:  # noqa: BLE001
                before = set()
        for n, it in enumerate(self.items, 1):
            path, name = it["path"], os.path.basename(it["path"])
            if it.get("wait") and self._n1mm_up is not None and not self._up():
                self._say(f"{name}: waiting for N1MM ({n}/{total})")
                end = self._clock() + self.wait_s
                while not self._up() and self._clock() < end:
                    self._sleep(1)
                if not self._up():
                    self._say(f"{name}: N1MM not up after {self.wait_s:.0f} s - starting anyway")
            if it["delay"]:
                self._sleep(it["delay"])
            if not os.path.exists(path):
                self.results.append((path, "not found"))
                self._say(f"{name} not found ({n}/{total})")
                continue
            if name.lower().endswith(".exe") and name.lower() in before:
                self.results.append((path, "running"))
                self._say(f"{name} already running ({n}/{total})")
                continue
            try:
                self._launch(path, it["args"])
            except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the rest
                self.results.append((path, f"failed: {exc}"))
                self._say(f"{name} failed to start: {exc} ({n}/{total})")
                continue
            self.results.append((path, "started"))
            self._say(f"started {name} ({n}/{total})")
