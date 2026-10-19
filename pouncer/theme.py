"""The main window's look: colours, fonts, the ttk theme and every style.

Three looks (``[ui] look``, View > Look; used from the next start):

* ``dark``    - Windows 11 style (the vendored Sun Valley ttk theme, MIT) in
                the phone page's dark colours, dark title bar;
* ``light``   - the same Windows 11 style, light;
* ``classic`` - the flat light ttk "clam" look of POUNCer up to v0.7.92.

Colours live here as module values (``theme.BG`` ...), set by ``apply()``
before the window's widgets are made, so the window code only names them.
``SCALE`` = the screen's DPI / 96 (Windows display scaling: POUNCer is DPI
aware, so it draws sharp and sizes its pixel values with ``px()``).
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

LOOKS = ("dark", "light", "classic")
LOOK = "classic"
SCALE = 1.0
FONT = "Segoe UI"
MODERN = False          # dark / light: Sun Valley theme, flat coloured keys

# ---- the palette (set by apply) ------------------------------------------
_CLASSIC = dict(
    BG="#eef1f5", PANEL="#ffffff", TEXT="#1f2933", MUTED="#5f6b7a", LINE="#d5dbe3",
    ACCENT="#1565c0", TOP_BG="#263238", TOP_FG="#ffffff", TOP_MUTED="#b0bec5",
    BAR_BG="#e1e6ec", STRIPE="#f6f8fb", SELECT="#cfe3fb",
    # spot list rows: text / background per kind
    T_STRONG="#1b5e20", T_DX="#fff4e5", T_ONFREQ="#d4ebff", T_MULT="#b71c1c",
    T_WORKED="#9aa5b1",
    # check line: ok / warn / bad = (background, text)
    CHECK={"ok": ("#e8f5e9", "#1b5e20"), "warn": ("#fff8e1", "#8d6e00"),
           "bad": ("#ffebee", "#b71c1c")},
    HELP_TEXT="#555555",
)
_LIGHT = dict(_CLASSIC, BG="#fafafa", PANEL="#ffffff", BAR_BG="#f0f0f0", LINE="#e0e0e0",
              ACCENT="#005fb8", SELECT="#cce4f7")
_DARK = dict(
    BG="#1c1c1c", PANEL="#202020", TEXT="#f2f2f2", MUTED="#a5adb5", LINE="#333333",
    ACCENT="#57c8ff", TOP_BG="#0b0f12", TOP_FG="#ffffff", TOP_MUTED="#90a4ae",
    BAR_BG="#141414", STRIPE="#262626", SELECT="#2d4152",
    # as on the phone: strong = green, DX = warm, tuned = blue, new mult = pink
    T_STRONG="#81c784", T_DX="#33290f", T_ONFREQ="#0d3a5c", T_MULT="#ff6e9c",
    T_WORKED="#6b7680",
    CHECK={"ok": ("#16301a", "#a5d6a7"), "warn": ("#332a0d", "#ffd54f"),
           "bad": ("#3b1418", "#ff8a80")},
    HELP_TEXT="#a5adb5",
)
_PALETTES = {"classic": _CLASSIC, "light": _LIGHT, "dark": _DARK}

BG = PANEL = TEXT = MUTED = LINE = ACCENT = TOP_BG = TOP_FG = TOP_MUTED = ""
BAR_BG = STRIPE = SELECT = T_STRONG = T_DX = T_ONFREQ = T_MULT = T_WORKED = HELP_TEXT = ""
CHECK: dict = {}

# keys on the window (modern looks): like the phone - F-keys green, ENTER
# green, Spot It blue, red while pressed
KEY_COLORS = {"fkey": "#2e7d32", "log": "#1b5e20", "spot": "#1565c0", "pressed": "#c62828"}

READY_COLORS = {"ok": ("#2e7d32", "#ffffff"), "warn": ("#f9a825", "#1f1f1f"),
                "bad": ("#c62828", "#ffffff")}


def _set_palette(look: str) -> None:
    globals().update(_PALETTES.get(look, _CLASSIC))


_set_palette("classic")


def px(n: float) -> int:
    """A size in pixels at 100 % scaling -> pixels on this screen."""
    return int(round(n * SCALE))


def sunvalley_dir() -> str:
    """The vendored Sun Valley theme (next to this file, or in the exe)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cand = os.path.join(base, "pouncer", "sunvalley")
        if os.path.isdir(cand):
            return cand
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunvalley")


