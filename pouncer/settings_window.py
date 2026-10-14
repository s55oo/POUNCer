"""Settings window of the exe (Tk): the same settings as the /settings page.

Built from ``settings.SETTINGS`` - one tab per group (Tap -> N1MM, SO2R /
SO2V, RBN, SSB, Program). A change is saved to config.ini at once (tick
box / list: when changed; number / text: Enter or leaving the box) through
the same ``save`` the browser page uses, so both always agree. Settings
used only after a restart are marked with ↻ and show the Restart button.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

OK_COLOR, ERR_COLOR, LATER_COLOR = "#2e7d32", "#c62828", "#ef6c00"


def group_items(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """[(group, [items])] in the order the groups first appear."""
    out: dict[str, list[dict]] = {}
    for it in items:
        out.setdefault(it.get("group") or "Other", []).append(it)
    return list(out.items())


def display_value(item: dict) -> str:
    v = item.get("value")
    if item["kind"] == "bool":
        return "true" if v else "false"
    return "" if v is None else str(v)


class SettingsWindow:
    """One Toplevel; ``open()`` again just brings it to the front."""

    def __init__(self, root, get_items, save, restart=None, config_path: str = "") -> None:
        self.root = root
        self.get_items = get_items      # () -> list of setting dicts (settings.current)
        self.save = save                # (key, text) -> {"ok", "live", "value"/"error"}
        self.restart = restart          # () -> None, or None (not the exe)
        self.config_path = config_path
        self.win: tk.Toplevel | None = None
        self.state: dict[str, ttk.Label] = {}
        self.vars: dict[str, tk.Variable] = {}
        self.saved: dict[str, str] = {}

    def open(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("POUNCer settings")
        win.geometry("700x520")
        win.minsize(460, 320)
        top = ttk.Frame(win, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        lead = ttk.Label(top, justify="left", wraplength=660, text=(
            "Saved straight into config.ini: a tick box or list at once, a number or text "
            "with Enter (or when you leave the box). ↻ = used after a restart."
            + (f"\n{self.config_path}" if self.config_path else "")))
        lead.pack(side="left", fill="x", expand=True)
        lead.bind("<Configure>", lambda e: lead.configure(wraplength=max(e.width - 4, 200)))
        self.restart_btn = tk.Button(top, text="Restart POUNCer", bg=LATER_COLOR, fg="#ffffff",
                                     relief="flat", padx=10, command=self._restart)
        book = ttk.Notebook(win)
        book.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        for group, items in group_items(self.get_items()):
            tab = ttk.Frame(book, padding=10)
            tab.columnconfigure(0, weight=1)
            book.add(tab, text=group)
            for row, it in enumerate(items):
                self._row(tab, row, it)
        win.bind("<Escape>", lambda _e: win.destroy())

    def _row(self, parent, row: int, it: dict) -> None:
        key = it["key"]
        box = ttk.Frame(parent)
        box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        box.columnconfigure(0, weight=1)
        label = it["label"] + ("" if it.get("live") else "  ↻")
        name = ttk.Label(box, text=label, font=("Segoe UI", 10, "bold"))
        name.grid(row=0, column=0, sticky="w")
        if not it.get("live"):
            name.configure(foreground=LATER_COLOR)
        widget = self._widget(box, it)
        widget.grid(row=0, column=1, sticky="e", padx=(8, 0))
        if it.get("help"):
            hl = ttk.Label(box, text=it["help"], foreground="#555555", justify="left")
            hl.grid(row=1, column=0, columnspan=2, sticky="w")
            hl.bind("<Configure>", lambda e, w=hl: w.configure(wraplength=max(e.width - 4, 200)))
        st = ttk.Label(box, text="")
        st.grid(row=2, column=0, columnspan=2, sticky="w")
        self.state[key] = st

    def _widget(self, parent, it: dict):
        key, kind = it["key"], it["kind"]
        text = display_value(it)
        self.saved[key] = text
        if kind == "bool":
            var = tk.BooleanVar(value=bool(it.get("value")))
            self.vars[key] = var
            return ttk.Checkbutton(parent, variable=var,
                                   command=lambda: self._save(it, "true" if var.get() else "false"))
        var = tk.StringVar(value=text)
        self.vars[key] = var
        if kind == "choice":
            w = ttk.Combobox(parent, textvariable=var, values=list(it["choices"]),
                             state="readonly", width=12)
            w.bind("<<ComboboxSelected>>", lambda _e: self._save(it, var.get()))
            return w
        w = ttk.Entry(parent, textvariable=var, width=8 if kind == "int" else 24)
        w.bind("<Return>", lambda _e: self._save(it, var.get()))
        w.bind("<FocusOut>", lambda _e: self._save(it, var.get()))
        return w

    def _save(self, it: dict, text: str) -> None:
        key = it["key"]
        if text.strip() == self.saved.get(key, "").strip():
            return                       # nothing changed (e.g. just left the box)
        res = self.save(key, text) or {}
        st = self.state.get(key)
        if res.get("ok"):
            shown = display_value({"kind": it["kind"], "value": res.get("value")})
            self.saved[key] = shown
            var = self.vars.get(key)
            if var is not None and it["kind"] != "bool":
                var.set(shown)
            if st is not None:
                st.configure(foreground=OK_COLOR if res.get("live") else LATER_COLOR,
                             text="Saved - in use now" if res.get("live")
                             else "Saved - used after a restart")
            if not res.get("live") and self.restart is not None:
                self.restart_btn.pack(side="right")
        elif st is not None:
            st.configure(foreground=ERR_COLOR, text=res.get("error") or "not saved")

    def _restart(self) -> None:
        if self.restart is not None:
            self.restart()
