"""Start your other programs together with POUNCer.

Two ways, both in order, each after its own delay (seconds after the one
before; the first after POUNCer's start):

1. ``[autostart] app1`` ... ``app4`` in config.ini / Settings > Autostart:
   the full path of an .exe, .bat, .cmd or shortcut, with ``app1_delay`` ...
   ``app4_delay``. Arguments: put the path in quotes and the arguments after
   it - ``"C:\\My Apps\\tool.exe" --port 9000``.
2. The ``autostart`` folder next to config.ini: every .exe / .bat / .cmd /
   .lnk / .vbs / .ps1 in it, in name order (``1 rotor.lnk``, ``2 cw.bat``),
   ``folder_delay`` seconds apart.

An .exe that was already running before POUNCer started its list is not
started again (listing the same program 4 times starts 4 copies), and when POUNCer
restarts itself (update, File > Restart) nothing is started - it all runs
already. Every program starts in its own folder, like a double-click.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)

APPS = 4
FOLDER_TYPES = (".exe", ".bat", ".cmd", ".lnk", ".vbs", ".ps1", ".url")
RESTART_ENV = "POUNCER_RESTARTED"     # set by POUNCer when it restarts itself


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
    for n, (text, delay) in enumerate(apps, 1):
        path, args = split_command(text)
        if path:
            out.append({"path": path, "args": args, "delay": max(0, int(delay)),
                        "from": f"app{n}"})
    for path in folder_entries(folder) if folder else ():
        out.append({"path": path, "args": "", "delay": max(0, int(folder_delay)),
                    "from": "folder"})
    return out


def _launch(path: str, args: str) -> None:
    cwd = os.path.dirname(path) or None
    if sys.platform == "win32":
        os.startfile(path, arguments=args, cwd=cwd)   # like a double-click
    else:                                              # (tests / macOS)
        subprocess.Popen([path, *args.split()] if args else [path], cwd=cwd)


class Autostart:
    """Runs the plan in a background thread; ``status`` = what happened."""

    def __init__(self, items: list[dict], running=None, launch=None, sleep=time.sleep,
                 on_status=None) -> None:
        self.items = items
        self._running = running            # -> set of lower-case exe names, or None
        self._launch = launch or _launch
        self._sleep = sleep
        self.on_status = on_status
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

    def run(self) -> None:
        total = len(self.items)
        # what ran BEFORE this list started: the same program listed twice
        # (e.g. 4 x QSOrouter.exe) starts 4 times, but a second POUNCer start
        # doesn't add 4 more
        before: set[str] = set()
        if self._running is not None:
            try:
                before = set(self._running() or ())
            except Exception:  # noqa: BLE001
                before = set()
        for n, it in enumerate(self.items, 1):
            if it["delay"]:
                self._sleep(it["delay"])
            path, name = it["path"], os.path.basename(it["path"])
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
