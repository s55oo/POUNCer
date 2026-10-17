"""Window alert (rare CQ + new mult: pop-up / sound), compact band chips, and
the CQ zones / timeline windows."""

import gc
import time
import types
import unittest

from pouncer.alerts import AlertWatch

try:
    import tkinter as tk
    from tkinter import ttk
    from pouncer import panels
except ImportError:  # pragma: no cover - Python without Tk
    tk = panels = None


class AlertWatchTests(unittest.TestCase):
    def test_once_per_station_and_band_after_warmup(self) -> None:
        now = [0.0]
        w = AlertWatch(warmup_s=30, clock=lambda: now[0])
        self.assertEqual(w.fresh([(("K1AA", "20m"), "a")]), [])      # start: known
        now[0] = 31
        self.assertEqual(w.fresh([(("K1AA", "20m"), "a")]), [])      # still the same one
        self.assertEqual(w.fresh([(("K1AA", "40m"), "b"), (("JA1X", "20m"), "c")]), ["b", "c"])
        self.assertEqual(w.fresh([(("JA1X", "20m"), "c")]), [])


@unittest.skipIf(panels is None, "tkinter not available")
class PanelHelperTests(unittest.TestCase):
    def test_colours(self) -> None:
        self.assertEqual(panels.hsl(0, 100, 50), "#ff0000")
        self.assertEqual(panels.timeline_color(0, 0, 0, 10), panels.EMPTY)
        self.assertNotEqual(panels.zone_color("hears_me", 30), panels.zone_color("i_hear", 30))

    def test_hour_boxes(self) -> None:
        t0 = 1_700_000_000 // 3600 * 3600              # a full hour
        slots = [t0 - 600 + i * 600 for i in range(8)]  # 23:50, 00:00 ... 01:00
        d = {"slots": slots, "slot_s": 600, "bands": ["20m"],
             "cells": {"20m": [[1, 1, 0]] * 8}}
        self.assertEqual(len(panels.group_boxes(d, 10)), 8)
        hb = panels.group_boxes(d, 60)
        self.assertEqual([b["cells"]["20m"][0] for b in hb], [1, 6, 1])
        self.assertEqual(hb[1]["len"], 3600)


def _zones_data(minutes):
    return {"own": "S50XX", "my_zone": 15, "minutes": minutes, "bands": ["20m", "40m"],
            "team": "off",
            "hears_me": {"20m": {5: {"snr": 18, "n": 2, "calls": 1, "who": ["W3SKM"], "age": 2}}},
            "i_hear": {"40m": {"25": {"snr": 9, "n": 1, "calls": 1, "who": ["JA1AA"], "age": 0}}}}


