"""A person spotted MY call on a cluster: pop-up once (skimmers don't count)."""

import unittest

from pouncer.alerts import SelfSpotWatch, base_call, is_skimmer


class SelfSpotTests(unittest.TestCase):
    def test_person_not_skimmer_once(self) -> None:
        t = [0.0]
        w = SelfSpotWatch(repeat_s=900, clock=lambda: t[0])
        self.assertFalse(w.check("S55OO", "S53WW-#", 14025.0, "S55OO"))    # skimmer
        self.assertFalse(w.check("S55OO", "DK8NE-@", 14074.0, "S55OO"))    # digital skimmer
        self.assertFalse(w.check("DL1AA", "DL1ABC", 14025.0, "S55OO"))     # not me
        self.assertTrue(w.check("S55OO", "DL1ABC", 14025.0, "S55OO"))
        self.assertFalse(w.check("S55OO", "dl1abc", 14025.3, "S55OO"))     # same spot again
        self.assertTrue(w.check("S55OO", "OK1XYZ", 14025.0, "S55OO"))      # another spotter
        self.assertTrue(w.check("TK0C", "F5ABC-2", 7012.0, "TK0C"))        # cluster node user
        self.assertTrue(w.check("S55OO", "S55OO", 21025.0, "S55OO"))       # my self-spot: shown
        self.assertTrue(SelfSpotWatch.is_self("S55OO-2", "S55OO"))
        self.assertFalse(SelfSpotWatch.is_self("DL1ABC", "S55OO"))
        t[0] = 1000
        self.assertTrue(w.check("S55OO", "DL1ABC", 14025.0, "S55OO"))      # 15 min later

    def test_portable_and_prefixed_calls(self) -> None:
        self.assertEqual(base_call("EA8/S55OO"), "S55OO")
        self.assertEqual(base_call("S55OO/P"), "S55OO")
        w = SelfSpotWatch()
        self.assertTrue(w.check("S55OO/P", "DL1ABC", 3525.0, "S55OO"))
        self.assertFalse(w.check("S55OO", "DL1ABC", 3525.0, ""))           # no call known
        self.assertTrue(is_skimmer("S53WW-#"))
        self.assertFalse(is_skimmer("S50ABC-2"))


class WatchedCallTests(unittest.TestCase):
    def test_every_spot_of_a_watched_call(self) -> None:
        w = SelfSpotWatch()
        self.assertTrue(w.check("S9R", "OH8X-#", 14025.0, "S55OO", watched=("S9R",)))  # skimmer too
        self.assertTrue(w.check("S9R", "DL1ABC", 14195.0, "S55OO", watched=("s9r",)))
        self.assertFalse(w.check("S9R", "OH8X-#", 14025.2, "S55OO", watched=("S9R",)))  # again
        self.assertFalse(w.check("S9R", "DL1ABC", 7040.0, "S55OO"))                       # not watched
        from pouncer import settings
        v, text = settings.parse(settings.BY_KEY["ui.watch_calls"], "s9r; 3B8X")
        self.assertEqual((v, text), (("S9R", "3B8X"), "S9R, 3B8X"))


class TeamSlaveTests(unittest.TestCase):
    def test_spot_from_the_master_brings_the_popup(self) -> None:
        import queue
        import types
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        f = types.SimpleNamespace(_self_spots=SelfSpotWatch(), _self_spot_q=queue.Queue(),
                                  _team_queue=queue.Queue(), _last_team_spot=0.0,
                                  cfg=types.SimpleNamespace(self_spot_alert=True),
                                  own_call=lambda: "S55OO")
        f._note_self_spot = P._note_self_spot.__get__(f)
        P._team_spot(f, {"dxcall": "S55OO", "freq_khz": 7197.0, "mode": "SSB", "snr": None,
                         "wpm": None, "spot_type": "", "spotter": "DL1ABC", "time_utc": "0758Z"})
        call, spotter, khz, _c, _t = f._self_spot_q.get_nowait()
        self.assertEqual((call, spotter, khz), ("S55OO", "DL1ABC", 7197.0))


if __name__ == "__main__":
    unittest.main()
