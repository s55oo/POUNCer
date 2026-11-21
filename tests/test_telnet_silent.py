"""A telnet connection that hangs - open, but nothing comes any more - is
dropped and reconnected ([rbn] telnet_silent_minutes), and every telnet
socket gets TCP keepalive (v0.7.241)."""
import queue
import socket
import threading
import time
import types
import unittest

from pouncer import rbn, settings
from pouncer.config import load_config


class SilentServer:
    """Accepts, sends a login prompt once, then says nothing (a hung node)."""

    def __init__(self) -> None:
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.srv.settimeout(0.2)
        self.port = self.srv.getsockname()[1]
        self.accepted = 0
        self.socks = []
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                s, _ = self.srv.accept()
            except OSError:
                continue
            self.accepted += 1
            self.socks.append(s)
            try:
                s.sendall(b"login: ")
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        for s in self.socks + [self.srv]:
            try:
                s.close()
            except OSError:
                pass


class SilentReconnectTests(unittest.TestCase):
    def client(self, port, silent_s):
        c = rbn.RBNClient("127.0.0.1", port, "S55OO", "", queue.Queue(), history=0,
                          kind="cluster", name="telnet")
        c._pause = lambda seconds: time.sleep(0.05)   # no 2 s backoff in a test
        c.rx_silent_s = silent_s
        return c

    def wait_for(self, cond, limit=10.0) -> bool:
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_hung_connection_is_dropped_and_reconnected(self):
        srv = SilentServer()
        c = self.client(srv.port, 1.5)
        try:
            c.start()
            self.assertTrue(self.wait_for(lambda: srv.accepted >= 2), "no reconnect")
        finally:
            c.stop() if hasattr(c, "stop") else c._stop.set()
            srv.close()

    def test_off_keeps_the_connection(self):
        srv = SilentServer()
        c = self.client(srv.port, 0)
        try:
            c.start()
            self.assertTrue(self.wait_for(lambda: srv.accepted >= 1))
            time.sleep(3.0)
            self.assertEqual(srv.accepted, 1)
        finally:
            c.stop() if hasattr(c, "stop") else c._stop.set()
            srv.close()


class KeepaliveTests(unittest.TestCase):
    def test_keepalive_is_on(self):
        a, b = socket.socketpair() if not hasattr(socket, "AF_UNIX") else (None, None)
        s = socket.socket()
        try:
            self.assertTrue(rbn.enable_keepalive(s))
            self.assertTrue(s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))
        finally:
            s.close()
            for x in (a, b):
                if x is not None:
                    x.close()

    def test_keepalive_never_raises(self):
        self.assertFalse(rbn.enable_keepalive(types.SimpleNamespace()))


class SettingTests(unittest.TestCase):
    def test_default_saved_and_applied_live(self):
        import os
        import tempfile
        self.assertEqual(load_config("").telnet_silent_minutes, 5)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            open(p, "w").close()
            cfg = load_config(p)
            self.assertTrue(settings.save(cfg, p, "rbn.telnet_silent_minutes", "0")["ok"])
            self.assertEqual(load_config(p).telnet_silent_minutes, 0)
        self.assertEqual(settings.place("rbn.telnet_silent_minutes")[0], "Spots")
        from pouncer.app import PouncerApp
        clients = {1: types.SimpleNamespace(rx_silent_s=0.0)}
        fake = types.SimpleNamespace(cfg=types.SimpleNamespace(telnet_silent_minutes=7),
                                     _rbn_clients={}, _telnet_clients=clients)
        PouncerApp._apply_rx_silent(fake)
        self.assertEqual(clients[1].rx_silent_s, 420.0)


if __name__ == "__main__":
    unittest.main()


class FtWindowTrustTests(unittest.TestCase):
    """A CW spot in an FT window with Q:n >= trust_q or GoCluster V is not held
    for PSK Reporter - that many skimmers decoded CW there (v0.7.241); N4OW
    7050.5 Q:6 (40 m FT4 window) was held, 2026-11-21 00:21Z."""

    def app(self, trust=3):
        from pouncer.app import PouncerApp
        asked = []
        telnets = {3: types.SimpleNamespace(name="telnet3", collector_role="cluster"),
                   4: types.SimpleNamespace(name="telnet4", collector_role="gocluster")}
        fake = types.SimpleNamespace(
            cfg=types.SimpleNamespace(cluster_trust_q=trust), _telnet_clients=telnets,
            pskr=types.SimpleNamespace(digital=lambda c, f: "", ask=lambda c, f: asked.append(c)),
            _overlap_keys=set())
        return fake, asked, PouncerApp._check_overlap

    def spot(self, call, raw, feed, q=0):
        from pouncer.rbn import Spot
        return Spot(dxcall=call, freq_khz=7050.5, mode="CW", snr=20, wpm=25, spot_type="CQ",
                    spotter="NU4F-#", time_utc="0021Z", received=time.time(), quality=q,
                    raw_line=raw, feed_id=feed)

    def test_q6_from_dxspider_is_not_held(self):
        fake, asked, check = self.app()
        s = self.spot("N4OW", "DX de NU4F-#: 7050.5 N4OW CW 20dB Q:6 Z:3,4,5 0021Z", "telnet3", 6)
        check(fake, s)
        self.assertEqual(asked, [])
        self.assertNotIn(s.key, fake._overlap_keys)

    def test_low_q_or_off_is_still_held(self):
        for trust, q in ((3, 2), (0, 9)):
            fake, asked, check = self.app(trust)
            s = self.spot("N4OW", "DX de NU4F-#: 7050.5 N4OW CW 20dB Q:%d 0021Z" % q, "telnet3", q)
            check(fake, s)
            self.assertEqual(asked, ["N4OW"], (trust, q))

    def test_gocluster_v_is_not_held_p_is(self):
        for conf, held in (("V", False), ("P", True)):
            fake, asked, check = self.app()
            raw = "DX de NU4F-#:    7050.50  N4OW        CW 20 dB 25 WPM        - EM73 %s 0021Z" % conf
            check(fake, self.spot("N4OW", raw, "telnet4"))
            self.assertEqual(bool(asked), held, conf)

    def test_q_from_a_raw_rbn_feed_is_not_trusted(self):
        fake, asked, check = self.app()
        check(fake, self.spot("N4OW", "DX de NU4F-#: 7050.5 N4OW CW 20dB Q:9 0021Z", "rbn:7000", 9))
        self.assertEqual(asked, ["N4OW"])
