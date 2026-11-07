"""Clean layout: the readable row list and the one problem sentence."""

import unittest

from pouncer import checks
from tests.test_so2r import _import_app

_import_app()                       # loads pouncer.app (and rowlist) with a stub tkinter if needed
from pouncer import rowlist  # noqa: E402


class ColumnTests(unittest.TestCase):
    def test_mode_type_only_when_they_say_something(self) -> None:
        cw = [{"mode": "CW", "type": "CQ"}, {"mode": "CW", "type": "CQ"}]
        keys = [c[0] for c in rowlist.visible_columns(cw)]
        self.assertNotIn("mode", keys)
        self.assertNotIn("type", keys)
        self.assertEqual(keys[:2], ["freq", "call"])
        mixed = cw + [{"mode": "RTTY", "type": "CQ"}]
        self.assertIn("mode", [c[0] for c in rowlist.visible_columns(mixed)])
        ssb = [{"mode": "SSB", "type": ""}]
        self.assertIn("mode", [c[0] for c in rowlist.visible_columns(ssb)])
        beacon = cw + [{"mode": "CW", "type": "BEACON"}]
        self.assertIn("type", [c[0] for c in rowlist.visible_columns(beacon)])

    def test_layout_fills_the_width_call_not_half(self) -> None:
        cols = rowlist.visible_columns([{"mode": "CW", "type": "CQ"}])
        xs = rowlist.layout(cols, 1200)
        self.assertEqual(xs[0][1], 0)
        self.assertAlmostEqual(xs[-1][2], 1200, delta=2)
        call = next(x1 - x0 for k, x0, x1 in xs if k == "call")
        self.assertLess(call, 600)                 # never half the width
        self.assertGreater(call, 150)


class ProblemTests(unittest.TestCase):
    def test_first_bad_then_warn(self) -> None:
        items = [{"name": "N1MM", "state": "ok", "text": "14025.0"},
                 {"name": "cty", "state": "warn", "text": "75 days old"},
                 {"name": "Log", "state": "bad", "text": "N1MM Logger.ini not found"},
                 {"name": "SCP", "state": "off", "text": ""}]
        self.assertEqual(checks.problem(items), ("Log: N1MM Logger.ini not found  (+1 more)", "bad"))
        self.assertEqual(checks.problem(items[:2]), ("cty: 75 days old", "warn"))
        self.assertEqual(checks.problem(items[:1]), ("", "ok"))


class AppTextTests(unittest.TestCase):
    def test_age_and_chip(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        P = app.PouncerApp
        self.assertEqual(P._age_text(20), "now")
        self.assertEqual(P._age_text(185), "3 min")
        self.assertEqual(P._age_text(3900), "1 h 05")
        self.assertEqual(P._band_chip_short("20m", {"mults": 7}), "20 \u00b7 7M")
        self.assertEqual(P._band_chip_short("160m", {}), "160")


if __name__ == "__main__":
    unittest.main()


class RowListTkTests(unittest.TestCase):
    """The widget on a real Tk window (skipped without Tk / a display)."""

    def setUp(self) -> None:
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"no Tk: {exc}")
        self.root.geometry("900x400")

    def tearDown(self) -> None:
        self.root.destroy()

    def _items(self):
        return [{"freq": "14025.0", "call": "K1ABC", "snr": "30", "wpm": "28", "mult": 2,
                 "run": "R12%", "rare": True, "age": "now", "spotter": "S53WW", "mode": "CW",
                 "type": "CQ", "dx": True, "onfreq": True},
                {"freq": "14031.2", "call": "DL1XYZ", "snr": "12", "wpm": "24", "mult": 0,
                 "run": "R90%", "age": "3 min", "spotter": "OH6BG", "mode": "CW", "type": "CQ",
                 "worked": True}]

    def test_draw_tap_sort_keys(self) -> None:
        taps, sorts = [], []
        w = rowlist.RowList(self.root, on_tap=taps.append, on_sort=sorts.append)
        w.pack(fill="both", expand=True)
        self.root.update()
        w.set_items(self._items())
        self.root.update()
        texts = [w.c.itemcget(i, "text") for i in w.c.find_all() if w.c.type(i) == "text"]
        self.assertIn("K1ABC", texts)
        self.assertIn("MM", texts)                 # the double-mult tag
        self.assertIn("R12% ⚡", texts)        # rare runner
        self.assertNotIn("CQ", texts)              # Type column left out (all CQ)
        # click the second row = tap it
        from pouncer import theme
        rh = theme.px(rowlist.ROW_H)
        w._click(type("E", (), {"x": 50, "y": int(rh * 1.5)})())
        self.assertEqual(taps, [1])
        # heading click sorts by that column
        x_call = next(x0 + 5 for k, x0, x1 in w._xs if k == "call")
        w._head_click(type("E", (), {"x": x_call, "y": 5})())
        self.assertEqual(sorts, ["call"])
        # arrows move the selection, Space taps it
        w.move(-1)
        self.assertEqual(w.selected, 0)
        w._tap_selected()
        self.assertEqual(taps, [1, 0])
        # hover over Runs of row 0: the explanation; elsewhere: gone
        w.items[0]["run_tip"] = "R12% - test"
        x_run = next(x0 + 5 for k, x0, x1 in w._xs if k == "run")
        ev = type("E", (), {"x": x_run, "y": 5, "x_root": 100, "y_root": 100})()
        w._hover(ev)
        self.assertIsNotNone(w._tip)
        ev.x = 5                                    # the kHz column
        w._hover(ev)
        self.assertIsNone(w._tip)


class RunTipTests(unittest.TestCase):
    def test_text(self) -> None:
        P = _import_app().PouncerApp
        t = P.run_explain("DL1ABC", {"run": 26, "hours": 18.4, "rec": False, "run_band": "20m"}, True)
        self.assertIn("26 % of the QSOs on 20m were made running", t)
        self.assertIn("about 18 hours", t)
        self.assertIn("Rare runner", t)
        t2 = P.run_explain("K1ABC", {"run": 88, "hours": 30, "rec": True}, False)
        self.assertIn("reconstructed log", t2)
        self.assertIn("regular runner", t2)
        self.assertEqual(P.run_explain("X", {}, False), "")


class NoWpmSortTests(unittest.TestCase):
    def test_wpm_is_not_sortable(self) -> None:
        wpm = next(c for c in rowlist.COLUMNS if c[0] == "wpm")
        self.assertIsNone(wpm[4])                      # heading click does nothing
        import types
        P = _import_app().PouncerApp
        calls = []
        f = types.SimpleNamespace(UNSORTED=P.UNSORTED, _set_sort=calls.append)
        P._on_sort(f, "wpm")
        P._on_sort(f, "snr")
        self.assertEqual(calls, ["snr"])
        from pouncer.config import load_config
        import os
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "c.ini")
        with open(p, "w") as fh:
            fh.write("[ui]\nsort = wpm\n")
        self.assertEqual(load_config(p).sort, "snr")   # an old 'sort = wpm' -> SNR
