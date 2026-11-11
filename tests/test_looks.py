"""Window looks (dark / light Windows 11 style, classic), DPI scale, tiles."""

import gc
import unittest

try:
    import tkinter as tk
    from pouncer import theme
    from pouncer.tiles import TileList
except ImportError:  # pragma: no cover
    tk = theme = TileList = None

from pouncer import winstate


class GeometryTests(unittest.TestCase):
    def test_scale_saved_geometry(self) -> None:
        self.assertEqual(winstate.scale_geometry("940x520+10+20", 1.5), "1410x780+10+20")
        self.assertEqual(winstate.scale_geometry("940x520+10+20", 1.02), "940x520+10+20")


@unittest.skipIf(tk is None, "tkinter not available")
class LookTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")

    def tearDown(self) -> None:
        self.root.destroy()
        # leave the module as other tests expect it: classic, 100 %
        theme.SCALE, theme.LOOK, theme.MODERN = 1.0, "classic", False
        theme._set_palette("classic")
        gc.collect()

    def test_dark_light_classic(self) -> None:
        from tkinter import ttk
        theme.apply(self.root, "dark")
        self.assertEqual(ttk.Style(self.root).theme_use(), "sun-valley-dark")
        self.assertTrue(theme.MODERN)
        self.assertEqual(theme.BG, "#1c1c1c")
        self.assertGreaterEqual(theme.SCALE, 1.0)
        theme.apply(self.root, "light")
        self.assertEqual(ttk.Style(self.root).theme_use(), "sun-valley-light")
        theme.apply(self.root, "classic")
        self.assertEqual(ttk.Style(self.root).theme_use(), "clam")
        self.assertFalse(theme.MODERN)
        self.assertEqual(theme.BG, "#eef1f5")
        theme.apply(self.root, "nonsense")             # unknown: dark
        self.assertEqual(theme.LOOK, "dark")
        theme.apply(self.root, "classic")

    def test_key_button_colours(self) -> None:
        theme.apply(self.root, "dark")
        b = theme.key_button(self.root, "ENTER", "log", lambda: None)
        self.assertEqual(b.cget("bg"), theme.KEY_COLORS["log"])
        self.assertEqual(theme.key_button(self.root, "SPOTit", "spot", lambda: None).cget("bg"),
                         theme.KEY_COLORS["spot"])
        theme.apply(self.root, "classic")

    def test_tiles_reuse_items_and_draw_only_the_visible(self) -> None:
        """v0.7.182: like the row list - Tk never frees a deleted canvas item."""
        theme.apply(self.root, "dark")
        taps = []
        t = TileList(self.root, on_tap=taps.append)
        t.pack(fill="both", expand=True)
        self.root.geometry("700x300")
        self.root.update()

        def many(n, tag=""):
            return [{"call": f"DL{i}{tag}", "line1": "14025.0", "line2": "1200Z", "mult": i % 3,
                     "tried": "2x" if i % 4 == 0 else ""} for i in range(n)]
        t.set_items(many(1500))
        self.root.update()
        last_id = max(t.c.find_all())
        for r in range(20):
            t.set_items(many(1500 - r * 9, tag=str(r)))
            self.root.update()
        self.assertEqual(max(t.c.find_all()), last_id)     # nothing new created
        shown = [t.c.itemcget(i, "text") for i in t.c.find_all()
                 if t.c.type(i) == "text" and t.c.itemcget(i, "state") != "hidden"]
        self.assertIn("DL019", shown)
        self.assertLess(len([x for x in shown if x.startswith("DL")]), 100)
        t.c.yview_moveto(0.5)                              # scroll: tiles there are drawn
        self.root.update()
        (x0, y0, x1, y1), j = next(b for b in t._boxes if b[0][1] >= t.c.canvasy(0))
        self.assertIn(f"DL{j}19", [t.c.itemcget(i, "text") for i in t.c.find_all()
                                   if t.c.type(i) == "text"])
        t._click(type("E", (), {"x": int(x0 + 5), "y": int(y0 - t.c.canvasy(0) + 5)})())
        self.assertEqual(taps, [j])

    def test_tiles_click_keys_and_marks(self) -> None:
        theme.apply(self.root, "dark")
        tapped = []
        t = TileList(self.root, on_tap=tapped.append)
        t.pack(fill="both", expand=True)
        self.root.geometry("700x300")
        self.root.update()
        items = [{"call": "3B8X", "line1": "14025.1", "line2": "1200Z 22dB", "strong": True,
                  "mult": 2, "dx": True, "rare": True, "onfreq": True},
                 {"call": "DL1AA", "line1": "14030.0", "line2": "1200Z 12dB", "stale": True},
                 {"call": "K3LR", "line1": "14012.5", "line2": "1200Z 28dB", "worked": True}]
        t.set_items(items)
        self.root.update()
        w = t.c.winfo_width()                           # as many ~330 px columns as fit
        self.assertEqual(t._cols(), max(1, (w - 6) // (theme.px(330) + 6)))
        texts = [t.c.itemcget(i, "text") for i in t.c.find_all() if t.c.type(i) == "text"]
        self.assertIn("3B8X", texts)
        self.assertIn("⚡", texts)
        (x0, y0, x1, y1), _ = t._boxes[1]
        t._click(type("E", (), {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2})())
        self.assertEqual(tapped, [1])
        t.move(1)                                       # next: K3LR
        self.assertEqual(t.selected, 2)
        t._tap_selected()
        self.assertEqual(tapped, [1, 2])
        theme.apply(self.root, "classic")


if __name__ == "__main__":
    unittest.main()
