"""The main window's look: one flat, calm theme (ttk "clam" + our colours).

Colours and fonts live here, so the window code only names styles:
``Sort.TButton`` / ``SortOn.TButton`` (sort keys, the active one filled),
``FKey.TButton`` (TX keys), ``Log.TButton`` (ENTER, green), ``Accent.TButton``
(Radio A / B), the spot list (taller rows, bold headings) and the colours of
the top bar, status chips and status bar.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

FONT = "Segoe UI"

BG = "#eef1f5"          # window
PANEL = "#ffffff"       # spot list
TEXT = "#1f2933"
MUTED = "#5f6b7a"
LINE = "#d5dbe3"
ACCENT = "#1565c0"
TOP_BG = "#263238"      # top bar
TOP_FG = "#ffffff"
TOP_MUTED = "#b0bec5"
BAR_BG = "#e1e6ec"      # status bar
STRIPE = "#f6f8fb"      # every other spot row
SELECT = "#cfe3fb"

READY_COLORS = {"ok": ("#2e7d32", "#ffffff"), "warn": ("#f9a825", "#1f1f1f"),
                "bad": ("#c62828", "#ffffff")}


def apply(root: tk.Tk) -> ttk.Style:
    """Set fonts, the ttk theme and every style the window uses."""
    from tkinter import font as tkfont   # here: the tests stub tkinter
    for name, size, weight in (("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
                               ("TkHeadingFont", 10, "bold"), ("TkMenuFont", 10, "normal")):
        try:
            tkfont.nametofont(name).configure(family=FONT, size=size, weight=weight)
        except tk.TclError:
            pass
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("TButton", padding=(10, 4), relief="flat", borderwidth=1,
                    background="#ffffff", bordercolor=LINE, lightcolor="#ffffff",
                    darkcolor=LINE, focuscolor=ACCENT)
    style.map("TButton", background=[("pressed", "#dfe7f1"), ("active", "#f1f5fa")])
    # sort keys: plain / the active one filled
    style.configure("Sort.TButton", padding=(10, 3))
    style.configure("SortOn.TButton", padding=(10, 3), background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("SortOn.TButton", background=[("pressed", "#0d47a1"), ("active", "#1976d2")])
    style.configure("Accent.TButton", padding=(10, 3), background="#e8f0fb",
                    foreground=ACCENT, bordercolor="#9fc0ea")
    style.map("Accent.TButton", background=[("active", "#dbe8fa")])
    # F-keys: they transmit - dark text on white, red edge on hover
    style.configure("FKey.TButton", padding=(6, 6), font=(FONT, 10, "bold"))
    style.map("FKey.TButton", bordercolor=[("active", "#c62828")],
              background=[("pressed", "#fde0e0"), ("active", "#fff5f5")])
    style.configure("Log.TButton", padding=(6, 6), font=(FONT, 10, "bold"),
                    background="#2e7d32", foreground="#ffffff", bordercolor="#2e7d32",
                    lightcolor="#2e7d32", darkcolor="#2e7d32")
    style.map("Log.TButton", background=[("pressed", "#1b5e20"), ("active", "#388e3c")])
    # spot list
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    rowheight=26, borderwidth=0, font=(FONT, 10))
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background="#e9edf2",
                    foreground=MUTED, relief="flat", padding=(6, 4), bordercolor=LINE)
    style.map("Treeview.Heading", background=[("active", "#dde3ea")])
    style.configure("TSpinbox", arrowsize=12, padding=(4, 2))
    style.configure("Vertical.TScrollbar", background="#d9dee5", troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED)
    return style
