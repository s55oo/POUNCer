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
        f = types.SimpleNamespace(cfg=types.SimpleNamespace(rbn_callsign="S55OO"),
                                  own_call=lambda: "S55OO")
        login = P._self_feed_login(f)
        self.assertRegex(login, r"^S55OO-[2-9]$")
        self.assertEqual(login, P._self_feed_login(f))        # the same on this PC
        off = types.SimpleNamespace(cfg=types.SimpleNamespace(self_spot_alert=True, self_spot_host=""),
                                    own_call=lambda: "S55OO", _self_feed=None)
        P._ensure_self_feed(off)                              # empty host = off
        self.assertIsNone(off._self_feed)


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
