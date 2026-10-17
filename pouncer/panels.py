"""CQ zones and band opening timeline as windows of POUNCer (View menu).

The same pictures as the browser pages /zones and /timeline, drawn on a Tk
canvas from the same data (``app._zones(minutes)`` / ``app._timeline(hours)``),
so they can sit on the logging PC's screen next to N1MM. Each window is made
once, refreshes itself while it is open and remembers its choice (15 / 30 /
60 min; 6 / 12 / 24 / 48 h and 10 min / 1 h boxes) until POUNCer closes.
"""

from __future__ import annotations

import colorsys
import logging
import math
import time
import tkinter as tk
from tkinter import ttk

log = logging.getLogger(__name__)

BG = "#161c21"        # the pages' dark panel
EMPTY = "#1f272d"
FG = "#e6e6e6"
MUTED = "#8a949c"
MINE = "#ffeb3b"


def hsl(h: float, s: float, light: float) -> str:
    """CSS hsl(h, s%, l%) -> '#rrggbb' (h in degrees, s / l 0..100)."""
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, light / 100, s / 100)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def zone_color(kind: str, snr) -> str:
    """Like the zones page: blue = hears me, green = I hear; brighter = stronger."""
    v = max(0.0, min(1.0, ((8 if snr is None else snr) - 3) / 27))
    if kind == "hears_me":
        return hsl(210, 70 + 10 * v, 22 + 26 * v)
    return hsl(125, 55 + 10 * v, 20 + 22 * v)


def timeline_color(spots: int, stations: int, dx: int, top: int) -> str:
    """Like the timeline page: orange (own continent) -> green (DX) with the
    DX share; brighter = more spots (log scale against the busiest box)."""
    if not spots:
        return EMPTY
    share = dx / stations if stations else 0
    v = math.log(1 + spots) / math.log(1 + max(top, 2))
    return hsl(28 + (125 - 28) * share, 75, 16 + 34 * v)


def group_boxes(d: dict, group_min: int) -> list[dict]:
    """10-minute slots -> boxes of 10 min or 1 hour (a box starts on the full hour)."""
    out: list[dict] = []
    slots, slot_s = d.get("slots") or [], d.get("slot_s") or 600
    for i, t in enumerate(slots):
        if group_min == 60 and out and (t // 60) % 60 != 0:
            b = out[-1]
            for band in d["bands"]:
                c, s = d["cells"][band][i], b["cells"][band]
                s[0] += c[0]; s[1] += c[1]; s[2] += c[2]
            b["len"] += slot_s
            continue
        out.append({"t": t, "len": slot_s,
                    "cells": {band: list(d["cells"][band][i]) for band in d["bands"]}})
    return out


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.gmtime(t))


class _Panel:
    """A Toplevel made on first open, raised on the next, refreshed every
    ``every_ms`` while it exists."""

    title = ""
    every_ms = 15000
    size = "900x400"

    def __init__(self, root, provider, browser=None) -> None:
        self.root = root
        self.provider = provider          # callable -> dict (the page's /api data)
        self.browser = browser            # callable: open the browser page
        self.win: tk.Toplevel | None = None
        self._after = None
        self.data: dict | None = None
        self._hits: list[tuple[tuple, object]] = []   # (x0, y0, x1, y1), what

    def is_open(self) -> bool:
        try:
            return self.win is not None and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def open(self) -> None:
        if self.is_open():
            self.win.deiconify()
            self.win.lift()
            self.refresh()
            return
        win = tk.Toplevel(self.root)
        win.title(self.title)
        sw = max(600, win.winfo_screenwidth() - 40)
        w, h = (int(v) for v in self.size.split("x"))
        win.geometry(f"{min(w, sw)}x{h}")
        win.configure(bg=BG)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.win = win
        bar = tk.Frame(win, bg=BG, padx=8, pady=6)
        bar.pack(fill="x")
        self._build_bar(bar)
        if self.browser is not None:
            tk.Button(bar, text="Open in the browser", command=self.browser, relief="flat",
                      bg="#263238", fg="white", activebackground="#37474f",
                      activeforeground="white", padx=8).pack(side="right")
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=8)
        self.canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=xs.set)
        xs.pack(side="bottom", fill="x")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.info = tk.Label(win, text="Click a box.", anchor="w", justify="left", bg="#1c2329",
                             fg=FG, padx=10, pady=6, font=("Segoe UI", 10))
        self.info.pack(fill="x", padx=8, pady=(4, 8))
        self.info.bind("<Configure>", lambda e: self.info.configure(
            wraplength=max(200, e.width - 20)))
        self.refresh()

    def close(self) -> None:
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

    def refresh(self) -> None:
        if self._after is not None:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if not self.is_open():
            return
        try:
            self.data = self._fetch()
        except Exception as exc:  # noqa: BLE001 - keep the window, say why
            log.info("%s: %s", self.title, exc)
            self.data = {"error": str(exc)}
        self.draw()
        self._after = self.root.after(self.every_ms, self.refresh)

    def _choice_buttons(self, bar, label, options, current, pick):
        if label:
            tk.Label(bar, text=label, bg=BG, fg=MUTED).pack(side="left", padx=(0, 4))
        btns = {}
        for value, text in options:
            b = tk.Button(bar, text=text, relief="flat", padx=8, fg="white",
                          activeforeground="white", command=lambda v=value: pick(v))
            b.pack(side="left", padx=2)
            btns[value] = b
        self._mark(btns, current)
        return btns

    @staticmethod
    def _mark(btns, current) -> None:
        for v, b in btns.items():
            on = v == current
            b.configure(bg="#1565c0" if on else "#263238",
                        activebackground="#1976d2" if on else "#37474f")

    def _on_click(self, event) -> None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for (x0, y0, x1, y1), what in self._hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.info.configure(text=self.describe(what))
                return

    # subclasses
    def _fetch(self) -> dict: return self.provider()
    def _build_bar(self, bar) -> None: ...
    def draw(self) -> None: ...
    def describe(self, what) -> str: return ""


