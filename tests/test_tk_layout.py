"""Real-Tk check of the Windows window layout (runs where tkinter works,
e.g. the Windows build; skipped elsewhere)."""

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
