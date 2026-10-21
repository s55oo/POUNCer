"""The spot list as tiles, like the phone page (View > Spot list as tiles).

One card per spot on a scrolling canvas, as many columns as fit: the call
big on the left (yellow = DX, grey = worked, ⚡ rare, ? not in SCP, ↻ tapped),
frequency / run history over first heard / SNR / calling time on the right.
Green card = strong (20 dB+), darker = not re-spotted lately, pink bar = new
multiplier (thick = double), blue frame = the radio is on it, yellow frame =
selected. Click = tap (QSY + call); arrow keys move, Space taps.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

CARD_W = 330         # wanted card width at 100 % (columns = what fits)
CARD_H = 58
GAP = 6

COLORS = {
    "dark": {"bg": "#101418", "card": "#263238", "strong": "#1b5e20", "stale": "#192125",
             "stale_strong": "#183a1c", "call": "#ffffff", "dx": "#ffeb3b", "worked": "#78848c",
             "detail": "#cfd8dc", "rare": "#ffd54f", "busted": "#ffb74d", "tried": "#90caf9"},
    "light": {"bg": "#eef1f5", "card": "#ffffff", "strong": "#d8f0da", "stale": "#e4e7eb",
              "stale_strong": "#cfe3d1", "call": "#1f2933", "dx": "#b26a00", "worked": "#9aa5b1",
              "detail": "#46525e", "rare": "#e69500", "busted": "#e65100", "tried": "#1565c0"},
}
MULT = "#ec407a"
ONFREQ = "#4fc3f7"
SELECTED = "#ffeb3b"


class TileList(ttk.Frame):
    def __init__(self, master, on_tap, on_select=None) -> None:
        super().__init__(master)
        self.on_tap = on_tap               # (index) -> None
        self.on_select = on_select
        self.c = tk.Canvas(self, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.c.yview)
        self.c.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.c.pack(side="left", fill="both", expand=True)
        self.items: list[dict] = []
        self.selected: int | None = None
        self._boxes: list[tuple] = []
        self._sig = None
        self.c.bind("<Configure>", lambda e: self.draw(force=True))
        self.c.bind("<Button-1>", self._click)
        self.c.bind("<MouseWheel>", lambda e: self.c.yview_scroll(int(-e.delta / 120), "units"))
        self.c.bind("<Button-4>", lambda e: self.c.yview_scroll(-1, "units"))
        self.c.bind("<Button-5>", lambda e: self.c.yview_scroll(1, "units"))
        for key, step in (("<Up>", -1), ("<Down>", 1)):
            self.c.bind(key, lambda e, s=step: self.move(s * self._cols()))
        self.c.bind("<Left>", lambda e: self.move(-1))
        self.c.bind("<Right>", lambda e: self.move(1))
        self.c.bind("<space>", lambda e: self._tap_selected())

    def palette(self) -> dict:
        return COLORS["dark" if theme.LOOK == "dark" else "light"]

    def _cols(self) -> int:
        w = max(1, self.c.winfo_width())
        return max(1, (w - GAP) // (theme.px(CARD_W) + GAP))

    def set_items(self, items: list[dict], selected: int | None = None) -> None:
        self.items = items
        if selected is not None:
            self.selected = selected
        elif self.selected is not None and self.selected >= len(items):
            self.selected = None
        self.draw()

    def move(self, step: int) -> str:
        if not self.items:
            return "break"
        i = 0 if self.selected is None else max(0, min(len(self.items) - 1, self.selected + step))
        self.selected = i
        self.draw(force=True)
        self._see(i)
        if self.on_select:
            self.on_select(i)
        return "break"

    def _tap_selected(self) -> str:
        if self.selected is not None and self.selected < len(self.items):
            self.on_tap(self.selected)
        return "break"

    def _see(self, i: int) -> None:
        for (x0, y0, x1, y1), j in self._boxes:
            if j == i:
                h = max(1, self._height)
                top, bottom = self.c.canvasy(0), self.c.canvasy(self.c.winfo_height())
                if y0 < top:
                    self.c.yview_moveto(y0 / h)
                elif y1 > bottom:
                    self.c.yview_moveto(max(0, (y1 - self.c.winfo_height()) / h))
                return

    def _click(self, e) -> None:
        self.c.focus_set()
        x, y = self.c.canvasx(e.x), self.c.canvasy(e.y)
        for (x0, y0, x1, y1), j in self._boxes:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.selected = j
                self.draw(force=True)
                self.on_tap(j)
                return

    def draw(self, force: bool = False) -> None:
        sig = (tuple(tuple(sorted(it.items())) for it in self.items), self.selected,
               self.c.winfo_width(), theme.LOOK)
        if sig == self._sig and not force:
            return
        self._sig = sig
        p = self.palette()
        c = self.c
        c.delete("all")
        c.configure(background=p["bg"])
        self._boxes = []
        cols = self._cols()
        w = max(1, c.winfo_width())
        cw = (w - GAP * (cols + 1)) / cols
        ch = theme.px(CARD_H)
        big = (theme.FONT, 17, "bold")
        small = (theme.FONT, 9)
        small_b = (theme.FONT, 9, "bold")
        for i, it in enumerate(self.items):
            r, k = divmod(i, cols)
            x0 = GAP + k * (cw + GAP)
            y0 = GAP + r * (ch + GAP)
            x1, y1 = x0 + cw, y0 + ch
            fill = (p["stale_strong"] if it.get("strong") else p["stale"]) if it.get("stale") else \
                (p["strong"] if it.get("strong") else p["card"])
            outline, width = "", 0
            if i == self.selected:
                outline, width = SELECTED, 3
            elif it.get("onfreq"):
                outline, width = ONFREQ, 3
            _round_rect(c, x0, y0, x1, y1, theme.px(10), fill=fill, outline=outline, width=width)
            if it.get("mult"):
                bar = theme.px(5 if it["mult"] == 1 else 10)
                _round_rect(c, x0, y0, x0 + bar + theme.px(6), y1, theme.px(10), fill=MULT,
                            outline="")
                c.create_rectangle(x0 + bar, y0 + 1, x0 + bar + theme.px(8), y1 - 1, fill=fill,
                                   outline="")
            color = p["worked"] if it.get("worked") else (p["dx"] if it.get("dx") else p["call"])
            tx = x0 + theme.px(16)
            t = c.create_text(tx, (y0 + y1) / 2, text=it["call"], anchor="w", fill=color, font=big)
            bx = c.bbox(t)[2] + theme.px(4) if c.bbox(t) else tx
            for mark, col in ((it.get("busted") and "?", p["busted"]),
                              (it.get("own") and "\u2605", p["tried"]),
                              (it.get("rare") and "⚡", p["rare"]),
                              (it.get("tried"), p["tried"])):
                if mark:
                    m = c.create_text(bx, (y0 + y1) / 2, text=mark, anchor="w", fill=col,
                                      font=small_b if mark == it.get("tried") else (theme.FONT, 14, "bold"))
                    bx = (c.bbox(m)[2] if c.bbox(m) else bx) + theme.px(4)
            rx = x1 - theme.px(10)
            c.create_text(rx, y0 + ch * 0.32, text=it.get("line1", ""), anchor="e",
                          fill=p["detail"], font=small_b)
            c.create_text(rx, y0 + ch * 0.70, text=it.get("line2", ""), anchor="e",
                          fill=p["detail"], font=small)
            self._boxes.append(((x0, y0, x1, y1), i))
        rows = (len(self.items) + cols - 1) // cols
        self._height = GAP + rows * (ch + GAP)
        c.configure(scrollregion=(0, 0, w, max(self._height, c.winfo_height())))


def _round_rect(c, x0, y0, x1, y1, r, **kw):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)
