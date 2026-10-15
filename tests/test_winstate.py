"""Remembering the main window (pouncer/winstate.py)."""

import os
import tempfile
import unittest

from pouncer import winstate as ws


class WinStateTests(unittest.TestCase):
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache", "window.json")
            self.assertEqual(ws.load(path), {})                 # first start
            self.assertTrue(ws.save(path, {"geometry": "940x520+10+20", "sort": "freq"}))
            self.assertEqual(ws.load(path)["sort"], "freq")
            with open(path, "w") as fh:
                fh.write("{broken")
            self.assertEqual(ws.load(path), {})                 # broken file: defaults

    def test_fit_geometry(self) -> None:
        self.assertEqual(ws.fit_geometry("940x520+100+80", 1920, 1080), "940x520+100+80")
        # was on a second monitor that is gone: back onto this screen
        self.assertEqual(ws.fit_geometry("940x520+3000+80", 1920, 1080), "940x520+1820+80")
        # bigger than the screen, above it
        self.assertEqual(ws.fit_geometry("3000x2000+0-50", 1920, 1080), "1920x1080+0+0")
        # too small
        self.assertEqual(ws.fit_geometry("100x50+0+0", 1920, 1080), "420x300+0+0")
        self.assertIsNone(ws.fit_geometry("rubbish", 1920, 1080))
        self.assertIsNone(ws.fit_geometry("", 1920, 1080))

    def test_columns_and_sort(self) -> None:
        cols = ("time", "freq", "call")
        self.assertEqual(ws.column_widths({"time": 80, "call": 5000, "x": 9, "freq": "?"}, cols),
                         {"time": 80, "call": 1200})
        self.assertEqual(ws.column_widths(None, cols), {})
        self.assertEqual(ws.sort_choice({"sort": "freq", "reverse": True}, cols, ("time", False)),
                         ("freq", True))
        self.assertEqual(ws.sort_choice({"sort": "gone"}, cols, ("time", False)), ("time", False))


if __name__ == "__main__":
    unittest.main()
