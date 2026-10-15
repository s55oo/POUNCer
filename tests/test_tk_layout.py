"""Real-Tk check of the Windows window layout (runs where tkinter works,
e.g. the Windows build; skipped elsewhere)."""

import gc
import unittest

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # no Tk in this Python
    tk = None


@unittest.skipIf(tk is None, "tkinter not available")
class TkLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        # free the Tk objects now, in the main thread: collected later in
        # another test's thread (web server) they raise "main thread is not
        # in main loop" there and that request never gets an answer
        gc.collect()

    def _settle(self, geometry: str) -> None:
        self.root.geometry(geometry)
        for _ in range(5):
            self.root.update()

    def test_buttons_wrap_instead_of_clipping(self) -> None:
        from pouncer.app import FlowFrame

        flow = FlowFrame(self.root)
        flow.pack(fill="x")
        buttons = [
            flow.add(ttk.Button(flow, text=f"Button number {i}"), gap=12 if i else 0)
            for i in range(8)
        ]
        self._settle("320x400+10+10")
        width = flow.winfo_width()
        rows = {b.winfo_y() for b in buttons}
        self.assertGreater(len(rows), 2)  # wrapped onto several rows
        for b in buttons:
            self.assertLessEqual(b.winfo_x() + b.winfo_width(), width + 1)
            self.assertLessEqual(b.winfo_y() + b.winfo_height(), flow.winfo_height() + 1)

        self._settle("2400x400+10+10")
        self.assertEqual(len({b.winfo_y() for b in buttons}), 1)  # one row again

    def test_label_wraps_to_window_width(self) -> None:
        from pouncer.app import PouncerApp

        frame = ttk.Frame(self.root)
        frame.pack(fill="x")
        label = PouncerApp._wrap_label(frame, "word " * 80)
        self._settle("300x300+10+10")
        self.assertLessEqual(int(str(label.cget("wraplength"))), 300)
        self.assertLessEqual(label.winfo_reqwidth(), 310)
        self.assertGreater(label.winfo_reqheight(), 40)  # several lines


    def test_fkey_row_stays_one_row(self) -> None:
        row = ttk.Frame(self.root)
        row.pack(fill="x")
        buttons = []
        for i, text in enumerate(["F2 RPRT", "F3 TU", "F4 CALL", "F5 HisCALL", "F7 ?", "ENTER"]):
            row.columnconfigure(i, weight=1, uniform="fkey")
            b = ttk.Button(row, text=text, width=1)
            b.grid(row=0, column=i, sticky="ew", padx=2)
            buttons.append(b)
        for geo in ("320x200+10+10", "1400x200+10+10"):
            self._settle(geo)
            self.assertEqual(len({b.winfo_y() for b in buttons}), 1)
            self.assertLessEqual(buttons[-1].winfo_x() + buttons[-1].winfo_width(),
                                 row.winfo_width() + 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(tk is None, "tkinter not available")
class TkSettingsWindowTests(unittest.TestCase):
    """The exe's Settings window and menu bar (real Tk, e.g. the Windows build)."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        # free the Tk objects now, in the main thread: collected later in
        # another test's thread (web server) they raise "main thread is not
        # in main loop" there and that request never gets an answer
        gc.collect()

    def test_tabs_save_and_restart_button(self) -> None:
        import types
        from pouncer import settings as settingsmod
        from pouncer.settings_window import SettingsWindow, group_items

        cfg = types.SimpleNamespace(**{it["attr"]: None for it in settingsmod.SETTINGS})
        cfg.qsy_wait_ms, cfg.focus_back_to_a, cfg.radio_b_qsy = 1500, False, "udp-left"
        cfg.rbn_spotter, cfg.rbn_backup_commands, cfg.default_radio = "S53WW", (), 1
        saved, restarted = [], []

        def save(key, text):
            saved.append((key, text))
            if key == "n1mm.qsy_wait_ms" and text == "abc":
                return {"ok": False, "error": "not a number"}
            item = settingsmod.BY_KEY[key]
            return {"ok": True, "live": item["live"], "value": text}

        w = SettingsWindow(self.root, lambda: settingsmod.current(cfg), save,
                           restart=lambda: restarted.append(1), config_path="C:/x/config.ini")
        w.open()
        self.root.update()
        groups = [g for g, _ in group_items(settingsmod.current(cfg))]
        book = [c for c in w.win.winfo_children() if c.winfo_class() == "TNotebook"][0]
        self.assertEqual([book.tab(t, "text") for t in book.tabs()], groups)
        # number: Enter saves, bad value refused
        w.vars["n1mm.qsy_wait_ms"].set("abc")
        w._save(settingsmod.BY_KEY["n1mm.qsy_wait_ms"] | {"kind": "int"}, "abc")
        self.assertEqual(w.state["n1mm.qsy_wait_ms"].cget("text"), "not a number")
        w._save(settingsmod.BY_KEY["n1mm.qsy_wait_ms"], "2000")
        self.assertEqual(w.state["n1mm.qsy_wait_ms"].cget("text"), "Saved - in use now")
        # tick box
        w.vars["n1mm.focus_back_to_a"].set(True)
        w._save(settingsmod.BY_KEY["n1mm.focus_back_to_a"], "true")
        self.assertIn(("n1mm.focus_back_to_a", "true"), saved)
        # a restart setting shows the Restart button
        self.assertFalse(w.restart_btn.winfo_ismapped())
        w._save(settingsmod.BY_KEY["rbn.spotter"], "S50R")
        self.root.update()
        self.assertEqual(w.state["rbn.spotter"].cget("text"), "Saved - used after a restart")
        self.assertTrue(w.restart_btn.winfo_ismapped())
        w.restart_btn.invoke()
        self.assertEqual(restarted, [1])
        # unchanged value (just leaving the box) doesn't save again
        n = len(saved)
        w._save(settingsmod.BY_KEY["rbn.spotter"], "S50R")
        self.assertEqual(len(saved), n)
        w.open()                                   # second open: same window
        self.assertEqual(len([c for c in self.root.winfo_children()
                              if c.winfo_class() == "Toplevel"]), 1)

    def test_menu_bar(self) -> None:
        import types
        from pouncer.app import PouncerApp

        f = types.SimpleNamespace(
            root=self.root, set_call_var=tk.BooleanVar(value=True),
            hide_worked_var=tk.BooleanVar(value=False),
            compact_var=tk.BooleanVar(value=False), topmost_var=tk.BooleanVar(value=False),
            updater=types.SimpleNamespace(exe="C:/x/POUNCer.exe"),
            _settings_win=types.SimpleNamespace(open=lambda: None),
            cfg=types.SimpleNamespace(config_path="C:/x/config.ini"),
        )
        for name in ("_open_config_folder", "_reconnect_log", "_restart_now", "_shutdown_and_quit",
                     "_on_toggle_worked", "_clear_spots", "_open_browser", "_on_update_click",
                     "_open_url", "_about", "_apply_compact", "_apply_topmost"):
            setattr(f, name, lambda *a, **k: None)
        PouncerApp._build_menu(f)
        bar = f.menubar
        labels = [bar.entrycget(i, "label") for i in range(bar.index("end") + 1)]
        self.assertEqual(labels, ["File", "View", "Settings...", "Help"])
        view = self.root.nametowidget(bar.entrycget(1, "menu"))
        vlabels = [view.entrycget(i, "label") for i in range(view.index("end") + 1)
                   if view.type(i) != "separator"]
        self.assertIn("Hide worked (same band + mode)", vlabels)
        self.assertIn("Contest summary", vlabels)
        self.assertIn("Compact window (beside N1MM)", vlabels)
        self.assertIn("Always on top", vlabels)


@unittest.skipIf(tk is None, "tkinter not available")
class TkWindowMemoryTests(unittest.TestCase):
    """Main window size, columns and sort survive a restart (real Tk)."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        gc.collect()   # Tk objects freed here, in the main thread (see above)

    def _fake(self, d):
        import types
        from pouncer.app import PouncerApp
        cols = ("time", "freq", "call")
        tree = ttk.Treeview(self.root, columns=cols, show="headings")
        tree.pack()
        f = types.SimpleNamespace(root=self.root, tree=tree, columns=cols, sort_col="time",
                                  sort_reverse=True, cfg=types.SimpleNamespace(run_history_dir=d),
                                  compact_var=tk.BooleanVar(value=False),
                                  topmost_var=tk.BooleanVar(value=False),
                                  _normal_geom="", _compact_geom="")
        f._update_headings = f._update_sort_buttons = lambda: None
        f._apply_compact = lambda: None
        for name in ("_window_file", "_restore_window", "_save_window", "_on_close",
                     "_apply_topmost"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        return f

    def test_save_then_restore(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = self._fake(d)
            self.root.geometry("640x400+50+60")
            f.tree.column("call", width=222)
            f.sort_col, f.sort_reverse = "freq", False
            self.root.update()
            f._save_window()
            # next start: defaults, then restored
            f.sort_col, f.sort_reverse = "time", True
            f.tree.column("call", width=100)
            self.root.geometry("900x500+0+0")
            self.root.update()
            f._restore_window()
            self.root.update()
            self.assertEqual((f.sort_col, f.sort_reverse), ("freq", False))
            self.assertEqual(int(f.tree.column("call", "width")), 222)
            self.assertTrue(self.root.geometry().startswith("640x400"))
            f._on_close()                      # saves, closes the window
            f._save_window()                   # window gone: no crash, file kept
            from pouncer import winstate
            self.assertEqual(winstate.load(f._window_file())["sort"], "freq")


@unittest.skipIf(tk is None, "tkinter not available")
class TkSpotListTests(unittest.TestCase):
    """Spot list colours, marks and keyboard (real Tk)."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        # free the Tk objects now, in the main thread: collected later in
        # another test's thread (web server) they raise "main thread is not
        # in main loop" there and that request never gets an answer
        gc.collect()

    def test_rows_and_space_key(self) -> None:
        import types
        from pouncer.app import PouncerApp
        from pouncer.rbn import Spot

        def spot(call, khz, snr=10):
            return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=snr, wpm=28, spot_type="CQ",
                        spotter="S53WW-#", time_utc="1200Z", received=0)
        spots = [spot("S50A", 14025.0), spot("K1ABC", 14030.0), spot("PHB9CBR", 14040.0, 25),
                 spot("DL1AA", 14050.0, 25)]
        cols = ("time", "freq", "snr", "wpm", "call", "cont", "mult", "mode", "type", "spotter")
        tree = ttk.Treeview(self.root, columns=cols, show="headings")
        tree.pack()
        tapped = []
        f = types.SimpleNamespace(
            tree=tree, displayed=[], _row_spots=[],
            _sorted_visible=lambda: list(spots), _on_freq_spot=lambda items: None,
            worked=types.SimpleNamespace(is_worked=lambda c, k, m: c == "K1ABC"),
            _mults=lambda s: 1 if s.dxcall == "S50A" else 0,
            _run_fields=lambda s: {"run": 10, "hours": 20} if s.dxcall == "DL1AA" else {},
            scp=types.SimpleNamespace(busted=lambda c: c == "PHB9CBR"),
            _is_dx=lambda c: False, _continent=lambda c: "EU",
            _act_on_spot=lambda s: tapped.append(s.dxcall))
        for name in ("_rebuild", "_on_tree_key"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        f._row_key = PouncerApp._row_key            # a staticmethod
        f._rebuild()
        rows = {tree.item(i, "values")[4]: tree.item(i, "tags") for i in tree.get_children()}
        self.assertEqual(tuple(rows["S50A"]), ("mult",))          # new mult
        self.assertEqual(tuple(rows["K1ABC"]), ("worked",))       # worked: grey
        self.assertIn("PHB9CBR ?", rows)                          # not in SCP
        self.assertIn("⚡ DL1AA", rows)                       # rare runner
        # Space taps the selected spot; the selection survives a redraw
        tree.selection_set("2")
        f._rebuild()
        self.assertEqual(tree.selection(), ("2",))
        self.assertEqual(f._on_tree_key(None), "break")
        self.assertEqual(tapped, ["PHB9CBR"])


@unittest.skipIf(tk is None, "tkinter not available")
class TkCompactTests(unittest.TestCase):
    """View > Compact window: narrow, on top, A / B line, 3 columns; and back."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        # free the Tk objects now, in the main thread: collected later in
        # another test's thread (web server) they raise "main thread is not
        # in main loop" there and that request never gets an answer
        gc.collect()

    def test_compact_and_back(self) -> None:
        import types
        from pouncer.app import PouncerApp
        from pouncer.n1mm import RadioState

        cols = ("time", "freq", "snr", "wpm", "call", "cont", "mult", "mode", "type", "spotter")
        frames = {n: ttk.Frame(self.root, height=20) for n in ("header", "outer", "fkeys", "bottom")}
        tree_frame = ttk.Frame(self.root)
        frames["header"].pack(fill="x")
        frames["outer"].pack(fill="x")
        frames["fkeys"].pack(fill="x")
        tree_frame.pack(fill="both", expand=True)
        frames["bottom"].pack(fill="x")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        tree.pack(fill="both", expand=True)
        bar = ttk.Frame(self.root)
        btn, info = ttk.Button(bar), ttk.Label(bar)
        st = RadioState()
        st.update({"radio_nr": 1, "freq_khz": 14025.0, "mode": "CW", "active_radio": 1,
                   "entry_window_hwnd": 1, "station": "", "tx_freq_khz": None})
        st.update({"radio_nr": 2, "freq_khz": 7012.0, "mode": "CW", "active_radio": 1,
                   "entry_window_hwnd": 2, "station": "", "tx_freq_khz": None})
        f = types.SimpleNamespace(
            root=self.root, tree=tree, _header=frames["header"], _outer=frames["outer"],
            fkey_row=frames["fkeys"], _bottom=frames["bottom"], _tree_frame=tree_frame,
            _compact_bar=bar, _compact_btn=btn, _compact_info=info,
            compact_var=tk.BooleanVar(value=False), topmost_var=tk.BooleanVar(value=False),
            _normal_geom="", _compact_geom="", radio=1, state=st, displayed=[1, 2, 3],
            COMPACT_COLUMNS=PouncerApp.COMPACT_COLUMNS,
            COMPACT_GEOMETRY=PouncerApp.COMPACT_GEOMETRY)
        f._radio_khz = lambda nr: st.snapshot(nr)["freq_khz"]
        f.cwspeed = types.SimpleNamespace(wpm={1: 28})
        f._radio_nr = lambda radio=None: radio or f.radio
        for name in ("_apply_compact", "_apply_topmost", "_update_compact_bar", "_wpm"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        self.root.geometry("900x500+20+20")
        self.root.update()
        f.compact_var.set(True)
        f._apply_compact()
        self.root.update()
        self.assertTrue(self.root.geometry().startswith("330x430"))
        self.assertEqual(tuple(tree["displaycolumns"]), ("freq", "call", "mult"))
        self.assertFalse(frames["header"].winfo_ismapped())
        self.assertTrue(bar.winfo_ismapped())
        self.assertTrue(f.topmost_var.get())
        self.assertEqual(btn.cget("text"), "Radio A")
        self.assertIn("A 14025.0", info.cget("text"))
        self.assertIn("B 7012.0", info.cget("text"))
        self.assertIn("28 WPM", info.cget("text"))
        f.compact_var.set(False)
        f._apply_compact()
        self.root.update()
        self.assertTrue(self.root.geometry().startswith("900x500"))
        self.assertTrue(frames["header"].winfo_ismapped())
        self.assertFalse(bar.winfo_ismapped())
        self.assertFalse(f.topmost_var.get())
        self.assertEqual(tuple(tree["displaycolumns"]), ("#all",))


@unittest.skipIf(tk is None, "tkinter not available")
class TkWpmPopupTests(unittest.TestCase):
    """The CW speed popup: made once, shown over the window, then hidden."""

    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        # free the Tk objects now, in the main thread: collected later in
        # another test's thread (web server) they raise "main thread is not
        # in main loop" there and that request never gets an answer
        gc.collect()

    def test_show_and_hide(self) -> None:
        import types
        from unittest import mock
        from pouncer import app as appmod
        from pouncer.app import PouncerApp

        f = types.SimpleNamespace(root=self.root, cfg=types.SimpleNamespace(cw_speed=True))
        for name in ("_build_wpm_popup", "_show_wpm_popup", "_hide_wpm_popup"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        self.root.geometry("600x400+50+50")
        self.root.update()
        made = []
        with mock.patch.object(appmod.win32mod, "_WIN", True), \
                mock.patch.object(appmod.win32mod, "make_no_activate", side_effect=made.append):
            f._build_wpm_popup()
        self.assertIsNotNone(f._wpm_pop)
        self.assertEqual(len(made), 1)            # no-activate style set once
        f._show_wpm_popup("CW 30 WPM \u25b2")
        self.root.update()
        self.assertEqual(f._wpm_pop_label.cget("text"), "CW 30 WPM \u25b2")
        self.assertGreater(float(f._wpm_pop.attributes("-alpha")), 0.5)
        self.assertIsNotNone(f._wpm_pop_after)
        f._hide_wpm_popup()
        self.root.update()
        self.assertEqual(float(f._wpm_pop.attributes("-alpha")), 0.0)   # faded out
        self.assertIsNone(f._wpm_pop_after)
        # off: no popup window at all
        f.cfg.cw_speed = False
        f._build_wpm_popup()
        self.assertIsNone(f._wpm_pop)
