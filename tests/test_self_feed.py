"""Spots of my call (SPOTTED): from any source, passed on by the team master.
(v0.7.214: no connection of its own any more.)"""

import queue
import time
import types
import unittest



class TeamSelfTests(unittest.TestCase):
    def test_master_passes_it_on_slave_shows_it(self) -> None:
        import threading as th
        from pouncer.team import Team
        got = []
        t = Team.__new__(Team)
        t._lock = th.Lock()
        t.peers = {}
        t.id, t.started = "me", 5.0
        t._on_self = got.append
        t._on_spot = t._on_runhist = None
        t.handle({"t": "hi", "id": "m", "started": 1}, "10.0.0.2")          # m = master
        t.handle({"t": "self", "id": "m", "d": {"call": "S55OO", "spotter": "DL1ABC",
                                                 "khz": 3678.0}}, "10.0.0.2")
        t.handle({"t": "self", "id": "x", "d": {"call": "S55OO"}}, "10.0.0.3")  # not the master
        self.assertEqual(got, [{"call": "S55OO", "spotter": "DL1ABC", "khz": 3678.0}])


if __name__ == "__main__":
    unittest.main()


class OwnCallTests(unittest.TestCase):
    def test_mycall_from_radioinfo_first(self) -> None:
        from pouncer.n1mm import RadioState, parse_radio_info
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        info = parse_radio_info(b"<RadioInfo><RadioNr>1</RadioNr><Freq>707200</Freq>"
                                b"<Mode>LSB</Mode><mycall>tk0c</mycall></RadioInfo>")
        self.assertEqual(info["mycall"], "TK0C")
        st = RadioState()
        st.update(info)
        f = types.SimpleNamespace(state=st, worked=types.SimpleNamespace(station_call="S55OO"),
                                  cfg=types.SimpleNamespace(rbn_callsign="S55OO"))
        self.assertEqual(P.own_call(f), "TK0C")              # N1MM's station call now
        f.state = RadioState()                               # no radio info yet: the log's
        self.assertEqual(P.own_call(f), "S55OO")


class MasterListTests(unittest.TestCase):
    def test_my_call_in_the_list_feed_pops_up_and_is_no_row(self) -> None:
        from pouncer.rbn import Spot
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        sent, noted = [], []
        f = types.SimpleNamespace(
            spot_queue=queue.Queue(), _team_queue=queue.Queue(), spots={}, _dirty=False,
            _perf={"spots_in": 0}, _last_own_spot=0.0, own_call=lambda: "S55OO",
            team=types.SimpleNamespace(is_master=lambda: True, send_spot=sent.append),
            _note_self_spot=lambda *a, **k: noted.append(a))
        for n in ("_drain_spots", "_is_my_call"):
            setattr(f, n, getattr(P, n).__get__(f))
        f._note_own = f._check_overlap = f._track_on_freq = f._serve = lambda s: None
        f._beacon_out = lambda s: False
        f._merged_dup = lambda s: False
        f._relabel_overlap = lambda: None
        f._spot_dict = lambda s: {"dxcall": s.dxcall}
        # the last spots after a cluster login: received = the spot's own time
        f.spot_queue.put(Spot("S55OO", 14234.0, "SSB", None, None, "", "DL1ABC", "0932Z",
                              time.time() - 240))
        f.spot_queue.put(Spot("K1ABC", 14250.0, "SSB", None, None, "", "DL1ABC", "0932Z",
                              time.time()))
        f._drain_spots()
        self.assertEqual(noted, [("S55OO", "DL1ABC", 14234.0)])
        self.assertEqual([s.dxcall for s in f.spots.values()], ["K1ABC"])   # no row of myself
        self.assertEqual(sent, [{"dxcall": "K1ABC"}])                         # not passed on as a spot


class BandTests(unittest.TestCase):
    def _fake(self, radio_khz, only=True):
        from pouncer.n1mm import RadioState
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        st = RadioState()
        for nr, khz in enumerate(radio_khz, 1):
            st.update({"radio_nr": nr, "freq_khz": khz, "mode": "USB"})
        f = types.SimpleNamespace(state=st, cfg=types.SimpleNamespace(self_spot_band_only=only))
        for n in ("_my_bands", "_on_my_band"):
            setattr(f, n, getattr(P, n).__get__(f))
        return f

    def test_only_on_the_radios_band(self) -> None:
        f = self._fake([7072.0])
        self.assertFalse(f._on_my_band(14345.0))
        self.assertTrue(f._on_my_band(7150.0))
        f2 = self._fake([7072.0, 14250.0])                 # SO2R: either radio
        self.assertTrue(f2._on_my_band(14345.0))
        self.assertTrue(self._fake([])._on_my_band(14345.0))          # no radio info: show
        self.assertTrue(self._fake([7072.0], only=False)._on_my_band(14345.0))
