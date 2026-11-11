"""Real Tk text bounds for compact ZO cards (also runnable with system Python)."""
import unittest

try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipIf(tk is None, 'tkinter not available')
class ZOTextBoundsTests(unittest.TestCase):
    def test_wrapped_marks_stay_inside_cards_and_clear_details(self):
        from pouncer.tiles import TileList
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'no Tk display: {exc}')
        root.withdraw()
        self.addCleanup(root.destroy)
        for width in (210, 240, 320, 600, 960):
            with self.subTest(width=width):
                tiles = TileList(root, on_tap=lambda i: None)
                try:
                    tiles.zo_mode = True
                    # Use actual canvas font measurement without relying on a
                    # window manager's minimum size or a mapped display.
                    tiles.c.winfo_width = lambda: width
                    tiles.c.winfo_height = lambda: 500
                    tiles.set_items([dict(call='VP2V/W1ABC/P', urgency='urgent',
                        line1='7020.0 R24% ~3h', line2='1200Z 26dB cq3m',
                        rare=True, busted=True, own=True, tried='↻123m×12')])
                    x0, y0, x1, y1 = tiles._boxes[0][0]
                    slot = tiles._pool[0]
                    marks = [tiles.c.bbox(i) for i in slot['marks']]
                    for box in marks:
                        self.assertGreaterEqual(box[0], x0)
                        self.assertLessEqual(box[2], x1)
                        self.assertGreaterEqual(box[1], y0)
                        self.assertLessEqual(box[3], y1)
                    for i, a in enumerate(marks):
                        for b in marks[i+1:]:
                            self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or
                                            a[3] <= b[1] or b[3] <= a[1])
                    self.assertLessEqual(tiles.c.bbox(slot['call'])[3], min(b[1] for b in marks))
                    self.assertLessEqual(max(b[3] for b in marks), tiles.c.bbox(slot['l1'])[1])
                    self.assertLessEqual(tiles.c.bbox(slot['l2'])[3], y1)
                    # Returning to the original layout clears wrapping/anchors.
                    tiles.zo_mode = False
                    tiles.draw(force=True)
                    for item in slot['marks']:
                        self.assertEqual(tiles.c.itemcget(item, 'anchor'), 'w')
                        self.assertEqual(float(tiles.c.itemcget(item, 'width')), 0)
                finally:
                    tiles.destroy()


if __name__ == '__main__':
    unittest.main()