class ZonesWindow(_Panel):
    """Per band two rows over CQ zones 1-40: hears me (blue) / I hear (green)."""

    title = "POUNCer - CQ zones: who hears me, what I hear"
    every_ms = 15000
    size = "1100x520"
    CELL, GAP, LAB = 22, 2, 120

    def __init__(self, root, provider, browser=None) -> None:
        super().__init__(root, provider, browser)
        self.minutes = 15

    def _build_bar(self, bar) -> None:
        self._btns = self._choice_buttons(bar, "last", [(15, "15 min"), (30, "30 min"),
                                                        (60, "60 min")], self.minutes, self._pick)
        tk.Label(bar, text="  blue = skimmers there hear you  ·  green = your continent hears "
                           "them  ·  number = best SNR (dB)  ·  yellow = your zone",
                 bg=BG, fg=MUTED).pack(side="left", padx=8)

    def _pick(self, minutes: int) -> None:
        self.minutes = minutes
        self._mark(self._btns, minutes)
        self.refresh()

    def _fetch(self) -> dict:
        return self.provider(self.minutes)

    def draw(self) -> None:
        if not self.is_open():
            return
        c, d = self.canvas, self.data or {}
        c.delete("all")
        self._hits = []
        if d.get("error"):
            c.create_text(10, 20, text=d["error"], fill="#ef9a9a", anchor="w")
            return
        cell, gap, lab = self.CELL, self.GAP, self.LAB
        step = cell + gap
        mz = d.get("my_zone")
        font = ("Segoe UI", 8)
        y = 4
        for z in range(1, 41):
            x = lab + (z - 1) * step
            c.create_text(x + cell / 2, y + 8, text=str(z), font=font,
                          fill=MINE if z == mz else MUTED)
        c.create_text(lab + 40 * step + 6, y + 8, text="zones", font=font, fill=MUTED, anchor="w")
        y += 20
        for band in d.get("bands") or []:
            y += 6
            for key, label, col in (("hears_me", "hears me", "#64b5f6"),
                                    ("i_hear", "I hear", "#81c784")):
                row = (d.get(key) or {}).get(band) or {}
                if key == "hears_me":
                    c.create_text(4, y + cell / 2, text=band, anchor="w", fill=FG,
                                  font=("Segoe UI", 10, "bold"))
                c.create_text(46, y + cell / 2, text=label, anchor="w", fill=col,
                              font=("Segoe UI", 9))
                n = 0
                for z in range(1, 41):
                    x = lab + (z - 1) * step
                    v = row.get(z) or row.get(str(z))
                    outline = MINE if z == mz else ""
                    if not v:
                        c.create_rectangle(x, y, x + cell, y + cell, fill=EMPTY, outline=outline)
                        continue
                    n += 1
                    c.create_rectangle(x, y, x + cell, y + cell, fill=zone_color(key, v.get("snr")),
                                       outline=outline)
                    c.create_text(x + cell / 2, y + cell / 2, fill="white",
                                  font=("Segoe UI", 8, "bold"),
                                  text="•" if v.get("snr") is None else str(v["snr"]))
                    self._hits.append(((x, y, x + cell, y + cell), (band, key, z, v)))
                c.create_text(lab + 40 * step + 6, y + cell / 2, text=str(n), anchor="w",
                              fill=MUTED, font=font)
                y += step
        msgs = []
        if not d.get("own"):
            msgs.append("Your call isn't known yet (N1MM station call or [rbn] callsign) - "
                        "'hears me' stays empty.")
        if d.get("team") == "slave":
            msgs.append("This POUNCer is a team slave (no RBN feed of its own): look at the "
                        "master's zones.")
        for m in msgs:
            y += 8
            c.create_text(4, y + 8, text=m, anchor="w", fill="#ffab91", font=("Segoe UI", 9))
            y += 16
        c.configure(scrollregion=(0, 0, lab + 40 * step + 50, y + 4))

    def describe(self, what) -> str:
        band, key, z, v = what
        calls = v.get("calls") or 0
        head = (f"{band}, CQ zone {z}: "
                + (f"{calls} skimmer{'s' if calls != 1 else ''} heard you" if key == "hears_me"
                   else f"{calls} station{'s' if calls != 1 else ''} heard here"))
        if v.get("snr") is not None:
            head += f", best {v['snr']} dB"
        n = v.get("n") or 0
        head += f", {n} spot{'s' if n != 1 else ''}, last " + (
            f"{v['age']} min ago" if v.get("age") else "just now")
        who = v.get("who") or []
        return head + ("\n" + ", ".join(who) + (" ..." if calls > len(who) else "") if who else "")