def _timeline_data(hours):
    now = time.time()
    last = int(now // 600 * 600)
    n = hours * 6
    slots = [last - (n - 1 - i) * 600 for i in range(n)]
    return {"slots": slots, "slot_s": 600, "bands": ["20m", "40m"], "now": now, "team": "off",
            "cells": {"20m": [[3, 2, 1]] * n, "40m": [[0, 0, 0]] * n}}


@unittest.skipIf(tk is None, "tkinter not available")
class TkPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        gc.collect()

    def test_zones_window(self) -> None:
        asked = []
        z = panels.ZonesWindow(self.root, lambda m: asked.append(m) or _zones_data(m))
        z.open()
        self.root.update()
        self.assertEqual(asked, [15])                       # last 15 min by default
        self.assertEqual(len(z._hits), 2)                   # two zones heard
        (x0, y0, x1, y1), _ = z._hits[0]
        z._on_click(types.SimpleNamespace(x=(x0 + x1) / 2, y=(y0 + y1) / 2))
        self.assertIn("CQ zone 5", z.info.cget("text"))
        self.assertIn("W3SKM", z.info.cget("text"))
        z._pick(60)
        self.assertEqual(asked[-1], 60)
        z.open()                                            # again: same window
        self.assertEqual(len([c for c in self.root.winfo_children()
                              if c.winfo_class() == "Toplevel"]), 1)
        z.close()
        self.assertFalse(z.is_open())

    def test_timeline_window(self) -> None:
        t = panels.TimelineWindow(self.root, _timeline_data)
        t.open()
        self.root.update()
        self.assertEqual(len(t._hits), 2 * 72)              # 12 h of 10-min boxes, 2 bands
        t._pick_g(60)
        self.root.update()
        self.assertLessEqual(len(t._hits), 2 * 13)
        _, (band, b) = t._hits[0]
        self.assertIn("UTC", t.describe((band, b)))
        t._pick_h(6)
        self.root.update()
        self.assertLessEqual(len(t._hits), 2 * 7)
        t.close()

    def test_provider_error_shown(self) -> None:
        def bad(_m):
            raise RuntimeError("no zones")
        z = panels.ZonesWindow(self.root, bad)
        z.open()
        self.root.update()
        texts = [z.canvas.itemcget(i, "text") for i in z.canvas.find_all()]
        self.assertIn("no zones", texts)
        z.close()


@unittest.skipIf(tk is None, "tkinter not available")
class TkAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        gc.collect()

    def _fake(self, popup=True, sound=False):
        from pouncer.app import PouncerApp
        tapped = []
        f = types.SimpleNamespace(root=self.root, cfg=types.SimpleNamespace(
            alert_popup=popup, alert_sound=sound), _act_on_spot=lambda s: tapped.append(s.dxcall))
        f._alerts = AlertWatch(warmup_s=0)
        f._row_key = PouncerApp._row_key
        for name in ("_build_alert_popup", "_check_alerts", "_show_alert", "_hide_alert",
                     "_on_alert_click"):
            setattr(f, name, getattr(PouncerApp, name).__get__(f))
        f.ALERT_MS = PouncerApp.ALERT_MS
        self.root.geometry("600x400+50+50")
        self.root.update()
        f._build_alert_popup()
        return f, tapped

    def test_popup_then_click_qsys(self) -> None:
        from pouncer.rbn import Spot
        f, tapped = self._fake()
        s = Spot(dxcall="3B8X", freq_khz=7012.0, mode="CW", snr=12, wpm=28, spot_type="CQ",
                 spotter="S53WW-#", time_utc="1200Z", received=0)
        f._check_alerts([s])
        self.root.update()
        self.assertTrue(f._alert_shown)
        self.assertGreater(f._alert_pop.winfo_rootx(), -100)     # moved onto the window
        self.assertIn("3B8X", f._alert_label.cget("text"))
        f._check_alerts([s])                       # same station: no second alert
        f._on_alert_click()
        self.assertEqual(tapped, ["3B8X"])
        self.assertFalse(f._alert_shown)

    def test_popup_off(self) -> None:
        from pouncer.rbn import Spot
        f, _ = self._fake(popup=False)
        s = Spot(dxcall="3B8X", freq_khz=7012.0, mode="CW", snr=12, wpm=28, spot_type="CQ",
                 spotter="S53WW-#", time_utc="1200Z", received=0)
        f._check_alerts([s])
        self.root.update()
        self.assertFalse(f._alert_shown)


@unittest.skipIf(tk is None, "tkinter not available")
class TkRebuildAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        gc.collect()

    def test_only_rare_new_mults_not_worked(self) -> None:
        from pouncer.app import PouncerApp
        from pouncer.rbn import Spot

        def spot(call, khz):
            return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=10, wpm=28, spot_type="CQ",
                        spotter="S53WW-#", time_utc="1200Z", received=0)
        spots = [spot("3B8X", 14025.0), spot("DL1AA", 14030.0), spot("K1AA", 14040.0),
                 spot("VP8Z", 14050.0)]
        cols = ("time", "freq", "snr", "wpm", "call", "cont", "mult", "mode", "type", "spotter")
        tree = ttk.Treeview(self.root, columns=cols, show="headings")
        got = []
        f = types.SimpleNamespace(
            tree=tree, displayed=[], _row_spots=[], _alerts=object(),
            _sorted_visible=lambda: list(spots), _on_freq_spot=lambda items: None,
            worked=types.SimpleNamespace(is_worked=lambda c, k, m: c == "VP8Z"),
            _mults=lambda s: 0 if s.dxcall == "DL1AA" else 1,
            _run_fields=lambda s: {"run": 5, "hours": 2} if s.dxcall != "K1AA" else {},
            scp=None, _is_dx=lambda c: True, _continent=lambda c: "AF", _tried=lambda s: None,
            _check_alerts=lambda items: got.extend(s.dxcall for s in items))
        f._row_key = PouncerApp._row_key
        PouncerApp._rebuild(f)
        # 3B8X: rare + mult; DL1AA no mult; K1AA not rare; VP8Z already worked
        self.assertEqual(got, ["3B8X"])


@unittest.skipIf(tk is None, "tkinter not available")
class TkCompactChipsTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        gc.collect()

    def test_chips_and_toggle(self) -> None:
        from pouncer.app import PouncerApp
        chips = {b: tk.Label(self.root) for b in ("40m", "20m")}
        f = types.SimpleNamespace(_compact_chips=chips, compact_var=tk.BooleanVar(value=True),
                                  _view_band="40m")
        f._band_chip_color = PouncerApp._band_chip_color
        f._update_compact_bands = PouncerApp._update_compact_bands.__get__(f)
        f._update_compact_bands({"40m": {"state": "open", "dx": 5, "calls": 8, "spots": 9,
                                         "mults": 3}}, "20m")
        self.assertEqual(chips["40m"].cget("text"), "\u25b640\u00b73")   # shown, 3 mults
        self.assertEqual(chips["20m"].cget("text"), "20")
        self.assertEqual(int(chips["20m"].cget("bd")), 1)               # the radio's band
        applied = []
        f._apply_compact = lambda: applied.append(f.compact_var.get())
        self.assertEqual(PouncerApp._toggle_compact(f), "break")
        self.assertEqual(applied, [False])


if __name__ == "__main__":
    unittest.main()