def dpi_aware() -> None:
    """Windows: draw at the screen's real resolution (sharp text at 125 / 150 %
    scaling) instead of being stretched by Windows. Call before Tk starts."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)    # system DPI aware
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - old Windows: stays as it was
        pass


def title_bar(win, dark: bool | None = None) -> None:
    """Windows 10 (2004+) / 11: a dark title bar for a dark look."""
    if sys.platform != "win32":
        return
    if dark is None:
        dark = LOOK == "dark"
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):      # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd), attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - only looks
        pass


def _pick_font(root) -> str:
    from tkinter import font as tkfont
    try:
        fams = set(tkfont.families(root))
    except tk.TclError:
        return "Segoe UI"
    for f in ("Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Helvetica"):
        if f in fams:
            return f
    return "Segoe UI"


def apply(root: tk.Tk, look: str = "classic") -> ttk.Style:
    """Pick the look, set fonts, the ttk theme and every style the window uses."""
    global LOOK, SCALE, FONT, MODERN
    look = look if look in LOOKS else "dark"
    from tkinter import font as tkfont   # here: the tests stub tkinter
    try:
        SCALE = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except (tk.TclError, ValueError, TypeError):
        SCALE = 1.0
    style = ttk.Style(root)
    if look in ("dark", "light"):
        try:
            if not getattr(root, "_sv_loaded", False):
                root.tk.call("source", os.path.join(sunvalley_dir(), "sv.tcl"))
                root._sv_loaded = True
            style.theme_use(f"sun-valley-{look}")
        except tk.TclError:
            look = "classic"           # theme files missing: the classic look
    LOOK, MODERN = look, look != "classic"
    _set_palette(look)
    FONT = _pick_font(root) if MODERN else "Segoe UI"
    for name, size, weight in (("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
                               ("TkHeadingFont", 10, "bold"), ("TkMenuFont", 10, "normal")):
        try:
            tkfont.nametofont(name).configure(family=FONT, size=size, weight=weight)
        except tk.TclError:
            pass
    root.configure(bg=BG)
    if MODERN:
        _modern_styles(style)
    else:
        _classic_styles(style)
    title_bar(root)
    return style


def _modern_styles(style: ttk.Style) -> None:
    """Sun Valley draws its widgets from images: our styles only set fonts,
    paddings and the list; the sort key that is on uses its Accent button."""
    style.configure(".", font=(FONT, 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT, font=(FONT, 10))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 10))
    style.configure("Sort.TButton", padding=(px(10), px(2)))
    try:
        style.layout("SortOn.TButton", style.layout("Accent.TButton"))
        style.configure("SortOn.TButton", **(style.configure("Accent.TButton") or {}))
        style.map("SortOn.TButton", **(style.map("Accent.TButton") or {}))
    except tk.TclError:
        pass
    style.configure("SortOn.TButton", padding=(px(10), px(2)))
    for name in ("FKey.TButton", "Log.TButton"):
        style.configure(name, font=(FONT, 10, "bold"), padding=(px(6), px(4)))
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    rowheight=px(22), font=(FONT, 9))   # small, like the old table
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", font=(FONT, 8, "bold"), foreground=MUTED)


def _classic_styles(style: ttk.Style) -> None:
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
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
                    rowheight=px(26), borderwidth=0, font=(FONT, 10))
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background="#e9edf2",
                    foreground=MUTED, relief="flat", padding=(6, 4), bordercolor=LINE)
    style.map("Treeview.Heading", background=[("active", "#dde3ea")])
    style.configure("TSpinbox", arrowsize=12, padding=(4, 2))
    style.configure("Vertical.TScrollbar", background="#d9dee5", troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED)


def key_button(parent, text: str, kind: str, command) -> tk.Button:
    """A flat coloured key (modern looks) - green F-keys / ENTER, blue Spot It,
    red while pressed, like the phone."""
    bg = KEY_COLORS.get(kind, KEY_COLORS["fkey"])
    return tk.Button(parent, text=text, command=command, bg=bg, fg="#ffffff",
                     activebackground=KEY_COLORS["pressed"], activeforeground="#ffffff",
                     relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                     font=(FONT, 10, "bold"), padx=px(6), pady=px(5))
