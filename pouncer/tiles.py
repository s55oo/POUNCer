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
from . import zo

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
        self._sb = sb
        self.c.configure(yscrollcommand=self._scrolled)
        sb.pack(side="right", fill="y")
        self.c.pack(side="left", fill="both", expand=True)
        self.zo_mode = False
        self._zo_headings = []
        self._zo_ch = theme.px(CARD_H)
        self._visible_items = ()
        self._tip = None
        self._tip_at = None
        self.c.bind("<Motion>", self._hover)
        self.c.bind("<Leave>", lambda e: self._hide_tip())
        self.items: list[dict] = []
        self.selected: int | None = None
        self._boxes: list[tuple] = []
        self._sig = None
        # reused canvas items, only the tiles on screen (v0.7.182) - Tk never
        # frees a deleted canvas item; see rowlist.RowList
        self._pool: list[dict] = []
        self._drawn = (0, 0)
        self._height = 0
        self.c.bind("<Configure>", lambda e: self.draw(force=True))
        self.c.bind("<Button-1>", self._click)
        self.c.bind("<MouseWheel>", lambda e: self.c.yview_scroll(int(-e.delta / 120), "units"))
        self.c.bind("<Button-4>", lambda e: self.c.yview_scroll(-1, "units"))
        self.c.bind("<Button-5>", lambda e: self.c.yview_scroll(1, "units"))
        for key, dx, dy in (("<Up>", 0, -1), ("<Down>", 0, 1),
                            ("<Left>", -1, 0), ("<Right>", 1, 0)):
            self.c.bind(key, lambda e, x=dx, y=dy: self._arrow(x, y))
        self.c.bind("<space>", lambda e: self._tap_selected())

    def palette(self) -> dict:
        return COLORS["dark" if theme.LOOK == "dark" else "light"]

    def _cols(self) -> int:
        if self.zo_mode:
            return 3
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

    def _arrow(self, dx, dy):
        if not self.zo_mode:
            return self.move(dx + dy * self._cols())
        index = zo.neighbor(self._boxes, self.selected, dx, dy)
        if index is not None:
            self.selected = index
            self.draw(force=True)
            self._see(index)
            if self.on_select:
                self.on_select(index)
        return "break"

    def _hide_tip(self):
        if self._tip is not None:
            self._tip.destroy()
        self._tip, self._tip_at = None, None

    def _hover(self, event):
        x, y = self.c.canvasx(event.x), self.c.canvasy(event.y)
        index = next((i for (x0, y0, x1, y1), i in self._boxes
                      if x0 <= x <= x1 and y0 <= y <= y1), None)
        text = self.items[index].get("run_tip", "") if index is not None else ""
        if not text:
            self._hide_tip()
        elif self._tip_at != (index, text):
            self._hide_tip()
            tip = tk.Toplevel(self.c)
            tip.overrideredirect(True)
            tk.Label(tip, text=text, justify="left", wraplength=theme.px(380),
                     bg="#263238", fg="#eceff1", padx=8, pady=6).pack()
            tip.geometry(f"+{event.x_root + 16}+{event.y_root + 14}")
            self._tip, self._tip_at = tip, (index, text)

    def _zo_visible(self, ch):
        top = self.c.canvasy(0) - self.MARGIN_ROWS * (ch + GAP)
        bottom = self.c.canvasy(self.c.winfo_height()) + self.MARGIN_ROWS * (ch + GAP)
        return tuple(i for (x0, y0, x1, y1), i in self._boxes if y1 >= top and y0 <= bottom)

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
        self._hide_tip()
        self.c.focus_set()
        x, y = self.c.canvasx(e.x), self.c.canvasy(e.y)
        for (x0, y0, x1, y1), j in self._boxes:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.selected = j
                self.draw(force=True)
                self.on_tap(j)
                return

    MARGIN_ROWS = 3                     # tile rows drawn above / below the window

    def _visible(self, cols: int, ch: float) -> tuple[int, int]:
        """Item indexes to draw: the tile rows on screen, plus a margin."""
        step = ch + GAP
        try:
            top = int(max(0, self.c.canvasy(0) - GAP) // step)
            n = int(max(self.c.winfo_height(), step * 4) // step) + 1
        except tk.TclError:
            top, n = 0, 8
        lo = max(0, top - self.MARGIN_ROWS) * cols
        return lo, min(len(self.items), (top + n + self.MARGIN_ROWS) * cols)

    def _scrolled(self, first, last) -> None:
        self._sb.set(first, last)
        if self.zo_mode:
            if self._zo_visible(self._zo_ch) != self._visible_items:
                self.draw(force=True)
            return
        cols = self._cols()
        lo, hi = self._visible(cols, theme.px(CARD_H))
        if lo < self._drawn[0] or hi > self._drawn[1]:
            self.draw(force=True)

    def _slot(self) -> dict:
        c = self.c
        hid = {"state": "hidden"}
        flat = [0] * 24
        return {"card": c.create_polygon(flat, smooth=True, **hid),
                "bar": c.create_polygon(flat, smooth=True, outline="", **hid),
                "cover": c.create_rectangle(0, 0, 0, 0, outline="", **hid),
                "call": c.create_text(0, 0, anchor="w", text="", **hid),
                "marks": [c.create_text(0, 0, anchor="w", text="", **hid) for _ in range(4)],
                "l1": c.create_text(0, 0, anchor="e", text="", **hid),
                "l2": c.create_text(0, 0, anchor="e", text="", **hid)}

    def _hide(self, slot: dict) -> None:
        c = self.c
        for k in ("card", "bar", "cover"):
            c.itemconfigure(slot[k], state="hidden")
        for t in [slot["call"], slot["l1"], slot["l2"]] + slot["marks"]:
            c.itemconfigure(t, state="hidden", text="")

    def draw(self, force: bool = False) -> None:
        sig = (tuple(tuple(sorted(it.items())) for it in self.items), self.selected,
               self.c.winfo_width(), theme.LOOK, self.zo_mode)
        if sig == self._sig and not force:
            return
        self._sig = sig
        p = self.palette()
        c = self.c
        c.configure(background=p["bg"])
        self._boxes = []
        cols = self._cols()
        w = max(1, c.winfo_width())
        cw = (w - GAP * (cols + 1)) / cols
        ch = theme.px(CARD_H)
        if self.zo_mode:
            # Compact cards reserve room for wrapped marks as well as details.
            ch = theme.px(176 if cw < theme.px(200) else 104)
            self._zo_ch = ch
            self._boxes, headings, self._height = zo.layout(self.items, w, ch, GAP, theme.px(28))
        else:
            headings = []
            for i in range(len(self.items)):
                r, k = divmod(i, cols)
                x0, y0 = GAP + k * (cw + GAP), GAP + r * (ch + GAP)
                self._boxes.append(((x0, y0, x0 + cw, y0 + ch), i))
            rows = (len(self.items) + cols - 1) // cols
            self._height = GAP + rows * (ch + GAP)
        while len(self._zo_headings) < len(headings):
            self._zo_headings.append(c.create_text(0, 0, anchor="nw", state="hidden"))
        for n, text_id in enumerate(self._zo_headings):
            if n < len(headings):
                x, y, text = headings[n]
                c.coords(text_id, x, y)
                c.itemconfigure(text_id, text=text, width=max(1, cw),
                                fill=("#ffb74d" if n == 0 else "#ffd54f" if n == 1 else p["detail"]),
                                font=(theme.FONT, 10, "bold"), state="normal")
            else:
                c.itemconfigure(text_id, state="hidden")
        if self.zo_mode:
            visible = self._zo_visible(ch)
        else:
            lo, hi = self._visible(cols, ch)
            self._drawn = (lo, hi)
            visible = tuple(range(lo, hi))
        self._visible_items = visible
        c.configure(scrollregion=(0, 0, w, max(self._height, c.winfo_height())))
        while len(self._pool) < len(visible):
            self._pool.append(self._slot())
        for n, slot in enumerate(self._pool):
            if n < len(visible):
                i = visible[n]
                self._fill(slot, i, self.items[i], self._boxes[i][0], p, ch)
            else:
                self._hide(slot)

    def _fill(self, slot: dict, i: int, it: dict, box: tuple, p: dict, ch: float) -> None:
        c = self.c
        x0, y0, x1, y1 = box
        size = max(10, min(17, int((x1 - x0) / 12))) if self.zo_mode else 17
        big = (theme.FONT, size, "bold")
        small = (theme.FONT, 8 if self.zo_mode else 9)
        small_b = (theme.FONT, 8 if self.zo_mode else 9, "bold")
        fill = (p["stale_strong"] if it.get("strong") else p["stale"]) if it.get("stale") else \
            (p["strong"] if it.get("strong") else p["card"])
        outline, width = "", 0
        if i == self.selected:
            outline, width = SELECTED, 3
        elif it.get("onfreq"):
            outline, width = ONFREQ, 3
        c.coords(slot["card"], *_round_pts(x0, y0, x1, y1, theme.px(10)))
        c.itemconfigure(slot["card"], fill=fill, outline=outline, width=width, state="normal")
        if it.get("mult"):
            bar = theme.px(5 if it["mult"] == 1 else 10)
            c.coords(slot["bar"], *_round_pts(x0, y0, x0 + bar + theme.px(6), y1, theme.px(10)))
            c.itemconfigure(slot["bar"], fill=MULT, state="normal")
            c.coords(slot["cover"], x0 + bar, y0 + 1, x0 + bar + theme.px(8), y1 - 1)
            c.itemconfigure(slot["cover"], fill=fill, state="normal")
        else:
            c.itemconfigure(slot["bar"], state="hidden")
            c.itemconfigure(slot["cover"], state="hidden")
        color = p["worked"] if it.get("worked") else (p["dx"] if it.get("dx") else p["call"])
        tx = x0 + theme.px(16)
        t = slot["call"]
        c.coords(t, tx, y0 + ch * 0.15 if self.zo_mode else (y0 + y1) / 2)
        c.itemconfigure(t, text=it["call"], fill=color, font=big, state="normal",
                        width=max(1, x1 - tx - 8) if self.zo_mode else 0)
        bb = c.bbox(t)
        bx = tx if self.zo_mode else (bb[2] + theme.px(4) if bb else tx)
        marks = [(m, col) for m, col in ((it.get("busted") and "?", p["busted"]),
                                         (it.get("own") and "\u2605", p["tried"]),
                                         (it.get("rare") and "\u26a1", p["rare"]),
                                         (it.get("tried"), p["tried"])) if m]
        mark_y = y0 + ch * 0.35
        row_bottom = mark_y
        for j, m_id in enumerate(slot["marks"]):
            if j < len(marks):
                mark, col = marks[j]
                c.coords(m_id, bx, mark_y if self.zo_mode else (y0 + y1) / 2)
                c.itemconfigure(m_id, text=mark, fill=col, state="normal",
                                anchor="nw" if self.zo_mode else "w", width=0,
                                font=(theme.FONT, 7, "bold") if self.zo_mode else
                                     (small_b if mark == it.get("tried") else (theme.FONT, 14, "bold")))
                bb = c.bbox(m_id)
                if self.zo_mode:
                    right = x1 - theme.px(8)
                    if bb and bb[2] > right and bx > tx:
                        bx, mark_y = tx, row_bottom + theme.px(3)
                    c.coords(m_id, bx, mark_y)
                    c.itemconfigure(m_id, width=max(1, right - bx - 2))
                    bb = c.bbox(m_id)
                    if bb:
                        row_bottom = max(row_bottom, bb[3])
                bx = (bb[2] if bb else bx) + theme.px(4)
            else:
                c.itemconfigure(m_id, state="hidden", text="")
        rx = x1 - theme.px(10)
        if self.zo_mode:
            rx = x0 + theme.px(10)
        for key in ("l1", "l2"):
            c.itemconfigure(slot[key], anchor="w" if self.zo_mode else "e",
                            justify="left" if self.zo_mode else "right",
                            width=max(1, x1 - rx - 8) if self.zo_mode else 0)
        c.coords(slot["l1"], rx, y0 + ch * (0.70 if self.zo_mode else 0.32))
        c.itemconfigure(slot["l1"], text=it.get("line1", ""), fill=p["detail"], font=small_b,
                        state="normal")
        c.coords(slot["l2"], rx, y0 + ch * (0.88 if self.zo_mode else 0.70))
        c.itemconfigure(slot["l2"], text=it.get("line2", ""), fill=p["detail"], font=small,
                        state="normal")


def _round_pts(x0, y0, x1, y1, r):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


def _round_rect(c, x0, y0, x1, y1, r, **kw):
    return c.create_polygon(_round_pts(x0, y0, x1, y1, r), smooth=True, **kw)
