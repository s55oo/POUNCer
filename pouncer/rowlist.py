"""The spot list as readable rows (the clean layout's list).

A table drawn on a canvas, because Tk's Treeview can only style whole rows:
here the **callsign is big and bold** and the other cells small and quiet,
and colour is used only where it means something:

- new multiplier: the call red, an **M** / **MM** tag;
- worked: the whole row grey; not re-spotted lately: dimmer;
- DX (another continent): a warm row tint;
- the station the radio is on: a blue frame; the selected row: a yellow frame;
- run history: R84% (⚡ yellow = a historical opportunity hint).

Columns: kHz, Call, SNR, WPM, Mult, Runs, Age, Skimmer - plus Mode and Type
only when the list holds more than one mode / a spot that isn't a CQ.
Click a row = tap (QSY + call), a heading = sort; arrows move, Space taps,
wheel scrolls. Same interface as ``tiles.TileList``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

ROW_H = 30
HEAD_H = 26

# (key, heading, width at 100 %, anchor, sort column of the app or None)
COLUMNS = (
    ("freq", "kHz", 82, "e", "freq"),
    ("call", "Call", 150, "w", "call"),
    ("snr", "SNR", 52, "e", "snr"),
    ("wpm", "WPM", 52, "e", None),          # shown, not sortable
    ("mult", "Mult", 54, "w", "mult"),
    ("run", "Runs", 104, "w", None),
    ("mode", "Mode", 58, "w", "mode"),
    ("type", "Type", 58, "w", "type"),
    ("age", "Age", 64, "e", "time"),
    ("spotter", "Skimmer", 96, "w", "spotter"),
)
OPTIONAL = ("mode", "type")     # shown only when they say something

COLORS = {
    "dark": {"bg": "#1b2126", "line": "#262e34", "dx": "#2a2418", "call": "#ffffff",
             "text": "#cfd8dc", "muted": "#8a9aa4", "worked": "#5f6b73", "head": "#78909c",
             "head_line": "#37474f", "mult1": "#ff8a80", "mult2": "#ff5252",
             "tag1": "#c62828", "tag2": "#b71c1c", "tag_fg": "#ffffff", "rare": "#ffd54f",
             "busted": "#ffb74d", "tried": "#90caf9", "onfreq": "#64b5f6", "sel": "#ffeb3b",
             "stale": "#a3b0b8"},
    "light": {"bg": "#ffffff", "line": "#e8ecef", "dx": "#fff4e0", "call": "#1f2933",
              "text": "#37474f", "muted": "#78909c", "worked": "#b0b8bf", "head": "#607d8b",
              "head_line": "#cfd8dc", "mult1": "#d32f2f", "mult2": "#b71c1c",
              "tag1": "#d32f2f", "tag2": "#b71c1c", "tag_fg": "#ffffff", "rare": "#e69500",
              "busted": "#e65100", "tried": "#1565c0", "onfreq": "#1e88e5", "sel": "#f9a825",
              "stale": "#62727b"},
}


def visible_columns(items: list[dict]) -> list[tuple]:
    """The columns to show: Mode / Type only when they differ from 'CW' / 'CQ'
    (several modes in the list, or a spot that isn't a CQ)."""
    modes = {str(it.get("mode") or "").upper() for it in items}
    show_mode = len(modes) > 1 or bool(modes - {"CW", ""})
    show_type = any(str(it.get("type") or "").upper() not in ("", "CQ") for it in items)
    out = []
    for col in COLUMNS:
        if col[0] == "mode" and not show_mode:
            continue
        if col[0] == "type" and not show_type:
            continue
        out.append(col)
    return out


def layout(cols: list[tuple], width: int, scale=lambda n: n) -> list[tuple[str, int, int]]:
    """(key, x0, x1) per column: the fixed widths, then what is left over
    spread over all columns (Call gets twice its share) - the table fills the
    width, but Call never takes half of it."""
    base = [scale(c[2]) for c in cols]
    extra = max(0, width - sum(base))
    shares = [2 if c[0] == "call" else 1 for c in cols]
    unit = extra / max(1, sum(shares))
    out, x = [], 0.0
    for c, w, s in zip(cols, base, shares):
        x1 = x + w + unit * s
        out.append((c[0], int(x), int(x1)))
        x = x1
    return out


class RowList(ttk.Frame):
    def __init__(self, master, on_tap, on_sort=None, on_select=None) -> None:
        super().__init__(master)
        self.on_tap = on_tap               # (index) -> None
        self.on_sort = on_sort             # (sort column) -> None
        self.on_select = on_select
        self.head = tk.Canvas(self, highlightthickness=0, bd=0, height=theme.px(HEAD_H))
        self.c = tk.Canvas(self, highlightthickness=0, bd=0, cursor="hand2")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.c.yview)
        self._sb = sb
        self.c.configure(yscrollcommand=self._scrolled)
        self.head.pack(side="top", fill="x")
        sb.pack(side="right", fill="y")
        self.c.pack(side="left", fill="both", expand=True)
        self.items: list[dict] = []
        self.selected: int | None = None
        self.sort_col, self.sort_reverse = "snr", True
        self._cols: list[tuple] = []
        self._height = 0
        self._sig = None
        self._head_sig = None
        # Canvas items are REUSED (v0.7.182): Tk never gives back the memory
        # of a deleted canvas item (~80 bytes each), and deleting + drawing
        # every row on every refresh grew POUNCer to 10 GB in a contest
        # night. Only the rows on screen (+ MARGIN) are drawn, into a pool
        # of slots that are re-filled - fast with 2000 spots, and flat memory.
        self._pool: list[dict] = []
        self._pool_sig = None
        self._drawn = (0, 0)
        self.c.bind("<Configure>", lambda e: self.draw(force=True))
        self.c.bind("<Button-1>", self._click)
        self.head.bind("<Button-1>", self._head_click)
        self.c.bind("<MouseWheel>", lambda e: self.c.yview_scroll(int(-e.delta / 120) * 3, "units"))
        self.c.bind("<Button-4>", lambda e: self.c.yview_scroll(-3, "units"))
        self.c.bind("<Button-5>", lambda e: self.c.yview_scroll(3, "units"))
        self.c.bind("<Up>", lambda e: self.move(-1))
        self.c.bind("<Down>", lambda e: self.move(1))
        self.c.bind("<Prior>", lambda e: self.move(-10))
        self.c.bind("<Next>", lambda e: self.move(10))
        self.c.bind("<space>", lambda e: self._tap_selected())
        # hover over Runs: what 'R26% ~18h' means (item "run_tip")
        self._tip: tk.Toplevel | None = None
        self._tip_at: tuple | None = None
        self.c.bind("<Motion>", self._hover)
        self.c.bind("<Leave>", lambda e: self._hide_tip())

    # ---- the same interface as TileList -----------------------------------

    def palette(self) -> dict:
        return COLORS["dark" if theme.LOOK == "dark" else "light"]

    def set_items(self, items: list[dict], selected: int | None = None) -> None:
        self.items = items
        if selected is not None:
            self.selected = selected
        elif self.selected is not None and self.selected >= len(items):
            self.selected = None
        self.draw()

    def set_sort(self, col: str, reverse: bool) -> None:
        self.sort_col, self.sort_reverse = col, reverse
        self._head_sig = None
        self._draw_head()

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
        rh = theme.px(ROW_H)
        y0, y1 = i * rh, (i + 1) * rh
        h = max(1, self._height)
        top, bottom = self.c.canvasy(0), self.c.canvasy(self.c.winfo_height())
        if y0 < top:
            self.c.yview_moveto(y0 / h)
        elif y1 > bottom:
            self.c.yview_moveto(max(0, (y1 - self.c.winfo_height()) / h))

    def _click(self, e) -> None:
        self.c.focus_set()
        i = int(self.c.canvasy(e.y) // max(1, theme.px(ROW_H)))
        if 0 <= i < len(self.items):
            self.selected = i
            self.draw(force=True)
            self.on_tap(i)

    def _head_click(self, e) -> None:
        if self.on_sort is None:
            return
        for col, x0, x1 in self._xs:
            if x0 <= e.x < x1:
                key = next((c[4] for c in self._cols if c[0] == col), None)
                if key:
                    self.on_sort(key)
                return

    # ---- hover help -------------------------------------------------------------

    def _cell_at(self, x: int, y: int) -> tuple[int | None, str | None]:
        i = int(self.c.canvasy(y) // max(1, theme.px(ROW_H)))
        col = next((k for k, x0, x1 in getattr(self, "_xs", []) if x0 <= x < x1), None)
        return (i if 0 <= i < len(self.items) else None), col

    def _hover(self, e) -> None:
        i, col = self._cell_at(e.x, e.y)
        text = self.items[i].get("run_tip", "") if i is not None and col == "run" else ""
        if not text:
            self._hide_tip()
            return
        if self._tip_at == (i, text) and self._tip is not None:
            self._tip.geometry(f"+{e.x_root + 16}+{e.y_root + 14}")
            return
        self._hide_tip()
        tip = tk.Toplevel(self.c)
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(tip, text=text, justify="left", wraplength=theme.px(380),
                 bg="#fff8e1" if theme.LOOK != "dark" else "#263238",
                 fg="#1f2933" if theme.LOOK != "dark" else "#eceff1",
                 font=(theme.FONT, 9), padx=8, pady=6, bd=1, relief="solid").pack()
        tip.geometry(f"+{e.x_root + 16}+{e.y_root + 14}")
        self._tip, self._tip_at = tip, (i, text)

    def _hide_tip(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
        self._tip, self._tip_at = None, None

    # ---- drawing --------------------------------------------------------------

    def _width(self) -> int:
        return max(1, self.c.winfo_width())

    def _draw_head(self) -> None:
        cols = self._cols or visible_columns(self.items)
        w = self._width()
        sig = (tuple(c[0] for c in cols), w, self.sort_col, self.sort_reverse, theme.LOOK)
        if sig == self._head_sig:
            return
        self._head_sig = sig
        p = self.palette()
        h = self.head
        h.delete("all")
        h.configure(background=p["bg"])
        hh = theme.px(HEAD_H)
        font = (theme.FONT, 9, "bold")
        pad = theme.px(8)
        for (col, x0, x1), c in zip(layout(cols, w, theme.px), cols):
            text = c[1]
            if c[4] and c[4] == self.sort_col:
                text += " ▼" if self.sort_reverse else " ▲"
            x = x1 - pad if c[3] == "e" else x0 + pad
            h.create_text(x, hh / 2, text=text, anchor=c[3], fill=p["head"], font=font)
        h.create_line(0, hh - 1, w, hh - 1, fill=p["head_line"])

    MARGIN = 15                         # rows drawn above / below the window

    def _visible(self) -> tuple[int, int]:
        """The rows to draw: on screen, plus MARGIN either side."""
        rh = max(1, theme.px(ROW_H))
        try:
            top = int(self.c.canvasy(0) // rh)
            n_vis = int(max(self.c.winfo_height(), rh * 20) // rh) + 1
        except tk.TclError:
            top, n_vis = 0, 40
        lo = max(0, top - self.MARGIN)
        return lo, min(len(self.items), top + n_vis + self.MARGIN)

    def _scrolled(self, first, last) -> None:
        """Scrollbar moved: draw the rows that came into view."""
        self._sb.set(first, last)
        lo, hi = self._visible()
        dlo, dhi = self._drawn
        if lo < dlo or hi > dhi:
            self.draw(force=True)

    def _slot(self) -> dict:
        """One row's canvas items, made once and re-filled (never deleted)."""
        c = self.c
        hid = {"state": "hidden"}
        slot = {"bg": c.create_rectangle(0, 0, 0, 0, outline="", **hid),
                "line": c.create_line(0, 0, 0, 0, **hid),
                "texts": {}, "marks": [], "tag_r": None, "tag_t": None}
        for col, _x0, _x1 in self._xs:
            anchor = next(k[3] for k in self._cols if k[0] == col)
            if col == "mult":
                slot["tag_r"] = c.create_rectangle(0, 0, 0, 0, outline="", **hid)
                slot["tag_t"] = c.create_text(0, 0, anchor="w", text="", **hid)
            else:
                slot["texts"][col] = c.create_text(0, 0, anchor="e" if col == "snr" else anchor,
                                                   text="", **hid)
            if col == "call":
                slot["marks"] = [c.create_text(0, 0, anchor="w", text="", **hid) for _ in range(3)]
        slot["sel"] = c.create_rectangle(0, 0, 0, 0, width=2, **hid)
        return slot

    def _hide_slot(self, slot: dict) -> None:
        c = self.c
        for k in ("bg", "line", "sel", "tag_r"):
            if slot.get(k) is not None:
                c.itemconfigure(slot[k], state="hidden")
        for t in list(slot["texts"].values()) + slot["marks"] + (
                [slot["tag_t"]] if slot.get("tag_t") is not None else []):
            c.itemconfigure(t, state="hidden", text="")

    def draw(self, force: bool = False) -> None:
        cols = visible_columns(self.items)
        w = self._width()
        sig = (tuple(tuple(sorted(it.items())) for it in self.items), self.selected, w,
               theme.LOOK, tuple(c[0] for c in cols))
        if sig == self._sig and not force:
            return
        self._sig = sig
        self._cols = cols
        self._xs = layout(cols, w, theme.px)
        self._draw_head()
        p = self.palette()
        c = self.c
        c.configure(background=p["bg"])
        pool_sig = (tuple(c_[0] for c_ in cols), w)
        if pool_sig != self._pool_sig:       # other columns / width: new slots (rare)
            c.delete("all")
            self._pool, self._pool_sig = [], pool_sig
        rh = theme.px(ROW_H)
        self._height = len(self.items) * rh
        c.configure(scrollregion=(0, 0, w, max(self._height, c.winfo_height())))
        lo, hi = self._visible()
        self._drawn = (lo, hi)
        while len(self._pool) < hi - lo:
            self._pool.append(self._slot())
        for k, slot in enumerate(self._pool):
            i = lo + k
            if i < hi:
                self._fill(slot, i, self.items[i], p, w, rh)
            else:
                self._hide_slot(slot)

    def _fill(self, slot: dict, i: int, it: dict, p: dict, w: int, rh: int) -> None:
        """Put row i into a slot: text, colours, fonts, places."""
        c = self.c
        pad = theme.px(8)
        call_f = (theme.FONT, 13, "bold")
        cell_f = (theme.FONT, 10)
        cell_b = (theme.FONT, 10, "bold")
        tag_f = (theme.FONT, 8, "bold")
        mark_f = (theme.FONT, 11, "bold")
        y0, y1 = i * rh, (i + 1) * rh
        ym = (y0 + y1) / 2
        if it.get("dx"):
            c.coords(slot["bg"], 0, y0, w, y1)
            c.itemconfigure(slot["bg"], fill=p["dx"], state="normal")
        else:
            c.itemconfigure(slot["bg"], state="hidden")
        c.coords(slot["line"], 0, y1 - 1, w, y1 - 1)
        c.itemconfigure(slot["line"], fill=p["line"], state="normal")
        grey = it.get("worked") or it.get("offline")
        text = p["worked"] if grey else (p["stale"] if it.get("stale") else p["text"])
        muted = p["worked"] if grey else p["muted"]
        for col, x0, x1 in self._xs:
            anchor = next(k[3] for k in self._cols if k[0] == col)
            x = x1 - pad if anchor == "e" else x0 + pad
            if col == "call":
                mult = int(it.get("mult") or 0)
                color = p["worked"] if grey else (
                    p["mult2"] if mult >= 2 else p["mult1"] if mult else
                    p["stale"] if it.get("stale") else p["call"])
                t = slot["texts"]["call"]
                c.coords(t, x, ym)
                c.itemconfigure(t, text=it.get("call", ""), fill=color, font=call_f,
                                state="normal")
                bb = c.bbox(t)
                bx = (bb[2] if bb else x) + theme.px(5)
                marks = [(m, mc, f) for m, mc, f in (
                    (it.get("busted") and "?", p["busted"], mark_f),
                    (it.get("own") and "\u2605", p["tried"], mark_f),
                    (it.get("tried"), p["tried"], (theme.FONT, 9, "bold"))) if m]
                for j, m_id in enumerate(slot["marks"]):
                    if j < len(marks) and bx < x1:
                        mark, mc, f = marks[j]
                        c.coords(m_id, bx, ym)
                        c.itemconfigure(m_id, text=mark, fill=muted if grey else mc, font=f,
                                        state="normal")
                        bb = c.bbox(m_id)
                        bx = (bb[2] if bb else bx) + theme.px(4)
                    else:
                        c.itemconfigure(m_id, state="hidden", text="")
            elif col == "mult":
                mult = int(it.get("mult") or 0)
                t, r = slot["tag_t"], slot["tag_r"]
                if mult and not grey:
                    c.coords(t, x + theme.px(5), ym)
                    c.itemconfigure(t, text="MM" if mult >= 2 else "M", fill=p["tag_fg"],
                                    font=tag_f, state="normal")
                    bb = c.bbox(t)
                    if bb:
                        c.coords(r, bb[0] - theme.px(4), bb[1] - 1, bb[2] + theme.px(4), bb[3] + 1)
                        c.itemconfigure(r, fill=p["tag2" if mult >= 2 else "tag1"],
                                        state="normal")
                        c.tag_lower(r, t)
                else:
                    c.itemconfigure(t, state="hidden", text="")
                    c.itemconfigure(r, state="hidden")
            else:
                t = slot["texts"][col]
                if col == "run":
                    rare = it.get("rare") and not grey
                    value = it.get("run", "") + (" \u26a1" if rare else "")
                    fill, font = (p["rare"] if rare else text), (cell_b if rare else cell_f)
                elif col == "snr":
                    value = it.get("snr", "")
                    fill = text
                    font = cell_b if it.get("strong") and not grey else cell_f
                else:
                    value = it.get(col, "")
                    fill = muted if col in ("age", "spotter", "type") else text
                    font = cell_f
                c.coords(t, x, ym)
                c.itemconfigure(t, text=value, fill=fill, font=font, state="normal")
        sel = slot["sel"]
        if i == self.selected or it.get("onfreq"):
            c.coords(sel, 1, y0 + 1, w - 2, y1 - 2)
            c.itemconfigure(sel, outline=p["sel"] if i == self.selected else p["onfreq"],
                            state="normal")
            c.tag_raise(sel)
        else:
            c.itemconfigure(sel, state="hidden")
