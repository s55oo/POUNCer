"""Traffic tap + console: silent while closed, lines shown / filtered when open."""

import unittest

from pouncer import traffic

try:
    import tkinter as tk
    from pouncer import console
    _root = tk.Tk()
    _root.withdraw()
except Exception:          # no display
    _root = None


class TapTests(unittest.TestCase):
    def tearDown(self) -> None:
        traffic.stop()

    def test_closed_keeps_nothing(self) -> None:
        traffic.stop()
        traffic.emit("rbn", "DX de X")
        self.assertFalse(traffic.active())
        self.assertEqual(traffic.drain(), [])

    def test_open_collects_and_drains(self) -> None:
        traffic.start(maxlen=3)
        for i in range(5):
            traffic.emit("rbn", f"line {i}")
        traffic.emit("N1MM >", b"<xml/>")
        got = traffic.drain()
        self.assertEqual([t for _, _, t in got], ["line 3", "line 4", "<xml/>"])
        self.assertEqual(traffic.drain(), [])

    def test_squeeze(self) -> None:
        self.assertEqual(traffic.squeeze("<a>\n  <b>1</b>\n</a>"), "<a> <b>1</b> </a>")
        self.assertTrue(traffic.squeeze("x" * 500, 10).endswith(" ..."))

    def test_groups(self) -> None:
        self.assertEqual(traffic.group_of("ssb"), "SSB")
        self.assertEqual(traffic.group_of("ssb >"), "SSB")
        self.assertEqual(traffic.group_of("rbn"), "RBN")
        self.assertEqual(traffic.group_of("N1MM 127.0.0.1"), "N1MM")
        self.assertEqual(traffic.group_of("TEAM >"), "TEAM")
        self.assertEqual(traffic.group_of("WEB 10.0.0.2"), "WEB")
        self.assertEqual(traffic.group_of("HTTP"), "HTTP")


@unittest.skipIf(_root is None, "no display")
class ConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = console.TrafficConsole(_root, notes=lambda: {"RBN": "paused - every radio on SSB"})
        self.c.open()

    def test_untick_retick_says_so_and_shows_again(self) -> None:
        traffic.emit("rbn", "first X1")
        self.c.add(traffic.drain())
        self.c.show["RBN"].set(False)
        self.c._switched("RBN")
        traffic.emit("rbn", "while hidden X2")
        self.c.add(traffic.drain())
        self.c.show["RBN"].set(True)
        self.c._switched("RBN")
        traffic.emit("rbn", "again X3")
        self.c.add(traffic.drain())
        body = self.body()
        self.assertIn("X1", body)
        self.assertNotIn("X2", body)
        self.assertIn("X3", body)
        self.assertIn("RBN hidden", body)
        self.assertIn("RBN shown again (paused - every radio on SSB)", body)
        self.assertEqual(self.c.counts["RBN"], 3)

    def test_a_failing_line_does_not_stop_it(self) -> None:
        calls = []
        real = self.c.add

        def boom(lines):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("bad line")
            real(lines)
        self.c.add = boom
        self.c._tick()                                  # fails, but is scheduled again
        self.assertIsNotNone(self.c._after)
        self.assertIn("console error: bad line", self.body())
        traffic.stop()                                  # tap stopped behind its back
        self.c._tick()
        self.assertTrue(traffic.active())

    def test_summary_tells_quiet_and_why(self) -> None:
        self.c.opened = 1000.0
        self.c.last = {"N1MM": 1098.0, "SSB": 1090.0}
        self.c.counts.update({"RBN": 0, "N1MM": 12, "SSB": 3})
        text = self.c.summary(now=1100.0)
        self.assertIn("RBN 0 (quiet 100s, paused - every radio on SSB)", text)
        self.assertIn("N1MM 12   ", text)
        self.assertIn("SSB 3 (quiet 10s)", text)
        self.assertIn("WEB 0 (hidden, quiet 100s)", text)
        st = {g: v[0] for g, v in self.c.states(now=1100.0).items()}
        self.assertEqual((st["N1MM"], st["SSB"], st["RBN"], st["TEAM"]),
                         ("live", "quiet", "why", "dead"))
        self.assertIn("quiet 3m", self.c.summary(now=1200.0))

    def test_new_lines_below_bar(self) -> None:
        _root.update()
        self.c.add([(0.0, "rbn", f"line {i}") for i in range(200)])
        _root.update()
        self.c.text.yview_moveto(0.0)                    # scrolled up by hand
        self.c.add([(0.0, "rbn", "newer")])
        self.assertTrue(self.c.below.winfo_manager())
        self.assertIn("1 new line below", self.c.below.cget("text"))
        self.c._follow()
        self.assertFalse(self.c.below.winfo_manager())

    def tearDown(self) -> None:
        self.c.close()

    def body(self) -> str:
        return self.c.text.get("1.0", "end")

    def test_open_starts_close_stops(self) -> None:
        self.assertTrue(traffic.active())
        self.c.close()
        self.assertFalse(traffic.active())
        self.assertFalse(self.c.is_open())

    def test_shows_filters_and_hides_groups(self) -> None:
        traffic.emit("rbn", "DX de OH8X-#: 14025.0 S55OO CW 20 dB")
        traffic.emit("N1MM 127.0.0.1", "<RadioInfo>")
        traffic.emit("WEB 10.0.0.2", "/api/spots")       # WEB off by default
        self.c.add(traffic.drain())
        self.assertIn("S55OO", self.body())
        self.assertIn("RadioInfo", self.body())
        self.assertNotIn("/api/spots", self.body())
        self.c.filter.set("radioinfo")
        traffic.emit("rbn", "DX de K1TTT-#: 7010.0 W1AW CW")
        traffic.emit("N1MM 127.0.0.1", "<RadioInfo> again")
        self.c.add(traffic.drain())
        self.assertNotIn("W1AW", self.body())
        self.assertIn("again", self.body())
        self.assertEqual(self.c.counts["RBN"], 2)

    def test_pause_and_keep(self) -> None:
        self.c._toggle_pause()
        traffic.emit("rbn", "hidden while paused")
        self.c.add(traffic.drain())
        self.assertNotIn("hidden", self.body())
        self.c._toggle_pause()
        old = console.KEEP
        console.KEEP = 50
        try:
            self.c.add([(0.0, "rbn", f"n{i}") for i in range(200)])
        finally:
            console.KEEP = old
        lines = self.body().strip().splitlines()
        self.assertLessEqual(len(lines), 50)
        self.assertTrue(lines[-1].endswith("n199"))


if __name__ == "__main__":
    unittest.main()