class TimelineWindow(_Panel):
    """Bands top to bottom, time left to right (UTC), now at the right."""

    title = "POUNCer - band opening timeline"
    every_ms = 60000
    size = "1100x330"
    LAB, TOT, ROW = 52, 64, 26

    def __init__(self, root, provider, browser=None) -> None:
        super().__init__(root, provider, browser)
        self.hours, self.group = 12, 10

    def _build_bar(self, bar) -> None:
        self._hbtns = self._choice_buttons(bar, "", [(6, "6 h"), (12, "12 h"), (24, "24 h"),
                                                     (48, "48 h")], self.hours, self._pick_h)
        tk.Label(bar, text="", bg=BG, width=2).pack(side="left")
        self._gbtns = self._choice_buttons(bar, "boxes", [(10, "10 min"), (60, "1 hour")],
                                           self.group, self._pick_g)
        tk.Label(bar, text="  orange = your continent  ·  green = DX  ·  brighter = more "
                           "spots  ·  yellow = now",
                 bg=BG, fg=MUTED).pack(side="left", padx=8)

    def _pick_h(self, hours: int) -> None:
        self.hours = hours
        self._mark(self._hbtns, hours)
        self.refresh()

    def _pick_g(self, group: int) -> None:
        self.group = group
        self._mark(self._gbtns, group)
        self.draw()

    def _fetch(self) -> dict:
        return self.provider(self.hours)

    def draw(self) -> None:
        if not self.is_open():
            return
        c, d = self.canvas, self.data or {}
        c.delete("all")
        self._hits = []
        if d.get("error") or not d.get("slots"):
            c.create_text(10, 20, text=d.get("error") or "No data yet.", fill="#ef9a9a",
                          anchor="w")
            return
        bx = group_boxes(d, self.group)
        top = max([b["cells"][band][0] for b in bx for band in d["bands"]] or [0])
        width = max(300, c.winfo_width())
        w = max(14 if self.group == 60 else 4, (width - self.LAB - self.TOT) // len(bx) - 1)
        step = w + 1
        per_hour = step * (1 if self.group == 60 else 6)
        every = 1 if per_hour >= 34 else 2 if per_hour >= 17 else 3 if per_hour >= 11 else 6
        small = ("Segoe UI", 8)
        y = 14
        for i, b in enumerate(bx):
            g = time.gmtime(b["t"])
            if g.tm_min == 0 and g.tm_hour % every == 0:
                c.create_text(self.LAB + i * step, y, text=f"{g.tm_hour:02d}", anchor="w",
                              fill=MUTED, font=small)
        c.create_text(self.LAB + len(bx) * step + 8, y, text="spots", anchor="w", fill=MUTED,
                      font=small)
        y += 12
        for band in d["bands"]:
            c.create_text(4, y + self.ROW / 2, text=band, anchor="w", fill=FG,
                          font=("Segoe UI", 10, "bold"))
            tot = 0
            for i, b in enumerate(bx):
                spots, stations, dx = b["cells"][band]
                tot += spots
                x = self.LAB + i * step
                c.create_rectangle(x, y, x + w, y + self.ROW - 2, width=0,
                                   fill=timeline_color(spots, stations, dx, top))
                if i == len(bx) - 1:
                    c.create_rectangle(x, y, x + w, y + self.ROW - 2, outline=MINE)
                self._hits.append(((x, y, x + w, y + self.ROW - 2), (band, b)))
            c.create_text(self.LAB + len(bx) * step + 8, y + self.ROW / 2, text=str(tot),
                          anchor="w", fill=MUTED, font=small)
            y += self.ROW
        if d.get("team") == "slave":
            y += 6
            c.create_text(4, y + 8, anchor="w", fill="#ffab91", font=("Segoe UI", 9),
                          text="This POUNCer is a team slave (no RBN feed of its own): look at "
                               "the master's timeline.")
            y += 16
        c.configure(scrollregion=(0, 0, self.LAB + len(bx) * step + self.TOT, y + 4))

    def describe(self, what) -> str:
        band, b = what
        spots, stations, dx = b["cells"][band]
        span = f"{_hhmm(b['t'])}-{_hhmm(b['t'] + b['len'])} UTC"
        if not spots:
            return f"{band}, {span}: nothing heard"
        return (f"{band}, {span}: {spots} spot{'s' if spots != 1 else ''}, {stations} "
                f"station{'s' if stations != 1 else ''}, {dx} DX "
                f"({round(100 * dx / stations) if stations else 0} %)")
