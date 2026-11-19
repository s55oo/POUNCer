"""Allow my own spots (Settings > Spots): off by default, a spot of your
own call is kept off port 10000 even though it still triggers the SPOTTED
pop-up (v0.7.233)."""

import os
import tempfile
import types
import unittest

from pouncer import settings
from pouncer.config import load_config


class SettingTests(unittest.TestCase):
    def test_off_by_default_and_saved(self) -> None:
        self.assertFalse(load_config("").serve_own_spots)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[rbn]\nhide_beacons = true\n")
            cfg = load_config(p)
            self.assertTrue(settings.save(cfg, p, "rbn.serve_own_spots", "true")["ok"])
            self.assertTrue(cfg.serve_own_spots)
            self.assertTrue(load_config(p).serve_own_spots)
        self.assertEqual(settings.place("rbn.serve_own_spots")[0], "Spots")


class ConsoleTests(unittest.TestCase):
    def test_excl_self_unless_allowed(self) -> None:
        from tests.test_console_shown import _app
        line = "DX de DL1ABC-#:   14025.0  S55OO          CW    24 dB  28 WPM  CQ      1802Z"
        f = _app(zones=(), own_call="S55OO")
        f.spotter_filter.configure("ww")
        self.assertFalse(f._console_shown("rbn", line))
        self.assertEqual(f._console_shown("rbn", line), "EXCL SELF")
        f.cfg.serve_own_spots = True
        self.assertTrue(f._console_shown("rbn", line))
        # someone else's call, same skimmer: never EXCL SELF
        other = line.replace("S55OO", "K1ABC")
        f.cfg.serve_own_spots = False
        self.assertTrue(f._console_shown("rbn", other))

    def test_no_own_call_set_never_excludes(self) -> None:
        from tests.test_console_shown import _app
        line = "DX de DL1ABC-#:   14025.0  S55OO          CW    24 dB  28 WPM  CQ      1802Z"
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        self.assertTrue(f._console_shown("rbn", line))   # no own call known: not "self"


class DrainSpotsTests(unittest.TestCase):
    def _fixture(self, serve_own_spots: bool):
        import queue
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        sent, served, noted = [], [], []
        f = types.SimpleNamespace(
            spot_queue=queue.Queue(), _team_queue=queue.Queue(), spots={}, _dirty=False,
            _perf={"spots_in": 0}, _last_own_spot=0.0, own_call=lambda: "S55OO",
            cfg=types.SimpleNamespace(serve_own_spots=serve_own_spots),
            team=types.SimpleNamespace(is_master=lambda: True, send_spot=sent.append),
            _note_self_spot=lambda *a, **k: noted.append(a))
        for n in ("_drain_spots", "_is_my_call", "_is_my_callsign"):
            setattr(f, n, getattr(P, n).__get__(f))
        f._note_own = f._check_overlap = f._track_on_freq = lambda s: None
        f._serve = served.append
        f._beacon_out = lambda s: False
        f._merged_dup = lambda s: False
        f._relabel_overlap = lambda: None
        f._spot_dict = lambda s: {"dxcall": s.dxcall}
        return f, sent, served, noted

    def test_off_by_default_keeps_it_off_port_10000(self) -> None:
        from pouncer.rbn import Spot
        f, sent, served, noted = self._fixture(serve_own_spots=False)
        f.spot_queue.put(Spot("S55OO", 14234.0, "CW", None, None, "", "DL1ABC", "0932Z",
                              __import__("time").time()))
        f._drain_spots()
        self.assertEqual(noted, [("S55OO", "DL1ABC", 14234.0)])   # still the SPOTTED pop-up
        self.assertEqual(served, [])                               # but not forwarded
        self.assertEqual(f.spots, {})

    def test_allowed_reaches_serve_and_the_team(self) -> None:
        from pouncer.rbn import Spot
        f, sent, served, noted = self._fixture(serve_own_spots=True)
        f.spot_queue.put(Spot("S55OO", 14234.0, "CW", None, None, "", "DL1ABC", "0932Z",
                              __import__("time").time()))
        f._drain_spots()
        self.assertEqual(noted, [("S55OO", "DL1ABC", 14234.0)])    # the pop-up either way
        self.assertEqual([s.dxcall for s in served], ["S55OO"])    # now forwarded too
        self.assertEqual(sent, [{"dxcall": "S55OO"}])               # and sent to the team


if __name__ == "__main__":
    unittest.main()
