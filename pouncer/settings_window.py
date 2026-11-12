"""Settings window of the exe (Tk): the same settings as the /settings page.

Built from ``settings.SETTINGS``, laid out by ``PAGES``: a list of pages on
the left (Display, Alerts & history, N1MM & radios, Phone page, Spots,
Cluster server, Startup & updates), all of them on one scrolling sheet on
the right - a page name jumps to its part, and the name of the part in view
is lit while scrolling. The search box at the top (Ctrl+F) keeps only the
settings whose name or help has every typed word.

Each row: the name, its box / tick right next to it, then one grey help line
("more" opens the rest). Nothing is written until **Save** (top right, or
Ctrl+S): a change only marks the row "changed - not saved yet"; Save writes
them all to config.ini through the same ``save`` the browser page uses (so
both always agree), Discard puts the boxes back; closing the window with
unsaved changes asks. Every saved change is logged by the app (old -> new).
Settings used only after a restart are marked with ↻ and show the Restart
button.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import theme

OK_COLOR, ERR_COLOR, LATER_COLOR = "#2e7d32", "#c62828", "#ef6c00"
PENDING_COLOR = "#f9a825"

_AUTOSTART = ["autostart.enabled"]
for _n in range(1, 9):
    _AUTOSTART += [f"autostart.app{_n}", f"autostart.app{_n}_delay", f"autostart.app{_n}_wait_n1mm"]
_AUTOSTART += ["autostart.folder", "autostart.folder_delay",
               "autostart.wait_n1mm", "autostart.n1mm_timeout"]

# page -> [(part, [setting keys])]; a key not listed here lands on "Other"
PAGES: list[tuple[str, list[tuple[str, list[str]]]]] = [
    ("Display", [
        ("Window", ["ui.layout", "ui.look", "ui.zo_mode", "ui.spot_tiles", "ui.band_lights",
                    "ui.check_line", "ui.rate_line", "ui.internet_check"]),
        ("Spot list", ["ui.min_snr", "ui.min_snr_keep_mults", "ui.mark_tried",
                       "ui.tried_minutes", "scp.enabled"]),
        ("Keys", ["ui.fkeys", "ui.fkeys_half"]),
    ]),
    ("Alerts & history", [
        ("Pop-ups", ["ui.alert_popup", "ui.alert_sound", "ui.self_spot_alert",
                     "ui.self_spot_band_only"]),
        ("History hints", ["worked.profile", "ui.urgency_contest_minutes"]),
    ]),
    ("N1MM & radios", [
        ("Tap -> N1MM", ["n1mm.qsy_wait_ms", "n1mm.qsy_settle_ms", "n1mm.type_mode",
                         "n1mm.wipe_before_call", "n1mm.focus_n1mm"]),
        ("SO2R / SO2V", ["n1mm.default_radio", "n1mm.radio_b_qsy", "n1mm.freq_decimal",
                         "n1mm.return_focus", "n1mm.focus_back_to_a"]),
        ("CW speed", ["n1mm.cw_speed", "n1mm.cw_speed_popup"]),
    ]),
    ("Phone page", [
        ("", ["web.phone_control", "web.cw_keyboard"]),
    ]),
    ("Spots", [
        ("Sources", ["rbn.sources", "rbn.history", "rbn.hide_beacons", "rbn.pause_on_phone",
                     "rbn.skimmer_quiet_minutes"]),
        ("Backups", ["rbn.telnet_backup", "rbn.hamqth_backup", "rbn.backup_host",
                     "rbn.backup_port", "rbn.backup_commands", "rbn.http_backup"]),
        ("SSB", ["ssb.follow_radio_mode", "ssb.mode", "ssb.use_n1mm_spots", "ssb.pskreporter",
                 "ssb.host", "ssb.port", "ssb.login", "ssb.self_spot_host", "ssb.self_spot_port",
                 "ssb.self_spot_filter", "ssb.self_spot_ssid"]),
        ("Spotters - who heard it", ["filter.mode", "rbn.spotter", "filter.dxcc",
                                     "filter.zones", "filter.conts"]),
        ("Spotted - who was heard", ["spotted.mode", "spotted.calls", "spotted.dxcc",
                                     "spotted.zones", "spotted.conts"]),
    ]),
    ("Cluster server", [
        ("", ["cluster.enabled", "cluster.port", "cluster.relay", "cluster.spots_out"]),
    ]),
    ("Startup & updates", [
        ("Autostart", _AUTOSTART),
        ("Country file & Super Check Partial", ["cty.auto_update", "cty.update_n1mm",
                                                "scp.update_n1mm"]),
        ("Updates", ["ui.auto_update"]),
    ]),
]


def group_items(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """[(group, [items])] in the order the groups first appear."""
    out: dict[str, list[dict]] = {}
    for it in items:
        out.setdefault(it.get("group") or "Other", []).append(it)
    return list(out.items())


def page_items(items: list[dict]) -> list[tuple[str, list[tuple[str, list[dict]]]]]:
    """[(page, [(part, [items])])] by ``PAGES``; settings not in it go to
    "Other" under their own group, so a new setting is never lost."""
    by_key = {it["key"]: it for it in items}
    placed: set[str] = set()
    out = []
    for page, parts in PAGES:
        got = []
        for part, keys in parts:
            its = [by_key[k] for k in keys if k in by_key and k not in placed]
            placed.update(it["key"] for it in its)
            if its:
                got.append((part, its))
        if got:
            out.append((page, got))
    rest = [it for it in items if it["key"] not in placed]
    if rest:
        out.append(("Other", group_items(rest)))
    return out


def display_value(item: dict) -> str:
    v = item.get("value")
    if item["kind"] == "bool":
        return "true" if v else "false"
    return "" if v is None else str(v)


def split_label(label: str) -> tuple[str, bool]:
    """'  ... TCP port' -> ('TCP port', True): a sub-option, shown indented."""
    s = label.strip()
    sub = label != label.lstrip() or s.startswith("...")
    if s.startswith("..."):
        s = s[3:].strip()
    return (s[:1].upper() + s[1:]) if s else s, sub


def matches(it: dict, query: str) -> bool:
    """Every typed word is in the name, help or key."""
    words = query.lower().split()
    hay = " ".join((it.get("label", ""), it.get("help", ""), it.get("key", ""))).lower()
    return all(w in hay for w in words)


class SettingsWindow:
    """One Toplevel; ``open()`` again just brings it to the front."""

    NAME_W = 250            # px: the name column, so the boxes line up

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
        self.rows: list[dict] = []          # {item, frame, page, part}
        self.heads: list[dict] = []         # {widget, page, part (None = page heading)}
        self.helps: list[dict] = []         # {label, more, text, open, indent}
        self.pages: list[str] = []
        self._wrap_w = 0

    def open(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()

    # ---- building ---------------------------------------------------------
    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        win.after(10, lambda w=win: theme.title_bar(w))      # dark title bar in the dark look
        self.win = win
        self.rows, self.heads, self.helps, self.pages = [], [], [], []
        win.title("POUNCer settings")
        h = max(theme.px(420), min(theme.px(760), win.winfo_screenheight() - 120))
        win.geometry(f"{theme.px(880)}x{h}")
        win.minsize(theme.px(560), theme.px(320))
        size = 9
        try:
            size = abs(int(tkfont.nametofont("TkDefaultFont").cget("size"))) or 9
        except (tk.TclError, ValueError):
            pass
        self.help_font = tkfont.Font(root=win, family=theme.FONT, size=max(size - 1, 8))
        self.name_font = tkfont.Font(root=win, family=theme.FONT, size=size, weight="bold")
        self.part_font = tkfont.Font(root=win, family=theme.FONT, size=size, weight="bold")
        self.page_font = tkfont.Font(root=win, family=theme.FONT, size=size + 4, weight="bold")

        # top: search (left), Restart / Discard / Save (right)
        top = ttk.Frame(win, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        self.save_btn = tk.Button(top, text="Save", bg=OK_COLOR, fg="#ffffff", relief="flat",
                                  padx=14, font=(theme.FONT, 10, "bold"), command=self.save_all,
                                  state="disabled", disabledforeground="#cfd8dc")
        self.save_btn.pack(side="right", padx=(6, 0))
        self.discard_btn = ttk.Button(top, text="Discard", command=self.discard, state="disabled")
        self.discard_btn.pack(side="right", padx=(6, 0))
        self.restart_btn = tk.Button(top, text="Restart POUNCer", bg=LATER_COLOR, fg="#ffffff",
                                     relief="flat", padx=10, command=self._restart)
        ttk.Label(top, text="Search").pack(side="left")
        self.search_var = tk.StringVar()
        self.search = ttk.Entry(top, textvariable=self.search_var, width=32)
        self.search.pack(side="left", padx=(6, 0))
        self.search_var.trace_add("write", lambda *_a: self._filter())
        self.clear_btn = ttk.Button(top, text="Clear", width=6, command=lambda: self.search_var.set(""))
        self.clear_btn.pack(side="left", padx=(2, 0))

        hint = ("Nothing is written to config.ini before Save (Ctrl+S).  "
                "↻ = used after a restart.")
        if self.config_path:
            hint += f"  {self.config_path}"
        self.hint = hint
        self.info = ttk.Label(win, text=hint, padding=(10, 0), foreground=theme.HELP_TEXT)
        self.info.pack(fill="x")

        body = ttk.Frame(win, padding=(10, 4, 10, 10))
        body.pack(fill="both", expand=True)
        # left: the pages
        self.side = tk.Listbox(body, activestyle="none", exportselection=False,
                               width=20, borderwidth=0, highlightthickness=1,
                               font=(theme.FONT, size + 1),
                               background=theme.PANEL or "#ffffff",
                               foreground=theme.TEXT or "#1f2933",
                               selectbackground=theme.SELECT or "#cfe3fb",
                               selectforeground=theme.TEXT or "#1f2933",
                               highlightbackground=theme.LINE or "#d5dbe3",
                               highlightcolor=theme.LINE or "#d5dbe3")
        self.side.pack(side="left", fill="y", padx=(0, 10))
        self.side.bind("<<ListboxSelect>>", self._jump)
        # right: every page on one scrolling sheet
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(right, highlightthickness=0, borderwidth=0)
        try:
            canvas.configure(background=ttk.Style().lookup("TFrame", "background") or "#eef1f5")
        except tk.TclError:
            pass
        bar = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=lambda a, b: (bar.set(a, b), self._lit()))
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=(4, 0, 12, 12))
        inner.columnconfigure(0, weight=1)
        item = canvas.create_window((0, 0), window=inner, anchor="nw")
        self.canvas, self.inner = canvas, inner
        canvas._pouncer_scroll = True          # the wheel handler finds it

        row = 0
        for page, parts in page_items(self.get_items()):
            self.pages.append(page)
            self.side.insert("end", "  " + page)
            head = ttk.Label(inner, text=page, font=self.page_font, foreground=theme.ACCENT)
            head.grid(row=row, column=0, sticky="w", pady=(14 if row else 4, 2))
            self.heads.append({"widget": head, "page": page, "part": None})
            row += 1
            for part, items in parts:
                if part:
                    ph = ttk.Label(inner, text=part, font=self.part_font,
                                   foreground=theme.MUTED)
                    ph.grid(row=row, column=0, sticky="w", pady=(8, 2))
                    self.heads.append({"widget": ph, "page": page, "part": part})
                    row += 1
                for it in items:
                    self._row(inner, row, it, page, part)
                    row += 1
        self.empty = ttk.Label(inner, text="", foreground=theme.HELP_TEXT)
        self.empty.grid(row=row, column=0, sticky="w", pady=20)
        self.empty.grid_remove()
        if self.pages:
            self.side.selection_set(0)
        self.side.configure(height=max(len(self.pages), 4))

        def fit(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def resized(e):
            canvas.itemconfigure(item, width=e.width)
            if abs(e.width - self._wrap_w) > 2:
                self._wrap_w = e.width
                self._rewrap()
            fit()

        inner.bind("<Configure>", fit)
        canvas.bind("<Configure>", resized)
        win.bind("<Escape>", self._escape)
        win.bind("<Control-s>", lambda _e: self.save_all())
        win.bind("<Control-S>", lambda _e: self.save_all())
        win.bind("<Control-f>", lambda _e: self._focus_search())
        win.bind("<Control-F>", lambda _e: self._focus_search())
        win.protocol("WM_DELETE_WINDOW", self.close)
        # the mouse wheel scrolls the sheet when the mouse is over it
        win.bind_all("<MouseWheel>", self._wheel, add="+")
        win.bind_all("<Button-4>", self._wheel, add="+")    # Linux
        win.bind_all("<Button-5>", self._wheel, add="+")

    def _row(self, parent, row: int, it: dict, page: str, part: str) -> None:
        key = it["key"]
        name_text, sub = split_label(it["label"])
        indent = 22 if sub else 0
        box = ttk.Frame(parent)
        box.grid(row=row, column=0, sticky="ew", pady=(2, 6), padx=(indent, 0))
        box.columnconfigure(0, minsize=max(self.NAME_W - indent, 120))
        box.columnconfigure(2, weight=1)
        later = not it.get("live")
        name = ttk.Label(box, text=name_text + ("  ↻" if later else ""), font=self.name_font,
                         wraplength=max(self.NAME_W - indent - 8, 110), justify="left")
        if later:
            name.configure(foreground=LATER_COLOR)
        name.grid(row=0, column=0, sticky="w")
        widget = self._widget(box, it)
        widget.grid(row=0, column=1, sticky="w", padx=(8, 0))
        if it["kind"] == "bool":       # clicking the name ticks it too
            name.bind("<Button-1>", lambda _e, w=widget: w.invoke())
        if it.get("help"):
            hb = ttk.Frame(box)
            hb.grid(row=1, column=0, columnspan=3, sticky="w")
            hl = ttk.Label(hb, text=it["help"], font=self.help_font,
                           foreground=theme.HELP_TEXT, justify="left")
            hl.pack(side="left", anchor="nw")
            more = ttk.Label(hb, text="more", font=self.help_font, foreground=theme.ACCENT,
                             cursor="hand2")
            h = {"label": hl, "more": more, "text": it["help"], "open": False, "indent": indent}
            more.bind("<Button-1>", lambda _e, h=h: self._toggle_help(h))
            self.helps.append(h)
        st = ttk.Label(box, text="")
        st.grid(row=2, column=0, columnspan=3, sticky="w")
        self.state[key] = st
        self.rows.append({"item": it, "frame": box, "page": page, "part": part})

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
            width = max([len(str(c)) for c in it["choices"]] + [8]) + 2
            w = ttk.Combobox(parent, textvariable=var, values=list(it["choices"]),
                             state="readonly", width=width)
            w.bind("<<ComboboxSelected>>", lambda _e: self._change(it, var.get()))
            return w
        if kind == "file":                 # a program path + Browse...
            box = ttk.Frame(parent)
            e = ttk.Entry(box, textvariable=var, width=40)
            e.pack(side="left")
            e.bind("<Return>", lambda _e: self._change(it, var.get()))
            e.bind("<FocusOut>", lambda _e: self._change(it, var.get()))

            def browse():
                from tkinter import filedialog
                path = filedialog.askopenfilename(
                    parent=self.win, title=it["label"].strip(" ."),
                    filetypes=[("Programs", "*.exe *.bat *.cmd *.lnk *.vbs *.ps1"), ("All files", "*.*")])
                if path:
                    import os
                    var.set(os.path.normpath(path))      # C:/a/b.exe -> C:\\a\\b.exe
                    self._change(it, var.get())
            ttk.Button(box, text="Browse...", command=browse).pack(side="left", padx=(4, 0))
            return box
        w = ttk.Entry(parent, textvariable=var, width=8 if kind == "int" else 30)
        w.bind("<Return>", lambda _e: self._change(it, var.get()))
        w.bind("<FocusOut>", lambda _e: self._change(it, var.get()))
        return w

    # ---- help lines: one line, "more" opens the rest ----------------------
    def _rewrap(self) -> None:
        for h in self.helps:
            self._show_help(h)

    def _show_help(self, h: dict) -> None:
        avail = max((self._wrap_w or 600) - h["indent"] - 40, 160)
        f = self.help_font
        text, label, more = h["text"], h["label"], h["more"]
        try:
            if h["open"]:
                label.configure(text=text, wraplength=avail - f.measure(" less ") - 6)
                more.configure(text="less")
                if not more.winfo_manager():
                    more.pack(side="left", anchor="sw", padx=(6, 0))
                return
            if "\n" not in text and f.measure(text) <= avail:
                label.configure(text=text, wraplength=0)
                if more.winfo_manager():
                    more.pack_forget()
                return
            room = avail - f.measure("…  more") - 6
            first = text.split("\n", 1)[0]
            lo, hi = 0, len(first)
            while lo < hi:                      # longest start that fits
                mid = (lo + hi + 1) // 2
                if f.measure(first[:mid]) <= room:
                    lo = mid
                else:
                    hi = mid - 1
            cut = first[:lo]
            if lo < len(first) and " " in cut[-20:]:
                cut = cut[:cut.rfind(" ")]      # end at a whole word
            label.configure(text=cut.rstrip(" ,;:-") + "…", wraplength=0)
            more.configure(text="more")
            if not more.winfo_manager():
                more.pack(side="left", anchor="nw", padx=(6, 0))
        except tk.TclError:
            pass

    def _toggle_help(self, h: dict) -> None:
        h["open"] = not h["open"]
        self._show_help(h)

    # ---- search and the page list -----------------------------------------
    def _focus_search(self) -> None:
        try:
            self.search.focus_set()
            self.search.select_range(0, "end")
        except tk.TclError:
            pass

    def _escape(self, _e=None) -> None:
        if self.search_var.get():
            self.search_var.set("")            # first Esc clears the search
        else:
            self.close()

    def _filter(self) -> None:
        q = self.search_var.get().strip()
        shown: set[tuple[str, str | None]] = set()
        n = 0
        for r in self.rows:
            ok = not q or matches(r["item"], q)
            if ok:
                r["frame"].grid()
                shown.add((r["page"], r["part"]))
                shown.add((r["page"], None))
                n += 1
            else:
                r["frame"].grid_remove()
        for hd in self.heads:
            if (hd["page"], hd["part"] or None) in shown:
                hd["widget"].grid()
            else:
                hd["widget"].grid_remove()
        for i, page in enumerate(self.pages):
            has = (page, None) in shown
            try:
                self.side.itemconfigure(i, foreground=(theme.TEXT or "#1f2933") if has
                                        else (theme.LINE or "#b0bec5"))
            except tk.TclError:
                pass
        if q and not n:
            self.empty.configure(text=f"No setting matches \u201c{q}\u201d")
            self.empty.grid()
        else:
            self.empty.grid_remove()
        try:
            self.inner.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _page_heads(self) -> list[tuple[str, tk.Widget]]:
        return [(hd["page"], hd["widget"]) for hd in self.heads
                if hd["part"] is None and hd["widget"].winfo_manager()]

    def _jump(self, _e=None) -> None:
        sel = self.side.curselection()
        if not sel:
            return
        page = self.pages[sel[0]]
        try:
            self.inner.update_idletasks()
            total = max(self.inner.winfo_height(), 1)
            for p, w in self._page_heads():
                if p == page:
                    self.canvas.yview_moveto(max(w.winfo_y() - 4, 0) / total)
                    break
        except tk.TclError:
            pass

    def _lit(self) -> None:
        """Light the page whose part is at the top of the sheet."""
        try:
            first = self.canvas.yview()[0]
            top = first * max(self.inner.winfo_height(), 1) + 30
            cur = None
            for p, w in self._page_heads():
                if w.winfo_y() <= top:
                    cur = p
            if cur is None:
                return
            i = self.pages.index(cur)
            if self.side.curselection() != (i,):
                self.side.selection_clear(0, "end")
                self.side.selection_set(i)
                self.side.see(i)
        except (tk.TclError, ValueError, AttributeError):
            pass

    def _wheel(self, event) -> None:
        """Scroll the sheet when the mouse is over it."""
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

    # ---- changes, Save, Discard -------------------------------------------
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
