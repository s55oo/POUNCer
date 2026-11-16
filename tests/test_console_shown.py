"""Traffic console: the raw lines of the spots POUNCer shows are yellow."""

import time
import types
import unittest

from pouncer import traffic
from pouncer.rbn import parse_spot

LINE = "DX de EA8BFK-#:   14025.0  DL1ABC         CW    24 dB  28 WPM  CQ      1802Z"
OTHER_SKIMMER = "DX de W3LPL-#:    14025.0  DL1ABC         CW    18 dB  28 WPM  CQ      1802Z"


def _app(zones=(14, 15), spotted=None):
    """A stand-in app with the real Spotters filter: skimmers in the zones."""
    from tests.test_so2r import _import_app
    from pouncer.rbn import SpotterFilter
    P = _import_app().PouncerApp
    zone = {"EA8BFK": 33, "DL1ABC": 14, "W3LPL": 5, "F5ABC": 14, "S50ABC": 15}
    sf = SpotterFilter("", zone_of=lambda c: zone.get(c), continent_of=lambda c: "EU",
                       home_cont=lambda: "EU")
    sf.configure("zones", zones=zones)
    f = types.SimpleNamespace(spotter_filter=sf, spotted_filter=spotted,
                              cfg=types.SimpleNamespace(hide_beacons=True))
    for name in ("_console_spot", "_console_shown"):
        setattr(f, name, getattr(P, name).__get__(f))
    return f


IN_ZONE = "DX de DL1ABC-#:   14025.0  K1ABC          CW    24 dB  28 WPM  CQ      1802Z"
OUT_ZONE = "DX de W3LPL-#:    14025.0  K1ABC          CW    18 dB  28 WPM  CQ      1802Z"


class ShownTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.f = _app()
        except Exception as exc:  # noqa: BLE001 - the app needs tkinter
            self.skipTest(f"app not importable here: {exc}")

    def test_spotter_zones_decide(self) -> None:
        f = self.f
        self.assertIsNone(f._console_shown("telnet", "Hello S55OO, this is a node"))
        self.assertTrue(f._console_shown("telnet", IN_ZONE))          # skimmer in zone 14
        self.assertFalse(f._console_shown("rbn", OUT_ZONE))           # zone 5: filtered out
        self.assertTrue(f._console_shown("telnet", "S55OO de NODE >" + IN_ZONE))
        # every source: HamQTH, the HTTP feed (JSON), N1MM's spot packets
        self.assertTrue(f._console_shown("HAMQTH", "DL1ABC-# 14025.0 K1ABC CW 24"))
        self.assertFalse(f._console_shown("HAMQTH", "W3LPL-# 14025.0 K1ABC CW 18"))
        self.assertTrue(f._console_shown(
            "N1MM 127.0.0.1", "<spot><dxcall>K1ABC</dxcall><frequency>1420500</frequency>"
            "<spottercall>F5ABC</spottercall><action>add</action></spot>"))

    def test_spotted_filter_and_beacons(self) -> None:
        f = _app(spotted=types.SimpleNamespace(accept=lambda call: call.startswith("K1")))
        self.assertTrue(f._console_shown("telnet", IN_ZONE))
        other = IN_ZONE.replace("K1ABC", "G3XYZ")
        self.assertFalse(f._console_shown("telnet", other))          # not a Spotted station
        beacon = "DX de DL1ABC-#:   14100.0  4U1UN          CW    24 dB  22 WPM  NCDXF B 1802Z"
        self.assertFalse(_app()._console_shown("telnet", beacon))

    def test_all_pass_without_filters(self) -> None:
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        self.assertTrue(f._console_shown("rbn", OUT_ZONE))


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
