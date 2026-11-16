"""N1MM Autostarter - POUNCer's autostart as a program of its own.

For a PC without POUNCer: starts the programs you chose (N1MM Logger+ and
its helpers) in order, each after its delay - the same rules as POUNCer's
*Settings > Startup & updates > Autostart* (``autostart.py`` does the work):

- Program 1-8 (path + arguments, "start after" seconds, "wait for N1MM"),
  then every program in the autostart folder.
- N1MM first: when Program 1 is N1MM Logger+ and it isn't running, it is
  started and the rest waits until N1MM is up.
- A program that already runs (an .exe) is not started a second time.

"N1MM is up" = N1MM's first RadioInfo / AppInfo broadcast on
``[autostarter] n1mm_port`` (only listened to while waiting; the port is
freed at once, and never taken from another program), or - when that port
is busy or N1MM's broadcasts are off - N1MM running for ``n1mm_settle``
seconds.

Settings in ``N1MMautostarter.ini`` next to the exe, written by the window
(Save). ``--setup`` opens the window without starting anything.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time

from . import autostart as am

log = logging.getLogger("autostarter")

VERSION = "1.1.0"
INI_NAME = "N1MMautostarter.ini"
LOG_NAME = "N1MMautostarter.log"
COUNTDOWN_S = 5          # at start: this long to press Stop
CLOSE_AFTER_S = 5        # "close when done": this long after the last one

DEFAULTS = {
    "folder": "", "folder_delay": 3, "wait_n1mm": True, "n1mm_timeout": 180,
    "n1mm_port": 12060, "n1mm_settle": 30, "close_when_done": False, "look": "dark",
}


# ---- settings -----------------------------------------------------------------

def base_dir() -> str:
    """Folder of the exe (or of the current directory when run from source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


def load(path: str) -> dict:
    """The settings: {"apps": [(cmd, delay, wait) x 8], folder, ...}."""
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8-sig")
    except (OSError, configparser.Error) as exc:
        log.warning("could not read %s: %s", path, exc)

    def get(section, key, fallback):
        try:
            raw = cp.get(section, key, raw=True, fallback=None)
        except configparser.Error:
            raw = None
        if raw is None or raw.strip() == "":
            return fallback
        raw = raw.strip()
        if isinstance(fallback, bool):
            return raw.lower() in ("1", "true", "yes", "on")
        if isinstance(fallback, int):
            try:
                return int(raw)
            except ValueError:
                return fallback
        return raw

    s = {"apps": []}
    for n in range(1, am.APPS + 1):
        s["apps"].append((get("autostart", f"app{n}", ""),
                          max(0, min(600, get("autostart", f"app{n}_delay", 5))),
                          get("autostart", f"app{n}_wait_n1mm", False)))
    s["folder"] = get("autostart", "folder", "")
    s["folder_delay"] = max(0, min(600, get("autostart", "folder_delay", 3)))
    s["wait_n1mm"] = get("autostart", "wait_n1mm", True)
    s["n1mm_timeout"] = max(10, min(900, get("autostart", "n1mm_timeout", 180)))
    s["n1mm_port"] = max(0, min(65535, get("autostarter", "n1mm_port", 12060)))
    s["n1mm_settle"] = max(5, min(300, get("autostarter", "n1mm_settle", 30)))
    s["close_when_done"] = get("autostarter", "close_when_done", False)
    look = str(get("autostarter", "look", "dark")).lower()
    s["look"] = look if look in ("dark", "light") else "dark"
    return s


def save(path: str, s: dict) -> None:
    """Write the whole file (it is ours alone), keeping a short header."""
    lines = ["; N1MM Autostarter - written by its window (Save). Same keys as",
             "; POUNCer's [autostart], so a list can be copied over either way.",
             "", "[autostart]"]
    for n, (cmd, delay, wait) in enumerate(s["apps"], 1):
        cmd = str(cmd).replace("\r", " ").replace("\n", " ").strip()
        lines += [f"app{n} = {cmd}", f"app{n}_delay = {int(delay)}",
                  f"app{n}_wait_n1mm = {'true' if wait else 'false'}"]
    folder = str(s.get("folder", "")).replace("\r", " ").replace("\n", " ").strip()
    lines += [f"folder = {folder}", f"folder_delay = {int(s['folder_delay'])}",
              f"wait_n1mm = {'true' if s['wait_n1mm'] else 'false'}",
              f"n1mm_timeout = {int(s['n1mm_timeout'])}", "", "[autostarter]",
              "; N1MM's broadcast port (Config > Broadcast Data, Radio / App Info),",
              "; listened to only while waiting for N1MM; 0 = don't listen.",
              f"n1mm_port = {int(s['n1mm_port'])}",
              "; Without a broadcast: N1MM counts as up after running this many seconds.",
              f"n1mm_settle = {int(s['n1mm_settle'])}",
              f"close_when_done = {'true' if s['close_when_done'] else 'false'}",
              "; the window: dark or light",
              f"look = {s.get('look', 'dark')}", ""]
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\r\n" if os.name == "nt" else "\n") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, path)


def make_plan(s: dict) -> list[dict]:
    folder = s.get("folder") or ""
    if folder and not os.path.isabs(folder):
        folder = os.path.join(base_dir(), folder)
    return am.plan(list(s["apps"]), folder, s["folder_delay"])


# ---- is N1MM up? ------------------------------------------------------------

def is_n1mm_packet(data: bytes) -> bool:
    head = data[:300].lower()
    return b"<radioinfo" in head or b"<appinfo" in head


class N1mmBeacon:
    """Listens for N1MM's broadcast only while someone waits for it.

    Never takes the port from another program (Windows: exclusive bind, no
    sharing) - if the port is busy, ``listening`` stays False and the
    "N1MM running for n1mm_settle s" rule decides alone."""

    def __init__(self, port: int, ip: str = "0.0.0.0") -> None:
        self.port, self.ip = port, ip
        self.heard = 0.0                      # time of N1MM's first broadcast
        self.listening = False
        self.error = ""
        self._stop = threading.Event()
        self._sock = None

    def start(self) -> None:
        if self.port <= 0:
            self.error = "off"
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):            # Windows
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((self.ip, self.port))
        except OSError as exc:
            sock.close()
            self.error = f"port {self.port} busy ({exc.__class__.__name__})"
            log.info("N1MM broadcast port %s not free (%s) - N1MM counts as up after "
                     "it runs a while", self.port, exc)
            return
        sock.settimeout(0.5)
        self._sock, self.listening = sock, True
        threading.Thread(target=self._run, name="n1mm-beacon", daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if is_n1mm_packet(data) and not self.heard:
                self.heard = time.time()
                log.info("N1MM broadcast heard on port %s", self.port)
                break                          # that's all we wanted: free the port
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.listening = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class N1mmUp:
    """N1MM up = its broadcast heard, or it has run for ``settle_s``."""

    def __init__(self, beacon: N1mmBeacon | None, settle_s: float,
                 running=None, clock=time.time) -> None:
        self.beacon = beacon
        self.settle_s = settle_s
        self._running = running or (lambda: None)
        self._clock = clock
        self.since = 0.0                       # first seen running

    def heard(self) -> float:
        return self.beacon.heard if self.beacon is not None else 0.0

    def __call__(self) -> bool:
        if self.heard():
            return True
        try:
            run = self._running()
        except Exception:  # noqa: BLE001
            run = None
        now = self._clock()
        if run:
            self.since = self.since or now
            return now - self.since >= self.settle_s
        self.since = 0.0
        return False


def n1mm_running() -> bool | None:
    try:
        from .win32 import is_n1mm_running
        return is_n1mm_running()
    except Exception:  # noqa: BLE001
        return None


def running_names():
    try:
        from .win32 import running_process_names
        return running_process_names()
    except Exception:  # noqa: BLE001
        return None


# ---- the run ----------------------------------------------------------------

class Runner:
    """N1MM first (wait until up), then the rest - in a thread; ``say(text)``
    gets every step (from that thread)."""

    def __init__(self, s: dict, say, launch=None, running=running_names,
                 n1mm_is_running=n1mm_running, sleep=None, clock=time.time) -> None:
        self.s = s
        self._say = say
        self._launch = launch or am._launch
        self._running = running
        self._n1mm_is_running = n1mm_is_running
        self._stop = threading.Event()
        self._sleep = sleep or (lambda t: self._stop.wait(t))
        self._clock = clock
        self.beacon = N1mmBeacon(s["n1mm_port"])
        self.up = N1mmUp(self.beacon, s["n1mm_settle"], n1mm_is_running, clock)
        self.done = threading.Event()
        self.results: list[tuple[str, str]] = []
        self.auto: am.Autostart | None = None      # the rest, while it runs
        self._base = 0
        self.last = ""                             # the last thing said

    def progress(self) -> list[tuple[str, str]]:
        """(path, result) of every program handled so far, in plan order."""
        a, r = self.auto, list(self.results)
        if a is None:
            return r
        return r[:self._base] + list(a.results)

    def say(self, text: str) -> None:
        self.last = text
        self._say(text)

    def stop(self) -> None:
        self._stop.set()
        self.beacon.stop()

    def start(self) -> None:
        threading.Thread(target=self.run, name="autostarter", daemon=True).start()

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            log.exception("autostart failed")
            self.say(f"failed: {exc}")
        finally:
            self.beacon.stop()
            self.done.set()

    def _run(self) -> None:
        items = make_plan(self.s)
        if not items:
            self.say("nothing to start - add programs and Save")
            return
        needs_n1mm = any(it.get("wait") for it in items) or (
            self.s["wait_n1mm"] and am.n1mm_first(items))
        if needs_n1mm:
            self.beacon.start()
        if (self.s["wait_n1mm"] and am.n1mm_first(items) and self._n1mm_is_running() is not True
                and os.path.exists(items[0]["path"])):
            first, items = items[0], items[1:]
            if first["delay"]:
                self.say(f"N1MM in {first['delay']} s")
                self._sleep(first["delay"])
            if self._stop.is_set():
                return
            try:
                self._launch(first["path"], first["args"])
                self.results.append((first["path"], "started"))
                self.say("started N1MM Logger+ - waiting until it is up")
            except Exception as exc:  # noqa: BLE001
                self.results.append((first["path"], f"failed: {exc}"))
                self.say(f"N1MM failed to start: {exc} - going on")
            else:
                w = am.N1mmWait(self._clock(), timeout_s=self.s["n1mm_timeout"],
                                no_process_s=am.N1MM_NO_PROCESS_S)
                last = ("", -99.0)
                while not self._stop.is_set():
                    up_t = self.up.heard() or (self._clock() if self.up() else 0.0)
                    done, text = w.check(self._clock(), up_t, 0.0, self._n1mm_is_running())
                    kind = text.split("(")[0]
                    if done or kind != last[0] or self._clock() - last[1] >= 10:
                        self.say(text)              # not every second: on change, or every 10 s
                        last = (kind, self._clock())
                    if done:
                        break
                    self._sleep(1)
        if self._stop.is_set():
            self.say("stopped")
            return
        if not items:
            self.say("all started")
            return
        a = am.Autostart(items, running=self._running, launch=self._launch, sleep=self._sleep,
                         on_status=self.say, n1mm_up=self.up, wait_s=self.s["n1mm_timeout"],
                         clock=self._clock, cancelled=self._stop.is_set)
        self._base = len(self.results)
        self.auto = a
        a.run()
        self.results += a.results
        self.auto = None
        if not self._stop.is_set():
            bad = [r for r in a.results if r[1] not in ("started", "running")]
            self.say("all started" if not bad else f"done - {len(bad)} not started (see the list)")


# ---- the window -------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="N1MMautostarter",
                                 description="Start N1MM Logger+ and your other programs.")
    ap.add_argument("--setup", action="store_true", help="open the window, start nothing")
    ap.add_argument("--ini", default="", help="settings file (default: next to the exe)")
    args = ap.parse_args(argv)
    ini = args.ini or os.path.join(base_dir(), INI_NAME)
    try:
        h = logging.handlers.RotatingFileHandler(os.path.join(os.path.dirname(ini) or ".", LOG_NAME),
                                                 maxBytes=200_000, backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(h)
    except OSError:
        pass
    logging.getLogger().setLevel(logging.INFO)
    log.info("N1MM Autostarter %s start, settings %s", VERSION, ini)
    App(ini, setup=args.setup or not os.path.exists(ini)).run()
    return 0


def display_name(path: str) -> str:
    """'C:\\HAM\\N1MM Logger+\\N1MMLogger.net.exe' -> 'N1MM Logger+',
    'C:\\HAM\\POUNCer\\POUNCer.exe' -> 'POUNCer'."""
    if am.is_n1mm(path):
        return "N1MM Logger+"
    name = os.path.basename(str(path or "").replace("\\", "/"))
    stem, ext = os.path.splitext(name)
    return stem if ext.lower() in am.FOLDER_TYPES else name


def row_states(items: list[dict], done: list[tuple[str, str]], running: bool,
               last: str = "") -> list[tuple[str, str]]:
    """(kind, text) per program for the list: kind = idle / next / ok / bad.

    ``done`` = Runner.progress() (in plan order); the first one not done yet
    is the one being started now."""
    out = []
    for n, it in enumerate(items):
        if n < len(done):
            res = done[n][1]
            if res == "started":
                out.append(("ok", "started"))
            elif res == "running":
                out.append(("ok", "was running"))
            elif res == "not found":
                out.append(("bad", "not found"))
            else:
                out.append(("bad", "failed"))
        elif running and n == len(done):
            low = last.lower()
            if "waiting" in low or "n1mm in" in low:
                out.append(("next", "waiting for N1MM" if "waiting" in low else "starting"))
            else:
                out.append(("next", f"in {it['delay']} s" if it["delay"] else "starting"))
        else:
            out.append(("idle", ""))
    return out


class App:
    """A small window: your programs as a list with a light each, Start /
    Stop - and a pencil to change the list (Edit)."""

    SHOWN = 4               # program rows in Edit before "+ Add program"
    DOT = {"idle": "#7a838c", "next": "#f9a825", "ok": "#43a047", "bad": "#e53935"}

    def __init__(self, ini: str, setup: bool = False) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        try:
            from . import theme
            theme.dpi_aware()
        except Exception:  # noqa: BLE001
            theme = None
        self.theme = theme
        self.ini = ini
        self.s = load(ini)
        self.runner: Runner | None = None
        self._countdown = 0
        self._after = None
        self._close_at = None
        self.editing = False
        self.more = False
        root = self.root = tk.Tk()
        root.title("N1MM Autostarter")
        if theme is not None:
            theme.set_icon(root)
        root.resizable(True, False)
        # the values (kept when the window is rebuilt for Edit / the look)
        self.v_app, self.v_delay, self.v_wait = [], [], []
        for _ in range(am.APPS):
            self.v_app.append(tk.StringVar()), self.v_delay.append(tk.StringVar())
            self.v_wait.append(tk.BooleanVar())
        self.v_folder, self.v_fdelay = tk.StringVar(), tk.StringVar()
        self.v_waitn1mm, self.v_close = tk.BooleanVar(), tk.BooleanVar()
        self.v_timeout, self.v_port, self.v_settle = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._fill()
        self.shown = max(self.SHOWN, self._last_used())
        self._status_text = ""
        self._apply_look()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if not make_plan(self.s):
            self._edit(True)
            self._status("Add your programs - N1MM Logger+ first - then Save.")
        elif not setup:
            self._countdown = COUNTDOWN_S
            self._tick_countdown()
        else:
            self._status("Ready - Start, or Edit to change the list.")

    # -- looks
    def _apply_look(self) -> None:
        t = self.theme
        if t is None:
            return
        try:
            t.apply(self.root, self.s.get("look", "dark"))
        except Exception:  # noqa: BLE001 - only looks
            pass

    def _c(self, name: str, fallback: str) -> str:
        return getattr(self.theme, name, "") or fallback if self.theme else fallback

    def _font(self, size: int = 10, weight: str = "normal"):
        return (self._c("FONT", "Segoe UI"), size, weight)

    def _px(self, n: float) -> int:
        return self.theme.px(n) if self.theme else int(n)

    # -- layout
    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        for w in self.root.winfo_children():
            w.destroy()
        bg, panel = self._c("BG", "#1c1c1c"), self._c("PANEL", "#202020")
        text, muted, line = self._c("TEXT", "#f2f2f2"), self._c("MUTED", "#a5adb5"), self._c("LINE", "#333333")
        P = self._px
        outer = ttk.Frame(self.root, padding=(P(14), P(12), P(14), P(12)))
        outer.pack(fill="both", expand=True)
        self.outer = outer

        # header: title + the look + Edit
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="N1MM Autostarter", font=self._font(13, "bold")).pack(side="left")
        ttk.Label(head, text=VERSION, style="Muted.TLabel", font=self._font(9)).pack(
            side="left", padx=(P(6), 0), pady=(P(4), 0))
        self.b_edit = ttk.Button(head, text="Done" if self.editing else "\u270e  Edit",
                                 command=lambda: self._edit(not self.editing))
        self.b_edit.pack(side="right")
        dark = self.s.get("look", "dark") == "dark"
        ttk.Button(head, text="Light" if dark else "Dark",
                   command=self._toggle_look).pack(side="right", padx=(0, P(6)))

        self.status = ttk.Label(outer, text=self._status_text, style="Muted.TLabel",
                                anchor="w", font=self._font(9))
        self.status.pack(fill="x", pady=(P(2), P(8)))

        if self.editing:
            self._build_edit(outer)
        else:
            self._build_list(outer, bg, panel, text, muted, line)

        # buttons
        btn = ttk.Frame(outer)
        btn.pack(fill="x", pady=(P(10), 0))
        if self.editing:
            ttk.Button(btn, text="Save", style="Accent.TButton", command=self._save_and_close_edit
                       ).pack(side="left", fill="x", expand=True)
            ttk.Button(btn, text="Cancel", command=self._cancel_edit).pack(
                side="left", padx=(P(8), 0))
            self.b_start = self.b_stop = None
        else:
            busy = self.runner is not None and not self.runner.done.is_set()
            self.b_start = ttk.Button(btn, text="Start", style="Accent.TButton",
                                      command=self._start,
                                      state="disabled" if busy else "normal")
            self.b_start.pack(side="left", fill="x", expand=True)
            self.b_stop = ttk.Button(btn, text="Stop", command=self._stop,
                                     state="normal" if busy or self._after else "disabled")
            self.b_stop.pack(side="left", padx=(P(8), 0))
        self.root.update_idletasks()
        self.root.geometry("")                     # fit the new content
        self.root.minsize(P(560) if self.editing else P(340), 1)

    def _build_list(self, parent, bg, panel, text, muted, line) -> None:
        tk = self.tk
        P = self._px
        card = tk.Frame(parent, bg=panel, highlightthickness=1, highlightbackground=line)
        card.pack(fill="x")
        self.rows = []
        items = make_plan(self.s)
        if not items:
            tk.Label(card, text="No programs yet - press Edit.", bg=panel, fg=muted,
                     font=self._font(10), pady=P(14)).pack()
        for n, it in enumerate(items):
            if n:
                tk.Frame(card, bg=line, height=1).pack(fill="x", padx=P(10))
            row = tk.Frame(card, bg=panel)
            row.pack(fill="x", padx=P(10), pady=P(7))
            dot = tk.Canvas(row, width=P(12), height=P(12), bg=panel, highlightthickness=0)
            dot.create_oval(P(1), P(1), P(11), P(11), fill=self.DOT["idle"], outline="", tags="d")
            dot.pack(side="left", padx=(0, P(10)))
            mid = tk.Frame(row, bg=panel)
            mid.pack(side="left", fill="x", expand=True)
            tk.Label(mid, text=display_name(it["path"]), bg=panel, fg=text, anchor="w",
                     font=self._font(10, "bold")).pack(fill="x")
            sub = []
            if n == 0 and am.is_n1mm(it["path"]) and self.s["wait_n1mm"]:
                sub.append("first - the rest waits until it is up")
            else:
                sub.append(f"after {it['delay']} s" if it["delay"] else "at once")
                if it.get("wait"):
                    sub.append("when N1MM is up")
            if it["from"] == "folder":
                sub.append("from the folder")
            tk.Label(mid, text=", ".join(sub), bg=panel, fg=muted, anchor="w",
                     font=self._font(8)).pack(fill="x")
            state = tk.Label(row, text="", bg=panel, fg=muted, font=self._font(9))
            state.pack(side="right")
            self.rows.append((dot, state))
        self.items = items
        self._show_states()

    def _build_edit(self, parent) -> None:
        tk, ttk = self.tk, self.ttk
        P = self._px
        grid = ttk.Frame(parent)
        grid.pack(fill="x")
        ttk.Label(grid, text="Program (path, then its arguments)", style="Muted.TLabel",
                  font=self._font(8)).grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="after s", style="Muted.TLabel", font=self._font(8)).grid(
            row=0, column=3, sticky="w", padx=(P(6), 0))
        ttk.Label(grid, text="N1MM up", style="Muted.TLabel", font=self._font(8)).grid(
            row=0, column=4, sticky="w", padx=(P(6), 0))
        for n in range(self.shown):
            r = n + 1
            ttk.Label(grid, text=str(n + 1), style="Muted.TLabel", font=self._font(9)).grid(
                row=r, column=0, sticky="e", padx=(0, P(6)))
            ttk.Entry(grid, textvariable=self.v_app[n]).grid(row=r, column=1, sticky="we", pady=P(2))
            ttk.Button(grid, text="\u2026", width=3,
                       command=lambda v=self.v_app[n]: self._browse_file(v)).grid(
                row=r, column=2, padx=(P(4), 0))
            ttk.Spinbox(grid, from_=0, to=600, textvariable=self.v_delay[n], width=4).grid(
                row=r, column=3, padx=(P(6), 0))
            cb = ttk.Checkbutton(grid, variable=self.v_wait[n])
            self._switch(cb)
            cb.grid(row=r, column=4, padx=(P(10), 0))
        grid.columnconfigure(1, weight=1)
        links = ttk.Frame(parent)
        links.pack(fill="x", pady=(P(6), 0))
        if self.shown < am.APPS:
            ttk.Button(links, text="+ Add program", command=self._add_row).pack(side="left")
        ttk.Button(links, text=("\u25be More options" if self.more else "\u25b8 More options"),
                   command=self._toggle_more).pack(side="right")
        if self.more:
            self._build_more(parent)

    def _build_more(self, parent) -> None:
        ttk = self.ttk
        P = self._px
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=(P(8), 0))
        f = ttk.Frame(box)
        f.pack(fill="x", pady=P(2))
        ttk.Label(f, text="Folder").pack(side="left")
        ttk.Entry(f, textvariable=self.v_folder).pack(side="left", fill="x", expand=True, padx=P(6))
        ttk.Button(f, text="\u2026", width=3, command=self._browse_folder).pack(side="left")
        ttk.Spinbox(f, from_=0, to=600, textvariable=self.v_fdelay, width=4).pack(
            side="left", padx=(P(6), P(2)))
        ttk.Label(f, text="s apart").pack(side="left")
        for text, var, extra in (
                ("N1MM first: start the rest when it is up, at most", self.v_waitn1mm,
                 (self.v_timeout, 10, 900, "s")),
                ("Close when everything is started", self.v_close, None)):
            r = ttk.Frame(box)
            r.pack(fill="x", pady=P(2))
            cb = ttk.Checkbutton(r, text=text, variable=var)
            self._switch(cb)
            cb.pack(side="left")
            if extra:
                v, lo, hi, unit = extra
                ttk.Spinbox(r, from_=lo, to=hi, textvariable=v, width=5).pack(
                    side="left", padx=(P(6), P(2)))
                ttk.Label(r, text=unit).pack(side="left")
        r = ttk.Frame(box)
        r.pack(fill="x", pady=P(2))
        ttk.Label(r, text="N1MM is up: its broadcast on UDP").pack(side="left")
        ttk.Spinbox(r, from_=0, to=65535, textvariable=self.v_port, width=7).pack(
            side="left", padx=P(6))
        ttk.Label(r, text="or after it runs").pack(side="left")
        ttk.Spinbox(r, from_=5, to=300, textvariable=self.v_settle, width=5).pack(
            side="left", padx=(P(6), P(2)))
        ttk.Label(r, text="s").pack(side="left")
        r = ttk.Frame(box)
        r.pack(fill="x", pady=(P(6), 0))
        ttk.Button(r, text="Start with Windows\u2026", command=self._open_startup).pack(side="left")

    def _switch(self, cb) -> None:
        if self.theme is not None and getattr(self.theme, "MODERN", False):
            try:
                cb.configure(style="Switch.TCheckbutton")
            except self.tk.TclError:
                pass

    def _last_used(self) -> int:
        used = [n + 1 for n in range(am.APPS) if self.v_app[n].get().strip()]
        return max(used) if used else 0

    # -- values
    def _fill(self) -> None:
        s = self.s
        for n, (cmd, delay, wait) in enumerate(s["apps"]):
            self.v_app[n].set(cmd), self.v_delay[n].set(str(delay)), self.v_wait[n].set(bool(wait))
        self.v_folder.set(s["folder"]), self.v_fdelay.set(str(s["folder_delay"]))
        self.v_waitn1mm.set(s["wait_n1mm"]), self.v_timeout.set(str(s["n1mm_timeout"]))
        self.v_port.set(str(s["n1mm_port"])), self.v_settle.set(str(s["n1mm_settle"]))
        self.v_close.set(s["close_when_done"])

    def _read(self) -> dict:
        def num(v, lo, hi, d):
            try:
                return max(lo, min(hi, int(str(v.get()).strip())))
            except ValueError:
                return d
        return {"apps": [(self.v_app[n].get().strip(), num(self.v_delay[n], 0, 600, 5),
                          bool(self.v_wait[n].get())) for n in range(am.APPS)],
                "folder": self.v_folder.get().strip(), "folder_delay": num(self.v_fdelay, 0, 600, 3),
                "wait_n1mm": bool(self.v_waitn1mm.get()),
                "n1mm_timeout": num(self.v_timeout, 10, 900, 180),
                "n1mm_port": num(self.v_port, 0, 65535, 12060),
                "n1mm_settle": num(self.v_settle, 5, 300, 30),
                "close_when_done": bool(self.v_close.get()),
                "look": self.s.get("look", "dark")}

    # -- actions
    def _edit(self, on: bool) -> None:
        if on:
            self._cancel_countdown("Not started - change the list, then Save.")
        self.editing = on
        self.shown = max(self.SHOWN, self._last_used())
        self._build()

    def _add_row(self) -> None:
        self.shown = min(am.APPS, self.shown + 1)
        self._build()

    def _toggle_more(self) -> None:
        self.more = not self.more
        self._build()

    def _toggle_look(self) -> None:
        self.s["look"] = "light" if self.s.get("look", "dark") == "dark" else "dark"
        try:
            save(self.ini, dict(load(self.ini), look=self.s["look"]) if os.path.exists(self.ini)
                 else self._read())
        except OSError:
            pass
        self._apply_look()
        self._build()

    def _save_and_close_edit(self) -> None:
        if self._save():
            self.editing = False
            self._build()

    def _cancel_edit(self) -> None:
        self._fill()
        self.editing = False
        self._status("Not changed.")
        self._build()

    def _browse_file(self, var) -> None:
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self.root, title="Program to start",
                                       filetypes=[("Programs", "*.exe *.bat *.cmd *.lnk *.vbs *.ps1"),
                                                  ("All files", "*.*")])
        if p:
            var.set(os.path.normpath(p))

    def _browse_folder(self) -> None:
        from tkinter import filedialog
        p = filedialog.askdirectory(parent=self.root, title="Folder with programs to start")
        if p:
            self.v_folder.set(os.path.normpath(p))

    def _save(self) -> bool:
        self.s = self._read()
        try:
            save(self.ini, self.s)
        except OSError as exc:
            self._status(f"Not saved: {exc}")
            return False
        self._status("Saved.")
        log.info("settings saved")
        return True

    def _open_startup(self) -> None:
        """Explorer at the Startup folder: put a shortcut to N1MMautostarter.exe
        there to have it start at Windows logon."""
        folder = os.path.join(os.environ.get("APPDATA", ""),
                              r"Microsoft\Windows\Start Menu\Programs\Startup")
        self._status("Drag a shortcut to N1MMautostarter.exe into this folder "
                     "to start it at Windows logon.")
        try:
            os.startfile(folder)                 # noqa: S606 - Explorer only
        except (AttributeError, OSError) as exc:
            self._status(f"Startup folder: {folder} ({exc})")

    def _tick_countdown(self) -> None:
        self._after = None
        if self._countdown <= 0:
            self._start()
            return
        self._status(f"Starting in {self._countdown} s - Stop to change the list first.")
        if self.b_start is not None:
            self.b_start.configure(text=f"Start now ({self._countdown})")
        if self.b_stop is not None:
            self.b_stop.configure(state="normal")
        self._countdown -= 1
        self._after = self.root.after(1000, self._tick_countdown)

    def _cancel_countdown(self, text: str) -> bool:
        if self._after is None:
            return False
        self.root.after_cancel(self._after)
        self._after = None
        if self.b_start is not None:
            self.b_start.configure(text="Start")
        self._status(text)
        return True

    def _start(self) -> None:
        if self.runner is not None and not self.runner.done.is_set():
            return
        self._cancel_countdown("")
        if self.editing:
            self.editing = False
            self._build()
        self.s = self._read()
        self.runner = Runner(self.s, say=lambda t: self.root.after(0, self._note, t))
        if self.b_start is not None:
            self.b_start.configure(state="disabled", text="Starting\u2026")
            self.b_stop.configure(state="normal")
        self.runner.start()
        self._watch()

    def _show_states(self) -> None:
        r = self.runner
        rows = getattr(self, "rows", [])
        if not rows:
            return
        running = r is not None and not r.done.is_set()
        states = row_states(self.items, r.progress() if r else [], running, r.last if r else "")
        muted = self._c("MUTED", "#a5adb5")
        colours = {"ok": self.DOT["ok"], "bad": self.DOT["bad"], "next": self.DOT["next"],
                   "idle": muted}
        for (dot, label), (kind, text) in zip(rows, states):
            try:
                dot.itemconfigure("d", fill=self.DOT[kind])
                label.configure(text=text, fg=colours[kind])
            except self.tk.TclError:
                pass

    def _watch(self) -> None:
        r = self.runner
        if r is None:
            return
        if not self.editing:
            self._show_states()
        if r.done.is_set():
            if self.b_start is not None:
                self.b_start.configure(state="normal", text="Start again")
                self.b_stop.configure(state="disabled")
            if self.s["close_when_done"] and not r._stop.is_set():
                self._status(f"Done - closing in {CLOSE_AFTER_S} s")
                self._close_at = self.root.after(CLOSE_AFTER_S * 1000, self._close)
            return
        self.root.after(300, self._watch)

    def _stop(self) -> None:
        self._cancel_countdown("Not started - Edit to change the list, or Start.")
        if self.runner is not None:
            self.runner.stop()
        if self.b_stop is not None:
            self.b_stop.configure(state="disabled")
            self.b_start.configure(state="normal", text="Start")

    def _note(self, text: str) -> None:
        self._status(text[:1].upper() + text[1:] if text else text)
        log.info("%s", text)

    def _status(self, text: str) -> None:
        self._status_text = text
        try:
            self.status.configure(text=text)
        except (AttributeError, self.tk.TclError):
            pass

    def _close(self) -> None:
        if self.runner is not None:
            self.runner.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
