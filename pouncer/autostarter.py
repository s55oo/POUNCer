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

VERSION = "1.0.0"
INI_NAME = "N1MMautostarter.ini"
LOG_NAME = "N1MMautostarter.log"
COUNTDOWN_S = 5          # at start: this long to press Stop
CLOSE_AFTER_S = 5        # "close when done": this long after the last one

DEFAULTS = {
    "folder": "", "folder_delay": 3, "wait_n1mm": True, "n1mm_timeout": 180,
    "n1mm_port": 12060, "n1mm_settle": 30, "close_when_done": False,
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
              f"close_when_done = {'true' if s['close_when_done'] else 'false'}", ""]
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
        self.say = say
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
        a.run()
        self.results += a.results
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


class App:
    def __init__(self, ini: str, setup: bool = False) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        try:
            from . import theme
            theme.dpi_aware()
        except Exception:  # noqa: BLE001
            pass
        self.ini = ini
        self.s = load(ini)
        self.runner: Runner | None = None
        self._countdown = 0
        self._after = None
        root = self.root = tk.Tk()
        root.title(f"N1MM Autostarter {VERSION}")
        root.minsize(720, 300)
        self._build()
        self._fill()
        if not setup and make_plan(self.s):
            self._countdown = COUNTDOWN_S
            self._tick_countdown()
        elif not make_plan(self.s):
            self._status("Add your programs (N1MM Logger+ as Program 1), then Save.")

    # -- layout
    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        root = self.root
        pad = {"padx": 4, "pady": 2}
        top = ttk.Frame(root, padding=8)
        top.pack(fill="both", expand=True)
        ttk.Label(top, text="Programs, started in this order (path, then arguments after "
                  "it; a path with spaces may be in quotes)").grid(row=0, column=0, columnspan=5,
                                                                    sticky="w", **pad)
        ttk.Label(top, text="start after (s)").grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(top, text="wait for N1MM").grid(row=1, column=4, sticky="w", **pad)
        self.v_app, self.v_delay, self.v_wait = [], [], []
        for n in range(am.APPS):
            r = n + 2
            ttk.Label(top, text=f"Program {n + 1}").grid(row=r, column=0, sticky="w", **pad)
            va, vd, vw = tk.StringVar(), tk.StringVar(), tk.BooleanVar()
            ttk.Entry(top, textvariable=va, width=60).grid(row=r, column=1, sticky="we", **pad)
            ttk.Button(top, text="Browse...",
                       command=lambda v=va: self._browse_file(v)).grid(row=r, column=2, **pad)
            ttk.Spinbox(top, from_=0, to=600, textvariable=vd, width=6).grid(row=r, column=3,
                                                                           sticky="w", **pad)
            ttk.Checkbutton(top, variable=vw).grid(row=r, column=4, **pad)
            self.v_app.append(va), self.v_delay.append(vd), self.v_wait.append(vw)
        r = am.APPS + 2
        ttk.Label(top, text="Folder").grid(row=r, column=0, sticky="w", **pad)
        self.v_folder, self.v_fdelay = tk.StringVar(), tk.StringVar()
        ttk.Entry(top, textvariable=self.v_folder, width=60).grid(row=r, column=1, sticky="we", **pad)
        ttk.Button(top, text="Browse...", command=self._browse_folder).grid(row=r, column=2, **pad)
        ttk.Spinbox(top, from_=0, to=600, textvariable=self.v_fdelay, width=6).grid(
            row=r, column=3, sticky="w", **pad)
        ttk.Label(top, text="(every program in it, by name, this many s apart)").grid(
            row=r, column=4, sticky="w", **pad)
        top.columnconfigure(1, weight=1)

        opt = ttk.Frame(top)
        opt.grid(row=r + 1, column=0, columnspan=5, sticky="we", pady=(6, 2))
        self.v_waitn1mm, self.v_close = tk.BooleanVar(), tk.BooleanVar()
        self.v_timeout, self.v_port, self.v_settle = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Checkbutton(opt, text="N1MM as Program 1: start the rest when N1MM is up, at most",
                        variable=self.v_waitn1mm).pack(side="left")
        ttk.Spinbox(opt, from_=10, to=900, textvariable=self.v_timeout, width=5).pack(side="left")
        ttk.Label(opt, text="s").pack(side="left", padx=(2, 12))
        ttk.Checkbutton(opt, text="Close when everything is started",
                        variable=self.v_close).pack(side="left")
        opt2 = ttk.Frame(top)
        opt2.grid(row=r + 2, column=0, columnspan=5, sticky="we", pady=2)
        ttk.Label(opt2, text="N1MM is up: its broadcast on UDP port").pack(side="left")
        ttk.Spinbox(opt2, from_=0, to=65535, textvariable=self.v_port, width=7).pack(side="left")
        ttk.Label(opt2, text="(0 = off), or after it runs").pack(side="left", padx=4)
        ttk.Spinbox(opt2, from_=5, to=300, textvariable=self.v_settle, width=5).pack(side="left")
        ttk.Label(opt2, text="s").pack(side="left", padx=2)

        btn = ttk.Frame(top)
        btn.grid(row=r + 3, column=0, columnspan=5, sticky="we", pady=(8, 4))
        ttk.Button(btn, text="Save", command=self._save).pack(side="left")
        self.b_start = ttk.Button(btn, text="Start now", command=self._start)
        self.b_start.pack(side="left", padx=6)
        self.b_stop = ttk.Button(btn, text="Stop", command=self._stop, state="disabled")
        self.b_stop.pack(side="left")
        ttk.Button(btn, text="Windows Startup folder",
                   command=self._open_startup).pack(side="right")
        self.status = ttk.Label(top, text="", anchor="w")
        self.status.grid(row=r + 4, column=0, columnspan=5, sticky="we", **pad)
        self.logbox = tk.Text(top, height=7, wrap="word", state="disabled", relief="flat",
                              background="#f3f6f8")
        self.logbox.grid(row=r + 5, column=0, columnspan=5, sticky="nsew", **pad)
        top.rowconfigure(r + 5, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

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
                "close_when_done": bool(self.v_close.get())}

    # -- actions
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
            self._status(f"not saved: {exc}")
            return False
        self._status(f"Saved: {self.ini}")
        log.info("settings saved")
        return True

    def _open_startup(self) -> None:
        """Explorer at the Startup folder: put a shortcut to N1MMautostarter.exe
        there to have it start at Windows logon."""
        folder = os.path.join(os.environ.get("APPDATA", ""),
                              r"Microsoft\Windows\Start Menu\Programs\Startup")
        self._status("Startup folder: drag a shortcut to N1MMautostarter.exe in here "
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
        self._status(f"Starting your programs in {self._countdown} s - press Stop to change "
                     "the list first")
        self.b_stop.configure(state="normal")
        self._countdown -= 1
        self._after = self.root.after(1000, self._tick_countdown)

    def _start(self) -> None:
        if self.runner is not None and not self.runner.done.is_set():
            return
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        self.s = self._read()
        self.b_start.configure(state="disabled")
        self.b_stop.configure(state="normal")
        self.runner = Runner(self.s, say=lambda t: self.root.after(0, self._note, t))
        self.runner.start()
        self._watch()

    def _watch(self) -> None:
        r = self.runner
        if r is None:
            return
        if r.done.is_set():
            self.b_start.configure(state="normal")
            self.b_stop.configure(state="disabled")
            if self.s["close_when_done"] and not r._stop.is_set():
                self._status(f"Done - closing in {CLOSE_AFTER_S} s")
                self.root.after(CLOSE_AFTER_S * 1000, self._close)
            return
        self.root.after(500, self._watch)

    def _stop(self) -> None:
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
            self._status("Not started - change the list, Save, or Start now")
        if self.runner is not None:
            self.runner.stop()
        self.b_stop.configure(state="disabled")
        self.b_start.configure(state="normal")

    def _note(self, text: str) -> None:
        self._status(text)
        self.logbox.configure(state="normal")
        self.logbox.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        log.info("%s", text)

    def _status(self, text: str) -> None:
        self.status.configure(text=text)

    def _close(self) -> None:
        if self.runner is not None:
            self.runner.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
