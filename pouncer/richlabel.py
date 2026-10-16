"""An info line for the main window with **bold** values.

A plain Tk label can't mix normal and bold text, so this is a read-only
Text widget that looks like a label: same background and font, no border,
word wrap to the window width, as many lines as it needs. ``**...**`` in
the text is shown bold (the values: frequency, spotter, ports, IPs ...);
the text can still be selected and copied (e.g. the phone URL).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


def segments(text: str) -> list[tuple[str, bool]]:
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]."""
    out = []
    for i, part in enumerate(str(text).split("**")):
        if part:
            out.append((part, i % 2 == 1))
    return out


def plain(text: str) -> str:
    return str(text).replace("**", "")


class RichLabel(tk.Text):
    def __init__(self, parent, text: str = "", bg: str | None = None, fg: str | None = None,
                 bold_fg: str | None = None, size: int | None = None) -> None:
        base = tkfont.nametofont("TkDefaultFont").copy()
        if size:
            base.configure(size=size)
        bold = base.copy()
        bold.configure(weight="bold")
        self._fonts = (base, bold)    # keep references (else Tk drops the fonts)
        bg = bg or ttk.Style().lookup("TFrame", "background") or parent.winfo_toplevel().cget("bg")
        super().__init__(parent, height=1, width=1, wrap="word", font=base, bg=bg,
                         borderwidth=0, highlightthickness=0, relief="flat", padx=0, pady=1,
                         cursor="arrow", takefocus=0, exportselection=True)
        if fg:
            super().configure(fg=fg)
        self.tag_configure("b", font=bold, foreground=bold_fg or fg or "")
        self._raw = None
        self._lines = 1
        self.set(text)
        self.bind("<Configure>", lambda e: self._fit(), add="+")

    # label-like API: .configure(text=...) / .cget("text")
    def configure(self, cnf=None, **kw):
        if "text" in kw:
            self.set(kw.pop("text"))
        if cnf or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def cget(self, key):
        if key == "text":
            return plain(self._raw or "")
        return super().cget(key)

    def set(self, text: str) -> None:
        if text == self._raw:
            return
        self._raw = text
        super().configure(state="normal")
        self.delete("1.0", "end")
        for part, bold in segments(text):
            self.insert("end", part, ("b",) if bold else ())
        super().configure(state="disabled")
        self._fit()

    def _fit(self) -> None:
        """As many lines high as the wrapped text needs."""
        try:
            n = int(self.tk.call(self._w, "count", "-update", "-displaylines", "1.0", "end"))
        except (tk.TclError, ValueError):
            return
        n = max(1, n)
        if n != self._lines:
            self._lines = n
            super().configure(height=n)
