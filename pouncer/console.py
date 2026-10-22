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

import logging
import time
import tkinter as tk
from tkinter import ttk

from . import theme, traffic

log = logging.getLogger(__name__)

KEEP = 3000
EVERY_MS = 250
QUIET_S = 5          # no line this long = "quiet" (amber dot)
DEAD_S = 30          # this long = red dot (unless the app says why: grey)
DOT = {"live": "#43a047", "quiet": "#f9a825", "dead": "#e53935", "why": "#78909c"}
GROUPS = traffic.GROUPS
group_of = traffic.group_of
COLORS = {"RBN": "#81c784", "SSB": "#ffb74d", "HTTP": "#4fc3f7", "N1MM": "#ce93d8",
          "TEAM": "#90caf9", "WEB": "#b0bec5", "PSKR": "#f48fb1", "OUT": "#ffeb3b"}


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
        ttk.Button(bar, text="Reset counters", command=self.reset_counts).pack(side="left", padx=2)
        # one chip per source: coloured dot (green = lines now, amber = quiet,
        # red = quiet long, grey = quiet for a known reason) + count / why
        self.info = ttk.Frame(win, padding=(8, 0, 8, 4))
        self.info.pack(fill="x")
        self.chips = {}
        d = theme.px(10)
        for n, g in enumerate(GROUPS):
            box = ttk.Frame(self.info)
            box.grid(row=n // 3, column=n % 3, sticky="w", padx=(0, 18))
            dot = tk.Canvas(box, width=d + 2, height=d + 2, highlightthickness=0, bd=0,
                            bg=theme.BG or "#1c1c1c")
            dot.create_oval(1, 1, d, d, fill=DOT["why"], outline="", tags="dot")
            dot.pack(side="left", padx=(0, 4))
            dot.state = "why"
            lbl = ttk.Label(box, text=g, font=(theme.FONT, 9))
            lbl.pack(side="left")
            self.chips[g] = (dot, lbl)
        for col in range(3):
            self.info.columnconfigure(col, weight=1)
        self.paused_lbl = ttk.Label(self.info, text="", font=(theme.FONT, 9, "bold"))
        self.paused_lbl.grid(row=0, column=3, sticky="e")
        # shown when the view is scrolled up and new lines came in below it
        self.below = tk.Label(win, text="", bg="#1565c0", fg="#ffffff", cursor="hand2",
                              font=(theme.FONT, 9, "bold"), pady=2)
        self.below.bind("<Button-1>", lambda e: self._follow())
        self._below_n = 0
        frame = tk.Frame(win, bg="#101418")
        self._frame = frame
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
        if self._at_end():
            self.text.see("end")
        self._draw_chips()

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
        self._follow()

    def reset_counts(self) -> None:
        """Counters back to 0 (the view stays); "quiet" counts from now."""
        self.counts = {g: 0 for g in GROUPS}
        self.last = {}
        self.opened = time.time()
        self.text.insert("end", time.strftime("%H:%M:%S ", time.gmtime())
                         + "--- counters reset ---\n", "mark")
        if self._at_end():
            self.text.see("end")
        self._draw_chips()

    def _at_end(self) -> bool:
        if not self.text.winfo_ismapped():     # not drawn yet: nothing is "below"
            return True
        return self.text.yview()[1] >= 0.999

    def _follow(self) -> None:
        """Back to the newest lines (the blue bar, or Clear)."""
        self.text.see("end")
        self._below_n = 0
        if self.below.winfo_manager():
            self.below.pack_forget()

    def _show_below(self, n: int) -> None:
        self._below_n += n
        self.below.configure(text=f"{self._below_n} new line"
                             f"{'' if self._below_n == 1 else 's'} below - click to follow")
        if not self.below.winfo_manager():
            self.below.pack(fill="x", side="bottom", before=self._frame)

    def _tick(self) -> None:
        self._after = None
        if not self.is_open():
            return
        if not traffic.active():           # someone stopped the tap: start it again
            traffic.start()
        try:
            self.add(traffic.drain())
        except Exception as exc:  # noqa: BLE001 - one bad line must not stop the console
            log.exception("traffic console")
            try:
                self.text.insert("end", f"--- console error: {exc} (it goes on) ---\n", "mark")
            except tk.TclError:
                pass
        finally:
            if self.is_open():
                self._after = self.root.after(EVERY_MS, self._tick)

    def add(self, lines: list) -> None:
        """Show drained (time, source, text) lines - filtered, at the end."""
        want = self.filter.get().strip().upper()
        at_end = self._at_end()
        if at_end and self._below_n:          # scrolled down by hand: following again
            self._follow()
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
            else:
                self._show_below(shown)
        self._draw_chips()

    def states(self, now: float | None = None) -> dict:
        """{group: (dot state, text)}: live / quiet / dead / why."""
        now = time.time() if now is None else now
        notes = {}
        if self.notes is not None:
            try:
                notes = self.notes() or {}
            except Exception:  # noqa: BLE001
                notes = {}
        out = {}
        for g in GROUPS:
            extra = []
            var = getattr(self, "show", {}).get(g)
            if var is not None and not var.get():
                extra.append("hidden")
            quiet = now - self.last.get(g, self.opened)
            if quiet >= QUIET_S:
                extra.append(f"quiet {int(quiet // 60)}m" if quiet >= 120 else f"quiet {int(quiet)}s")
            if notes.get(g):
                extra.append(notes[g])
            if quiet < QUIET_S:
                st = "live"
            elif notes.get(g):
                st = "why"
            else:
                st = "quiet" if quiet < DEAD_S else "dead"
            text = f"{g} {self.counts.get(g, 0)}" + (f" ({', '.join(extra)})" if extra else "")
            out[g] = (st, text)
        return out

    def _draw_chips(self) -> None:
        for g, (st, text) in self.states().items():
            dot, lbl = self.chips[g]
            if dot.state != st:
                dot.state = st
                dot.itemconfigure("dot", fill=DOT[st])
            if lbl.cget("text") != text:
                lbl.configure(text=text)
        self.paused_lbl.configure(text="PAUSED" if self.paused else "")

    def summary(self, now: float | None = None) -> str:
        """'RBN 120   SSB 0 (quiet 40s, spots come from N1MM) ...' (as the chips)."""
        return "   ".join(t for _, t in self.states(now).values())
