"""Real-Tk check of the Windows window layout (runs where tkinter works,
e.g. the Windows build; skipped elsewhere)."""

import gc
import sys
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

    def test_flow_survives_a_destroyed_child(self) -> None:
        """v0.7.188: the band chips are rebuilt on a layout change (clean <->
        detailed); the row measured the destroyed frame ("bad window path
        name") and stopped laying out - the new chips were never placed."""
        from pouncer.app import FlowFrame
        errors = []
        old = self.root.report_callback_exception
        self.root.report_callback_exception = lambda et, ev, tb: errors.append(ev)
        try:
            flow = FlowFrame(self.root)
            flow.pack(fill="x")
            flow.add(tk.Label(flow, text="Sort"))
            chips = flow.add(tk.Frame(flow, width=80, height=20))
            self.root.update()
            chips.destroy()
            flow.event_generate("<Configure>")
            new = flow.add(tk.Frame(flow, width=90, height=20))
            self.root.update()
            self.assertEqual(errors, [])
            self.assertEqual(new.winfo_manager(), "place")      # the new chips are placed
            self.assertEqual(len(flow._items), 2)
            flow.destroy()
            self.root.update()
            self.assertEqual(errors, [])
        finally:
            self.root.report_callback_exception = old

    def test_flow_hides_a_button_without_a_gap(self) -> None:
        from pouncer.app import FlowFrame
        flow = FlowFrame(self.root)
        flow.pack(fill="x")
        a, b, c = (flow.add(ttk.Button(flow, text=t)) for t in ("Time", "Radio A (A/B)", "SNR"))
        self._settle("900x200+10+10")
        x_c = c.winfo_x()
        flow.set_visible(b, False)              # one radio: no A / B switch
        self._settle("900x200+10+10")
        self.assertFalse(b.winfo_ismapped())
        self.assertLess(c.winfo_x(), x_c)       # SNR moved up into the gap
        flow.set_visible(b, True)
        self._settle("900x200+10+10")
        self.assertTrue(b.winfo_ismapped())

    def test_label_wraps_to_window_width(self) -> None:
        from pouncer.app import PouncerApp

        frame = ttk.Frame(self.root)
        frame.pack(fill="x")
        label = PouncerApp._wrap_label(frame, "word " * 80)
        self._settle("300x300+10+10")
        self.root.update()
        self.assertLessEqual(label.winfo_width(), 310)
        self.assertGreater(label.winfo_height(), 40)  # several lines
        # **...** = bold values; .cget("text") gives the plain text
        label.configure(text="RBN: **logged in**   spotter=**S53WW**")
        self.assertEqual(label.cget("text"), "RBN: logged in   spotter=S53WW")
        bold = label.tag_ranges("b")
        self.assertEqual([label.get(bold[i], bold[i + 1]) for i in range(0, len(bold), 2)],
                         ["logged in", "S53WW"])
        self.assertEqual(str(label.cget("state")), "disabled")   # read-only, copyable


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
        from pouncer.settings_window import SettingsWindow, page_items

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
        # every setting is on a page (none falls to "Other"), pages listed on the left
        pages = page_items(settingsmod.current(cfg))
        self.assertNotIn("Other", [p for p, _ in pages])
        self.assertEqual(sum(len(its) for _p, parts in pages for _q, its in parts),
                         len(settingsmod.SETTINGS))
        self.assertEqual(w.side.get(0, "end"), tuple("  " + p for p, _ in pages))
        self.assertEqual(len(w.rows), len(settingsmod.SETTINGS))
        # search keeps only the matching rows; Esc clears it, not the window
        w.search_var.set("snr")
        self.root.update()
        shown = [r["item"]["key"] for r in w.rows if r["frame"].winfo_manager()]
        self.assertEqual(sorted(shown), ["ui.min_snr", "ui.min_snr_keep_mults"])
        w.search_var.set("zzzz nothing")
        self.root.update()
        self.assertIn("No setting matches", w.empty.cget("text"))
        self.assertTrue(w.empty.winfo_manager())
        w._escape()
        self.assertEqual(w.search_var.get(), "")
        self.assertIsNotNone(w.win)
        self.root.update()
        self.assertEqual(len([r for r in w.rows if r["frame"].winfo_manager()]),
                         len(settingsmod.SETTINGS))
        # the page list jumps to its page
        w.side.selection_clear(0, "end")
        w.side.selection_set(len(pages) - 1)
        w._jump()
        self.root.update()
        self.assertGreater(w.canvas.yview()[0], 0)
        # open() at a page / part scrolls there and lights it in the list
        w.open("Cluster server")
        self.root.update()
        self.assertEqual(w.side.curselection(), (w.pages.index("Cluster server"),))
        y_cluster = w.canvas.yview()[0]
        w.open("Spots", "Spotted - who was heard")
        self.root.update()
        self.assertEqual(w.side.curselection(), (w.pages.index("Spots"),))
        self.assertNotEqual(w.canvas.yview()[0], y_cluster)
        # every closed help line fits the sheet as it is on screen (label + "more")
        w.win.geometry("880x700")
        self.root.update()
        w._rewrap()
        self.root.update()
        too_wide = [h["text"][:40] for h in w.helps if not h["open"]
                    and h["label"].master.winfo_reqwidth() > w._avail(h) + 2]
        self.assertEqual(too_wide, [])
        self.assertTrue(any(h["more"].winfo_manager() for h in w.helps))
        # long help: one line + "more"; "more" opens it all
        h = max(w.helps, key=lambda h: len(h["text"]))
        self.assertTrue(h["label"].cget("text").endswith("…"))
        self.assertEqual(h["more"].cget("text"), "more")
        w._toggle_help(h)
        self.assertEqual(h["label"].cget("text"), h["text"])
        self.assertEqual(h["more"].cget("text"), "less")
        # a change is only marked - nothing saved before Save
        w.vars["n1mm.qsy_wait_ms"].set("2000")
        w._change(settingsmod.BY_KEY["n1mm.focus_back_to_a"], "true")
        self.assertEqual(saved, [])
        self.assertIn("not saved yet", w.state["n1mm.qsy_wait_ms"].cget("text"))
        self.assertEqual(w.save_btn.cget("text"), "Save (2)")
        # Save writes them all
        self.assertTrue(w.save_all())
        self.assertIn(("n1mm.qsy_wait_ms", "2000"), saved)
        self.assertIn(("n1mm.focus_back_to_a", "true"), saved)
        self.assertEqual(w.state["n1mm.qsy_wait_ms"].cget("text"), "Saved - in use now")
        self.assertEqual(w.save_btn.cget("state"), "disabled")
        # a bad value is refused and stays pending
        w.vars["n1mm.qsy_wait_ms"].set("abc")
        self.assertFalse(w.save_all())
        self.assertEqual(w.state["n1mm.qsy_wait_ms"].cget("text"), "not a number")
        self.assertIn("n1mm.qsy_wait_ms", w.pending)
        # Discard puts the box back
        w.discard()
        self.assertEqual(w.vars["n1mm.qsy_wait_ms"].get(), "2000")
        self.assertEqual(w.pending, {})
        # a restart setting shows the Restart button after Save
        self.assertFalse(w.restart_btn.winfo_ismapped())
        w.vars["rbn.backup_host"].set("dxc.example.org")
        w.save_all()
        self.root.update()
        self.assertEqual(w.state["rbn.backup_host"].cget("text"), "Saved - used after a restart")
        self.assertTrue(w.restart_btn.winfo_ismapped())
        w.restart_btn.invoke()
        self.assertEqual(restarted, [1])
        # typing back the saved value: nothing pending
        n = len(saved)
        w.vars["rbn.backup_host"].set("dxc.example.org")
        w.save_all()
        self.assertEqual(len(saved), n)
        w.open()                                   # second open: same window
        self.assertEqual(len([c for c in self.root.winfo_children()
                              if c.winfo_class() == "Toplevel"]), 1)

    def test_menu_bar(self) -> None:
        import types
        from pouncer.app import PouncerApp

        opened = []
        f = types.SimpleNamespace(
            root=self.root, set_call_var=tk.BooleanVar(value=True),
            hide_worked_var=tk.BooleanVar(value=False),
            compact_var=tk.BooleanVar(value=False), topmost_var=tk.BooleanVar(value=False),
            tiles_var=tk.BooleanVar(value=True), detailed_var=tk.BooleanVar(value=False),
            updater=types.SimpleNamespace(exe="C:/x/POUNCer.exe"),
            _settings_win=types.SimpleNamespace(open=lambda *a: opened.append(a)),
            _console=types.SimpleNamespace(open=lambda: None),
            cfg=types.SimpleNamespace(config_path="C:/x/config.ini", alert_popup=True, rate_line=True,
                                      alert_sound=False, look="dark", team_key="",
                                      rbn_source="rbn,cluster", rbn_telnet_backup=False,
                                      rbn_hamqth_backup=True, filter_mode="calls",
                                      rbn_spotter="S53WW", filter_dxcc="", filter_zones="14, 15",
                                      filter_conts="EU", rbn_host="telnet.reversebeacon.net",
                                      rbn_port=7000, rbn_backup_host="azure.s53m.com",
                                      rbn_backup_port=8000, ssb_host="azure.s53m.com", ssb_port=8000,
                                      spotted_mode="ww", spotted_calls="", spotted_dxcc="JA",
                                      spotted_zones="", spotted_conts=""),
            _start_autostart=lambda: None, _stop_waiting_n1mm=lambda: None, _save_diagnostics=lambda: None, _open_path=lambda p: None,
            _autostart_folder=lambda: "",
        )
        for name in ("_open_config_folder", "_reconnect_log", "_restart_now", "_shutdown_and_quit",
                     "_on_toggle_worked", "_clear_spots", "_open_browser", "_on_update_click",
                     "_open_url", "_about", "_apply_compact", "_apply_topmost", "_set_alert", "_open_app_window", "_set_look", "_toggle_tiles", "_toggle_layout", "_show_phone_qr", "_allow_firewall",
                     "_team_password", "_become_master", "_toggle_team_master", "_switch_version", "_station_test", "_toggle_rate_line", "_update_team", "_rollback"):
            setattr(f, name, lambda *a, **k: None)
        f._team_pw_label = PouncerApp._team_pw_label.__get__(f)
        for name in ("_refresh_spot_menus", "_menu_source", "_menu_filter", "_show_connections",
                     "_menu_spotted", "_refresh_menu_states", "_key_hide_worked"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        f._menu_key = PouncerApp._menu_key
        f.team, f._n1mm_wait_rest = None, None
        f.SPOT_MENU, f.SOURCE_NAMES, f.CONN_KEYS = (PouncerApp.SPOT_MENU, PouncerApp.SOURCE_NAMES,
                                                    PouncerApp.CONN_KEYS)
        PouncerApp._build_menu(f)
        bar = f.menubar
        labels = [bar.entrycget(i, "label") for i in range(bar.index("end") + 1)]
        # the Mac: Settings is in the POUNCer (apple) menu, not on the bar (Cmd+,)
        mac = sys.platform == "darwin"
        self.assertEqual([x for x in labels if x], ["File", "N1MM", "View", "Spots", "Spotters",
                                                    "Spotted", "Tools", "Team"]
                         + ([] if mac else ["Settings"]) + ["Help"])

        def menu(name):
            return self.root.nametowidget(bar.entrycget(labels.index(name), "menu"))

        def items(m):
            return [m.entrycget(i, "label") for i in range(m.index("end") + 1)
                    if m.type(i) != "separator"]

        def sub(m, label):
            return self.root.nametowidget(m.entrycget(items_all(m).index(label), "menu"))

        def items_all(m):
            return [m.entrycget(i, "label") if m.type(i) != "separator" else "-"
                    for i in range(m.index("end") + 1)]
        # Settings: all of it, or straight to one page (the Mac: in the POUNCer menu)
        from pouncer import settings as settingsmod
        pages = [p for p, _ in settingsmod.PAGES]
        if not mac:
            setm = menu("Settings")
            self.assertEqual(items(setm), ["All settings\u2026"] + [p + "\u2026" for p in pages])
            setm.invoke(0)
            setm.invoke(items_all(setm).index("Spots\u2026"))
            self.assertEqual(opened, [(), ("Spots",)])
            opened.clear()
        f._refresh_spot_menus()
        spots = menu("Spots")
        slabels = items(spots)
        self.assertIn("RBN - telnet.reversebeacon.net:7000", slabels)
        # the list's own switches live with the spots now (after the relabelled sources)
        self.assertIn("Hide worked (same band + mode)", slabels)
        self.assertIn("Clear spots", slabels)
        self.assertTrue(f._src_vars["rbn"].get() and f._src_vars["cluster"].get())
        self.assertFalse(f._src_vars["telnet"].get())
        self.assertTrue(f._src_vars["hamqth"].get())
        spt = menu("Spotters")
        self.assertEqual(spt.entrycget(0, "label"), "Calls: S53WW")
        self.assertIn("empty", spt.entrycget(1, "label"))
        self.assertEqual(f._flt_var.get(), "calls")
        spd = menu("Spotted")
        self.assertEqual(spd.entrycget(1, "label"), "DXCC: JA")
        # "Edit the lists..." opens the Settings window at that part of Spots
        spt.invoke(items_all(spt).index("Edit the lists\u2026 (Settings > Spots)"))
        spd.invoke(items_all(spd).index("Edit the lists\u2026 (Settings > Spots)"))
        self.assertEqual(opened, [("Spots", "Spotters - who heard it"),
                                  ("Spots", "Spotted - who was heard")])
        self.assertEqual(f._spd_var.get(), "ww")
        self.assertEqual(items(menu("N1MM")), ["Reconnect log", "Also enter callsign in N1MM",
                                               "Station test (QSY + callsign, each radio)"])
        vlabels = items(menu("View"))
        for label in ("Compact window (beside N1MM)", "Always on top",
                      "Detailed layout (every status line, the old table)",
                      "Spot list as tiles (like the phone)",
                      "ZO mode (Urgent / Medium / Normal columns)",
                      "Rate / QSOs / mults per band line",
                      "Look (from the next start)"):
            self.assertIn(label, vlabels)
        tools = menu("Tools")
        tlabels = items(tools)
        for label in ("Contest summary", "CQ zones: who hears me", "Band opening timeline",
                      "Phone page", "Alerts", "Traffic console (raw data in / out)"):
            self.assertIn(label, tlabels)
        self.assertEqual(items(sub(tools, "Phone page")),
                         ["In the browser", "QR code\u2026 (scan with the phone)", "As an app window"])
        self.assertEqual(items(sub(tools, "Alerts")), ["Pop-up (history hint + new mult)", "Sound"])
        file = menu("File")
        flabels = items(file)
        self.assertIn("Autostart", flabels)
        self.assertEqual(items(sub(file, "Autostart")), [
            "Start autostart programs now", "Don't wait for N1MM (start the rest now)",
            "Open the autostart folder"])
        team = menu("Team")
        self.assertIn("Update all POUNCers in the team (from the master)", items(team))
        helpm = menu("Help")
        self.assertIn("Versions", items(helpm))
        self.assertIn("Troubleshooting", items(helpm))
        # nothing that can't work now is clickable: no team here, not waiting for N1MM
        f._refresh_menu_states()
        for i in range(team.index("end") + 1):
            if team.type(i) != "separator" and not team.entrycget(i, "label").startswith("Team password"):
                self.assertEqual(team.entrycget(i, "state"), "disabled", team.entrycget(i, "label"))
        auto = sub(file, "Autostart")
        self.assertEqual(auto.entrycget(1, "state"), "disabled")
        f._n1mm_wait_rest = []
        f._refresh_menu_states()
        self.assertEqual(auto.entrycget(1, "state"), "normal")
        self.assertEqual(f.look_var.get(), "dark")
        self.assertTrue(f.alert_popup_var.get())
        self.assertFalse(f.alert_sound_var.get())


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
            _run_fields=lambda s: {"run": 10, "hours": 20, "rare": True} if s.dxcall == "DL1AA" else {},
            scp=types.SimpleNamespace(busted=lambda c: c == "PHB9CBR"),
            _is_dx=lambda c: False, _continent=lambda c: "EU",
            _act_on_spot=lambda s: tapped.append(s.dxcall))
        f._tried = lambda s: (2, 3) if s.dxcall == "W2XYZ" else None
        for name in ("_rebuild", "_on_tree_key"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        f._row_key = PouncerApp._row_key            # a staticmethod
        f._rebuild()
        rows = {tree.item(i, "values")[4]: tuple(t for t in tree.item(i, "tags") if t != "stripe")
                for i in tree.get_children()}             # (zebra rows aside)
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
        f._show_tiles = lambda: None
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
