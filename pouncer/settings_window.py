"""Settings window of the exe (Tk): the same settings as the /settings page.

Built from ``settings.SETTINGS`` - one tab per group (Tap -> N1MM, SO2R /
SO2V, RBN, SSB, Program). Nothing is written until **Save** (top right,
or Ctrl+S): a change only marks the row "changed - not saved yet"; Save writes
them all to config.ini through the same ``save`` the browser page uses (so
both always agree), Discard puts the boxes back; closing the window with
unsaved changes asks. Every saved change is logged by the app (old -> new).
Settings used only after a restart are marked with ↻ and show the Restart
button.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

OK_COLOR, ERR_COLOR, LATER_COLOR = "#2e7d32", "#c62828", "#ef6c00"
PENDING_COLOR = "#f9a825"


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
        self.pending: dict[str, tuple[dict, str]] = {}   # key -> (item, new text), not saved yet

    def open(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        win.after(10, lambda w=win: theme.title_bar(w))      # dark title bar in the dark look
        self.win = win
        win.title("POUNCer settings")
        # as tall as the screen allows (up to 760 px): more rows without scrolling
        h = max(theme.px(420), min(theme.px(760), win.winfo_screenheight() - 120))
        win.geometry(f"{theme.px(720)}x{h}")
        win.minsize(theme.px(460), theme.px(320))
        top = ttk.Frame(win, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        # Save / Discard first (right), then the text takes the rest
        self.save_btn = tk.Button(top, text="Save", bg=OK_COLOR, fg="#ffffff", relief="flat",
                                  padx=14, font=(theme.FONT, 10, "bold"), command=self.save_all,
                                  state="disabled", disabledforeground="#cfd8dc")
        self.save_btn.pack(side="right", padx=(6, 0))
        self.discard_btn = ttk.Button(top, text="Discard", command=self.discard, state="disabled")
        self.discard_btn.pack(side="right", padx=(6, 0))
        self.restart_btn = tk.Button(top, text="Restart POUNCer", bg=LATER_COLOR, fg="#ffffff",
                                     relief="flat", padx=10, command=self._restart)
        lead = ttk.Label(top, justify="left", wraplength=560, text=(
            "Change what you like, then press Save (Ctrl+S) - nothing is written to "
            "config.ini before. ↻ = used after a restart."
            + (f"\n{self.config_path}" if self.config_path else "")))
        lead.pack(side="left", fill="x", expand=True)
        lead.bind("<Configure>", lambda e: lead.configure(wraplength=max(e.width - 4, 200)))
        self.info = ttk.Label(win, text="", padding=(10, 0))
        self.info.pack(fill="x")
        book = ttk.Notebook(win)
        book.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        for group, items in group_items(self.get_items()):
            outer, tab = self._scroll_tab(book)
            book.add(outer, text=group)
            for row, it in enumerate(items):
                self._row(tab, row, it)
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<Control-s>", lambda _e: self.save_all())
        win.bind("<Control-S>", lambda _e: self.save_all())
        win.protocol("WM_DELETE_WINDOW", self.close)
        # the mouse wheel scrolls the tab under the mouse (all tabs can be long)
        win.bind_all("<MouseWheel>", self._wheel, add="+")
        win.bind_all("<Button-4>", self._wheel, add="+")    # Linux
        win.bind_all("<Button-5>", self._wheel, add="+")

    def _scroll_tab(self, book):
        """A tab that scrolls: a Canvas with the rows in a frame, and a
        scrollbar that shows only when the rows don't fit."""
        outer = ttk.Frame(book)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        try:
            canvas.configure(background=ttk.Style().lookup("TFrame", "background") or "#eef1f5")
        except tk.TclError:
            pass
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=10)
        inner.columnconfigure(0, weight=1)
        item = canvas.create_window((0, 0), window=inner, anchor="nw")

        def fit(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            need = inner.winfo_reqheight() > canvas.winfo_height() + 1
            if need and not bar.winfo_ismapped():
                bar.pack(side="right", fill="y")
            elif not need and bar.winfo_ismapped():
                bar.pack_forget()
                canvas.yview_moveto(0)

        inner.bind("<Configure>", fit)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(item, width=e.width), fit()))
        canvas._pouncer_scroll = True          # the wheel handler finds it
        return outer, inner

    def _wheel(self, event) -> None:
        """Scroll the settings tab under the mouse."""
        if self.win is None or not self.win.winfo_exists():
            return
        w = event.widget
        try:
            if not str(w).startswith(str(self.win)):
                return
        except tk.TclError:
            return
        while w is not None and not getattr(w, "_pouncer_scroll", False):
            w = getattr(w, "master", None)
        if w is None:
            return
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
            step *= max(1, abs(event.delta) // 120) if abs(event.delta) >= 120 else 1
        first, last = w.yview()
        if first <= 0 and last >= 1:
            return                               # all of it fits: nothing to scroll
        w.yview_scroll(step * 3, "units")

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
            hl = ttk.Label(box, text=it["help"], foreground=theme.HELP_TEXT, justify="left")
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
                                   command=lambda: self._change(it, "true" if var.get() else "false"))
        var = tk.StringVar(value=text)
        self.vars[key] = var
        # every keystroke marks the row changed (Save then takes what is typed,
        # even without Enter)
        var.trace_add("write", lambda *_a: self._change(it, var.get()))
        if kind == "choice":
            w = ttk.Combobox(parent, textvariable=var, values=list(it["choices"]),
                             state="readonly", width=12)
            w.bind("<<ComboboxSelected>>", lambda _e: self._change(it, var.get()))
            return w
        if kind == "file":                 # a program path + Browse...
            box = ttk.Frame(parent)
            e = ttk.Entry(box, textvariable=var, width=46)
            e.pack(side="left")
            e.bind("<Return>", lambda _e: self._change(it, var.get()))
            e.bind("<FocusOut>", lambda _e: self._change(it, var.get()))

            def browse():
                from tkinter import filedialog
                path = filedialog.askopenfilename(
                    parent=self.win, title=it["label"],
                    filetypes=[("Programs", "*.exe *.bat *.cmd *.lnk *.vbs *.ps1"), ("All files", "*.*")])
                if path:
                    import os
                    var.set(os.path.normpath(path))      # C:/a/b.exe -> C:\\a\\b.exe
                    self._change(it, var.get())
            ttk.Button(box, text="Browse...", command=browse).pack(side="left", padx=(4, 0))
            return box
        w = ttk.Entry(parent, textvariable=var, width=8 if kind == "int" else 24)
        w.bind("<Return>", lambda _e: self._change(it, var.get()))
        w.bind("<FocusOut>", lambda _e: self._change(it, var.get()))
        return w

    def _change(self, it: dict, text: str) -> None:
        """A box / tick changed: remember it, nothing is written yet."""
        key = it["key"]
        st = self.state.get(key)
        if text.strip() == self.saved.get(key, "").strip():
            if self.pending.pop(key, None) is not None and st is not None:
                st.configure(text="")            # back to what is saved
        else:
            self.pending[key] = (it, text)
            if st is not None:
                st.configure(foreground=PENDING_COLOR, text="changed - not saved yet (Save)")
        self._update_buttons()

    def _update_buttons(self) -> None:
        n = len(self.pending)
        state = "normal" if n else "disabled"
        try:
            self.save_btn.configure(state=state, text=f"Save ({n})" if n else "Save")
            self.discard_btn.configure(state=state)
        except (AttributeError, tk.TclError):
            pass

    def save_all(self) -> bool:
        """Write every pending change. True when all of them were saved."""
        if not self.pending:
            return True
        done, bad, later = 0, 0, False
        for key, (it, text) in list(self.pending.items()):
            res = self.save(key, text) or {}
            st = self.state.get(key)
            if res.get("ok"):
                shown = display_value({"kind": it["kind"], "value": res.get("value")})
                self.pending.pop(key, None)
                self.saved[key] = shown
                var = self.vars.get(key)
                if var is not None and it["kind"] != "bool" and var.get() != shown:
                    var.set(shown)           # (its trace sees "= saved": nothing pending)
                done += 1
                later = later or not res.get("live")
                if st is not None:
                    st.configure(foreground=OK_COLOR if res.get("live") else LATER_COLOR,
                                 text="Saved - in use now" if res.get("live")
                                 else "Saved - used after a restart")
            else:
                bad += 1
                if st is not None:
                    st.configure(foreground=ERR_COLOR, text=res.get("error") or "not saved")
        if later and self.restart is not None:
            self.restart_btn.pack(side="right", padx=(6, 0))
        try:
            self.info.configure(
                foreground=ERR_COLOR if bad else OK_COLOR,
                text=(f"Saved {done} change{'s' if done != 1 else ''}" if done else "")
                + (f" - {bad} not saved (see the red rows)" if bad else ""))
        except (AttributeError, tk.TclError):
            pass
        self._update_buttons()
        return not bad

    def discard(self) -> None:
        """Back to what config.ini holds."""
        items = list(self.pending.items())
        self.pending.clear()
        for key, (it, _text) in items:
            var = self.vars.get(key)
            if var is not None:
                old = self.saved.get(key, "")
                var.set(old == "true" if it["kind"] == "bool" else old)
            self.pending.pop(key, None)
            st = self.state.get(key)
            if st is not None:
                st.configure(text="")
        self._update_buttons()
        try:
            self.info.configure(foreground=theme.HELP_TEXT, text="Changes discarded")
        except (AttributeError, tk.TclError):
            pass

    def close(self) -> None:
        """Window closed: unsaved changes? Ask first."""
        if self.win is None:
            return
        if self.pending:
            from tkinter import messagebox
            ans = messagebox.askyesnocancel(
                "POUNCer settings", f"{len(self.pending)} change(s) not saved. Save them?",
                parent=self.win)
            if ans is None:
                return
            if ans and not self.save_all():
                return                      # a bad value: keep the window open
            if not ans:
                self.discard()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.win = None

    def _restart(self) -> None:
        if self.restart is not None:
            self.restart()
