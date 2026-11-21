"""People's spots (spotter without '-#') from a cluster Telnet: all of them to
port 10000, in every mode, past the collector ([cluster] people_all, v0.7.245)."""
import calendar
import types
import unittest

from pouncer.collector import Collector
from pouncer.rbn import parse_cluster_spot

T = calendar.timegm((2026, 11, 21, 12, 0, 0))


def app(**cfg):
    base = dict(follow_modes=("CW",), hide_beacons=True, hide_warc=False, cluster_merge=True,
                cluster_people_all=True, telnet1_role="cluster", telnet2_role="cluster",
                telnet4_role="gocluster")
    base.update(cfg)
    return types.SimpleNamespace(cfg=types.SimpleNamespace(**base), spots={})


def spot(line, feed="telnet"):
    s = parse_cluster_spot(line)
    s.feed_id = s.source = feed
    s.received = s.arrived = T
    return s


SSB = "DX de S51DX:     14205.0  JA1ABC       59 tnx QSO                     1200Z"
FT8 = "DX de W7O:       14074.4  JH8ESQ       FT8 -12                        1200Z"
CW = "DX de S51DX:     14025.0  K1ABC        cq                             1200Z"


class PeopleAllTests(unittest.TestCase):
    def collector(self):
        return Collector({'trust_quality': 2}, clock=lambda: T)

    def test_every_mode_goes_out(self):
        a, c = app(), self.collector()
        for line in (SSB, FT8, CW):
            out = c.process(a, spot(line))
            self.assertEqual(len(out), 1, line)
            self.assertFalse(out[0].collector_selected)     # mirrors merged downstream

    def test_a_repeat_by_another_person_goes_out_too(self):
        a, c = app(), self.collector()
        self.assertTrue(c.process(a, spot(CW)))
        again = CW.replace("S51DX", "S52AB").replace("1200Z", "1201Z")
        self.assertTrue(c.process(a, spot(again)))          # no 180 s repeat window

    def test_off_people_go_through_the_collector(self):
        a, c = app(cluster_people_all=False), self.collector()
        self.assertEqual(c.process(a, spot(FT8)), [])        # FT8 not in the Spots modes
        self.assertTrue(c.process(a, spot(CW)))
        again = CW.replace("S51DX", "S52AB")
        self.assertEqual(c.process(a, spot(again)), [])      # a repeat

    def test_skimmers_are_unchanged(self):
        a, c = app(), self.collector()
        sk = "DX de DL8LAS-#: 14074.0 JH8ESQ FT8 -12 dB 1200Z"
        self.assertEqual(c.process(a, spot(sk)), [])
        self.assertFalse(Collector.person(a.cfg, spot(sk)))

    def test_gocluster_person_counts_rbn_feed_does_not(self):
        cfg = app().cfg
        self.assertTrue(Collector.person(cfg, spot(SSB, "telnet4")))
        self.assertFalse(Collector.person(cfg, spot(SSB, "rbn")))
        h = spot(SSB)
        h.history = True
        self.assertFalse(Collector.person(cfg, h))           # sh/dx history: as before

    def test_engine_still_learns_from_it(self):
        a, c = app(), self.collector()
        c.process(a, spot(CW))
        self.assertTrue(c.engine.latest.get(('20M', 'UNKNOWN', 'K1ABC')) or
                        c.engine.latest.get(('20M', 'CW', 'K1ABC')))


class ServeTests(unittest.TestCase):
    def test_ft8_person_spot_is_served(self):
        from pouncer.app import PouncerApp
        sent = []
        dxc = types.SimpleNamespace(relay=lambda: True, port=10000,
                                    publish=lambda s: sent.append(s) or True)
        f = types.SimpleNamespace(cfg=app().cfg, _dxc=dxc, _overlap_keys=set(), _unpub=None)
        f._digi_wanted = lambda: set()
        s = spot(FT8)
        s.mode = "FT8"
        PouncerApp._serve(f, s)
        self.assertEqual(sent, [s])
        sk = spot("DX de DL8LAS-#: 14074.0 JH8ESQ FT8 -12 dB 1200Z")
        sk.mode = "FT8"
        PouncerApp._serve(f, sk)
        self.assertEqual(sent, [s])                          # a skimmer's FT8: still out


if __name__ == "__main__":
    unittest.main()
