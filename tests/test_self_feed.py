"""Spots-of-my-call connection: own SSID, node filter, pop-up from its lines."""

import queue
import socket
import threading
import time
import types
import unittest

from pouncer.alerts import SelfSpotWatch
from pouncer.rbn import RBNClient


class FakeNode:
    """Login prompt; after the filter command one spot of S55OO."""

    def __init__(self) -> None:
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = []
        threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        c, _ = self.srv.accept()
        c.sendall(b"Welcome\r\nlogin: ")
        buf = b""
        while True:
            try:
                chunk = c.recv(1024)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                self.got.append(line.decode())
                if line.startswith(b"accept/spots"):
                    c.sendall(b"DX de DL1ABC:     3678.0  S55OO        test        0823Z\r\n")


class FeedTests(unittest.TestCase):
    def test_login_filter_and_popup(self) -> None:
        node = FakeNode()
        hits = []
        w = SelfSpotWatch()

        class All:
            primary = ""

            @staticmethod
            def accept(_):
                return True
        feed = RBNClient("127.0.0.1", node.port, "S55OO-5", All(), queue.Queue(), history=0,
                         backup_commands=["accept/spots call S55OO"], kind="cluster",
                         name="self-spot",
                         on_raw=lambda s: w.check(s.dxcall, s.spotter, s.freq_khz, "S55OO")
                         and hits.append((s.dxcall, s.spotter, s.freq_khz)))
        feed.backup_commands = ("accept/spots call S55OO",)
        feed.start()
        for _ in range(100):
            if hits:
                break
            time.sleep(0.05)
        feed.stop()
        self.assertEqual(node.got[:2], ["S55OO-5", "accept/spots call S55OO"])
        self.assertEqual(hits, [("S55OO", "DL1ABC", 3678.0)])

    def test_login_ssid_and_when(self) -> None:
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        f = types.SimpleNamespace(cfg=types.SimpleNamespace(rbn_callsign="S55OO", self_spot_ssid="77"),
                                  own_call=lambda: "S55OO")
        self.assertEqual(P._self_feed_login(f), "S55OO-77")
        slave = types.SimpleNamespace(cfg=types.SimpleNamespace(self_spot_alert=True, self_spot_host="h"),
                                      own_call=lambda: "S55OO", _self_feed=None,
                                      team=types.SimpleNamespace(role=lambda: "slave"))
        P._ensure_self_feed(slave)                            # a slave: the master logs in
        self.assertIsNone(slave._self_feed)
        off = types.SimpleNamespace(cfg=types.SimpleNamespace(self_spot_alert=True, self_spot_host=""),
                                    own_call=lambda: "S55OO", _self_feed=None)
        P._ensure_self_feed(off)                              # empty host = off
        self.assertIsNone(off._self_feed)


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
        f._note_own = f._check_overlap = f._track_on_freq = lambda s: None
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
