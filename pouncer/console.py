"""Traffic console (View > Traffic console): what comes in and goes out.

Raw lines of every source as they arrive - RBN and the SSB cluster (telnet
lines, and the commands sent), N1MM (UDP packets in, QSY / call commands
out), the HTTP backup feed, the team, the phone page's requests. The taps
(``traffic.emit``) only work while this window is open; closed = no work.

Sources can be ticked off (they keep being counted; a line in the view marks
the switch), a filter word (a call, "DX de", "RadioInfo" ...) shows matching
lines only, Pause freezes the view (lines keep counting), Clear empties it.
The top bar shows per source: lines so far, "hidden", how long it has been
quiet, and why (``notes()`` from the app: RBN paused on SSB, SSB via N1MM,
via the team master ...). At most ``KEEP`` lines are kept in the window.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from . import theme, traffic

KEEP = 3000
EVERY_MS = 250
GROUPS = traffic.GROUPS
group_of = traffic.group_of
COLORS = {"RBN": "#81c784", "SSB": "#ffb74d", "HTTP": "#4fc3f7", "N1MM": "#ce93d8",
          "TEAM": "#90caf9", "WEB": "#b0bec5", "OUT": "#ffeb3b"}


class TrafficConsole:
    def __init__(self, root, notes=None) -> None:
        self.root = root
        self.notes = notes            # () -> {group: "paused - radio on SSB", ...}
        self.win: tk.Toplevel | None = None
        self._after = None
        self.counts: dict[str, int] = {g: 0 for g in GROUPS}
        self.last: dict[str, float] = {}
        self.opened = time.time()
        self.paused = False

    def is_open(self) -> bool:
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def open(self) -> None:
        if self.is_open():
            self.win.deiconify()
            self.win.lift()
            return
        traffic.start()
        self.opened = time.time()
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("POUNCer - traffic console")
        win.geometry(f"{theme.px(900)}x{theme.px(420)}")
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.after(10, lambda w=win: theme.title_bar(w, True))
        bar = ttk.Frame(win, padding=(6, 4))
        bar.pack(fill="x")
        self.show = {g: tk.BooleanVar(value=g != "WEB") for g in GROUPS}
        for g in GROUPS:
            ttk.Checkbutton(bar, text=g, variable=self.show[g],
                            command=lambda g=g: self._switched(g)).pack(side="left", padx=(0, 6))
        ttk.Label(bar, text="  filter").pack(side="left")
        self.filter = tk.StringVar()
        ttk.Entry(bar, textvariable=self.filter, width=14).pack(side="left", padx=(4, 8))
        self.pause_btn = ttk.Button(bar, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=2)
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="left", padx=2)
        self.info = ttk.Label(win, text="", padding=(8, 0, 8, 4), font=(theme.FONT, 9))
        self.info.pack(fill="x")
        frame = tk.Frame(win, bg="#101418")
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, bg="#101418", fg="#cfd8dc", insertbackground="#cfd8dc",
                            font=("Consolas", 9), wrap="none", bd=0, padx=6, pady=4)
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        for g, col in COLORS.items():
            self.text.tag_configure(g, foreground=col)
        self.text.tag_configure("time", foreground="#607d8b")
        self.text.tag_configure("mark", foreground="#607d8b", font=("Consolas", 9, "italic"))
        self._tick()

    def _switched(self, g: str) -> None:
        """A source ticked on / off: say so in the view (else it looks stuck)."""
        on = self.show[g].get()
        note = (self.notes() if self.notes else {}).get(g, "") if on else ""
        self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime())
                         + (f"--- {g} shown again" + (f" ({note})" if note else "") if on
                            else f"--- {g} hidden (still counted above)") + " ---\n", "mark")
        if self.text.yview()[1] >= 0.98:
            self.text.see("end")

    def close(self) -> None:
        traffic.stop()                     # the taps go quiet again
        if self._after is not None:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_btn.configure(text="Resume" if self.paused else "Pause")

    def _clear(self) -> None:
        self.text.delete("1.0", "end")

    def _tick(self) -> None:
        self._after = None
        if not self.is_open():
            return
        lines = traffic.drain()
        self.add(lines)
        self._after = self.root.after(EVERY_MS, self._tick)

    def add(self, lines: list) -> None:
        """Show drained (time, source, text) lines - filtered, at the end."""
        want = self.filter.get().strip().upper()
        at_end = self.text.yview()[1] >= 0.999
        shown = 0
        for t, src, txt in lines:
            g = group_of(src)
            self.counts[g] = self.counts.get(g, 0) + 1
            self.last[g] = t
            var = self.show.get(g)
            if self.paused or (var is not None and not var.get()):
                continue
            if want and want not in txt.upper() and want not in src.upper():
                continue
            out = src.endswith(">")
            self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime(t)), "time")
            self.text.insert("end", f"{src:<16} ", "OUT" if out else g)
            self.text.insert("end", txt + "\n")
            shown += 1
        if shown:
            n = int(self.text.index("end-1c").split(".")[0])
            if n > KEEP:
                self.text.delete("1.0", f"{n - KEEP}.0")
            if at_end:
                self.text.see("end")
        self.info.configure(text=self.summary() + ("   PAUSED" if self.paused else ""))

    def summary(self, now: float | None = None) -> str:
        """'RBN 120   SSB 0 (via N1MM)   N1MM 45 ...': lines per source, hidden,
        quiet for how long, and the app's note on why."""
        now = time.time() if now is None else now
        notes = {}
        if self.notes is not None:
            try:
                notes = self.notes() or {}
            except Exception:  # noqa: BLE001
                notes = {}
        parts = []
        for g in GROUPS:
            s = f"{g} {self.counts.get(g, 0)}"
            extra = []
            var = getattr(self, "show", {}).get(g)
            if var is not None and not var.get():
                extra.append("hidden")
            quiet = now - self.last.get(g, self.opened)
            if quiet >= 30:
                extra.append(f"quiet {int(quiet // 60)}m" if quiet >= 60 else f"quiet {int(quiet)}s")
            if notes.get(g):
                extra.append(notes[g])
            parts.append(s + (f" ({', '.join(extra)})" if extra else ""))
        return "   ".join(parts)
