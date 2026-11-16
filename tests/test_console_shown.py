"""Traffic console: the raw lines of the spots POUNCer shows are yellow."""

import time
import types
import unittest

from pouncer import traffic
from pouncer.rbn import parse_spot

LINE = "DX de EA8BFK-#:   14025.0  DL1ABC         CW    24 dB  28 WPM  CQ      1802Z"
OTHER_SKIMMER = "DX de W3LPL-#:    14025.0  DL1ABC         CW    18 dB  28 WPM  CQ      1802Z"


def _app():
    from tests.test_so2r import _import_app
    P = _import_app().PouncerApp
    visible = []
    f = types.SimpleNamespace(radio=1, _last_weak=7)
    f._visible_spots = lambda radio=None, band=None: list(visible)
    for name in ("_note_accepted", "_console_shown"):
        setattr(f, name, getattr(P, name).__get__(f))
    f._accepted_key = P._accepted_key
    f.ACCEPTED_KEEP_S = P.ACCEPTED_KEEP_S
    return f, visible


class ShownTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.f, self.visible = _app()
        except Exception as exc:  # noqa: BLE001 - the app needs tkinter
            self.skipTest(f"app not importable here: {exc}")
        traffic.start()

    def tearDown(self) -> None:
        traffic.stop()

    def test_only_accepted_and_listed_spots(self) -> None:
        f = self.f
        self.assertIsNone(f._console_shown("telnet", "Hello S55OO, this is a node"))
        self.assertFalse(f._console_shown("telnet", LINE))           # not taken in (yet)
        spot = parse_spot(LINE)
        f._note_accepted(spot)
        self.assertFalse(f._console_shown("telnet", LINE))           # taken in, not listed
        self.visible.append(spot)
        f._shown_cache = None
        self.assertTrue(f._console_shown("telnet", LINE))
        # the same station from a skimmer outside the zones: never taken in
        self.assertFalse(f._console_shown("rbn", OTHER_SKIMMER))
        # a prompt in front of the line is fine
        self.assertTrue(f._console_shown("telnet", "S55OO de NODE >" + LINE))
        self.assertEqual(f._last_weak, 7)                            # the phone's count kept

    def test_nothing_noted_while_the_console_is_closed(self) -> None:
        traffic.stop()
        self.f._note_accepted(parse_spot(LINE))
        self.assertFalse(getattr(self.f, "_accepted", {}))


try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
except Exception:  # noqa: BLE001
    _root = None


@unittest.skipIf(_root is None, "no display")
class ConsoleYellowTests(unittest.TestCase):
    def setUp(self) -> None:
        from pouncer import console
        self.yes = set()
        self.c = console.TrafficConsole(
            _root, shown=lambda src, txt: (None if "DX de" not in txt else txt in self.yes))
        self.c.open()

    def tearDown(self) -> None:
        self.c.close()

    def tagged(self) -> str:
        r = self.c.text.tag_ranges("shown")
        return "".join(self.c.text.get(r[i], r[i + 1]) for i in range(0, len(r), 2))

    def test_yellow_now_or_a_moment_later(self) -> None:
        self.yes.add(LINE)
        self.c.add([(time.time(), "telnet", LINE), (time.time(), "telnet", "login: S55OO"),
                    (time.time(), "rbn", OTHER_SKIMMER)])
        self.assertIn("DL1ABC", self.tagged())
        self.assertNotIn("W3LPL", self.tagged())
        self.assertNotIn("login", self.tagged())
        self.assertEqual(len(self.c._pending), 1)                    # W3LPL: looked at again
        self.yes.add(OTHER_SKIMMER)
        self.c.recheck()
        self.assertIn("W3LPL", self.tagged())
        self.assertEqual(self.c._pending, [])

    def test_given_up_after_the_wait(self) -> None:
        self.c.add([(time.time(), "rbn", OTHER_SKIMMER)])
        self.c.recheck(now=time.time() + 60)
        self.assertEqual(self.c._pending, [])
        self.assertEqual(self.tagged(), "")


if __name__ == "__main__":
    unittest.main()
