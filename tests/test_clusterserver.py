"""POUNCer's DX cluster server: login, DX de lines, no N1MM loop, echoes."""

import socket
import time
import unittest

from pouncer.clusterserver import ClusterServer, dx_line, strip_telnet
from pouncer.rbn import Spot


def spot(call="DL1ABC", khz=14025.0, source="", spotter="S53WW-#"):
    return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=23, wpm=28, spot_type="CQ",
                spotter=spotter, time_utc="1200Z", received=time.time(), source=source)


class Client:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.buf = b""

    def until(self, text, timeout=3.0):
        end = time.time() + timeout
        while text.encode() not in self.buf and time.time() < end:
            try:
                self.buf += self.s.recv(4096)
            except socket.timeout:
                break
        return self.buf.decode("ascii", "replace")

    def send(self, line):
        self.s.sendall(line.encode() + b"\r\n")


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = ClusterServer(port=0, version="0.7.141")
        self.assertTrue(self.srv.start())
        self.port = self.srv._sock.getsockname()[1]

    def tearDown(self) -> None:
        self.srv.stop()

    def test_login_spots_shdx_bye(self) -> None:
        c = Client(self.port)
        self.assertIn("login:", c.until("login:"))
        c.send("S55OO")
        self.assertIn("S55OO de POUNCER >", c.until("de POUNCER >"))
        for _ in range(50):
            if self.srv.client_count():
                break
            time.sleep(0.02)
        self.assertTrue(self.srv.publish(spot()))
        out = c.until("1200Z")
        self.assertIn("DX de S53WW-#:", out)
        self.assertIn("14025.0  DL1ABC", out)
        self.assertIn("CW 23 dB 28 WPM CQ", out)
        # the same spot again within a minute: not resent
        self.assertFalse(self.srv.publish(spot()))
        c.buf = b""
        c.send("sh/dx")
        self.assertIn("DL1ABC", c.until("de POUNCER >"))
        c.buf = b""
        c.send("set/skimmer")                       # anything else: just the prompt
        self.assertIn("de POUNCER >", c.until("de POUNCER >"))
        c.send("bye")
        self.assertIn("73", c.until("73"))
        c.s.close()

    def test_never_n1mms_own_spots(self) -> None:
        self.assertFalse(self.srv.publish(spot(source="n1mm")))
        self.assertFalse(self.srv.echo("DL1ABC", 14025.0))

    def test_echo_of_our_spot(self) -> None:
        self.srv.publish(spot(call="K1ABC", khz=14031.2))
        self.assertTrue(self.srv.echo("k1abc", 14031.2))
        self.assertTrue(self.srv.echo("K1ABC", 14031.4))      # N1MM may round
        self.assertFalse(self.srv.echo("K1ABC", 14035.0))
        self.assertFalse(self.srv.echo("W1XYZ", 14031.2))

    def test_busy_port(self) -> None:
        other = ClusterServer(port=self.port)
        self.assertFalse(other.start())
        self.assertIn("busy", other.error)


class FormatTests(unittest.TestCase):
    def test_dx_line(self) -> None:
        line = dx_line("S53WW-#", 7008.6, "DL5ARM", "CW 21 dB 28 WPM CQ", "0857Z")
        self.assertTrue(line.startswith("DX de S53WW-#:"))
        self.assertIn("7008.6  DL5ARM", line)
        self.assertTrue(line.endswith("0857Z"))

    def test_strip_telnet(self) -> None:
        self.assertEqual(strip_telnet(b"\xff\xfb\x01S55OO\xff\xf1\r\n"), b"S55OO\r\n")


class AppEchoTests(unittest.TestCase):
    def test_own_spot_back_from_n1mm_is_ignored(self) -> None:
        import types
        from tests.test_so2r import _import_app
        app = _import_app()
        srv = ClusterServer(port=0)
        srv._sock = object()                       # 'running' for publish
        srv.publish(spot(call="EA6RJU", khz=14032.0))
        f = types.SimpleNamespace(_dxc=srv)
        # returns before touching anything else (would raise on the bare fake)
        app.PouncerApp._on_n1mm_spot(f, {"call": "EA6RJU", "freq_khz": 14032.0, "action": "add"})



class FakeCluster:
    """The real cluster (azure) in a test: asks for the call, logs what it
    gets, sends what the test tells it to."""

    def __init__(self):
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(2)
        self.port = self.ls.getsockname()[1]
        self.conn = None
        self.got = b""
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.conn, _ = self.ls.accept()
            self.conn.sendall(b"Please enter your call: ")
            self.conn.settimeout(0.2)
            while True:
                try:
                    d = self.conn.recv(4096)
                    if not d:
                        return
                    self.got += d
                except socket.timeout:
                    continue
        except OSError:
            return

    def wait_got(self, text, timeout=3.0):
        end = time.time() + timeout
        while text.encode() not in self.got and time.time() < end:
            time.sleep(0.02)
        return self.got.decode()

    def close(self):
        for s in (self.conn, self.ls):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass


class RelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.up = FakeCluster()
        self.lines = []
        self.srv = ClusterServer(port=0, version="0.7.142", upstream=("127.0.0.1", self.up.port),
                                 on_upstream_line=self.lines.append, retry_s=1)
        self.assertTrue(self.srv.start())
        self.port = self.srv._sock.getsockname()[1]
        self.c = Client(self.port)
        self.c.until("login:")
        self.c.send("S55OO")
        self.assertIn("S55OO", self.up.wait_got("S55OO"))       # logged in to the cluster
        for _ in range(100):
            if self.srv.relay_connected():
                break
            time.sleep(0.02)
        self.assertTrue(self.srv.relay_connected())

    def tearDown(self) -> None:
        self.c.s.close()
        self.srv.stop()
        self.up.close()

    def test_both_ways_and_no_double_spot(self) -> None:
        self.assertIn("Please enter your call", self.c.until("your call"))
        self.up.conn.sendall(b"DX de OH6BG:     14025.0  DL1ABC       CQ                  1200Z\r\n")
        self.assertIn("DL1ABC", self.c.until("1200Z"))
        for _ in range(50):
            if self.lines:
                break
            time.sleep(0.02)
        self.assertTrue(self.lines and "DL1ABC" in self.lines[0])
        # the same spot from POUNCer: the client already has it from the cluster
        self.c.buf = b""
        self.srv.publish(spot(khz=14025.1))
        # a new one from POUNCer goes in between
        self.assertTrue(self.srv.publish(spot(call="F5BY", khz=7012.0)))
        out = self.c.until("F5BY")
        self.assertIn("F5BY", out)
        self.assertNotIn("14025.1", out)
        # the client's DX spot goes to the cluster
        self.c.send("DX 14030.0 K2BWC tnx")
        self.assertIn("DX 14030.0 K2BWC tnx", self.up.wait_got("K2BWC"))

    def test_skimmer_spots_come_from_pouncer_not_the_cluster(self) -> None:
        self.c.until("your call")
        self.up.conn.sendall(b"DX de S53WW-#:   14025.0  DL1ABC       CW    23 dB  28 WPM  CQ      1200Z\r\n"
                             b"DX de S50ARX:    14205.0  EA6RJU       59 tnx                  1201Z\r\n")
        out = self.c.until("EA6RJU")
        self.assertIn("EA6RJU", out)                     # a person's spot passes
        self.assertNotIn("DL1ABC", out)                  # the skimmer's doesn't ...
        for _ in range(50):
            if len(self.lines) >= 2:
                break
            time.sleep(0.02)
        self.assertEqual(len(self.lines), 2)             # ... but POUNCer reads both
        self.assertTrue(self.srv.publish(spot()))        # POUNCer's own (chosen source) goes
        self.assertIn("DL1ABC", self.c.until("DL1ABC"))

    def test_thrown_out_by_another_login_waits(self) -> None:
        self.c.until("your call")
        self.srv.KICK_WAIT_S = 30
        self.up.conn.sendall(b"Reconnected as S55OO at 192.0.2.7, this instance is disconnected\r\n")
        self.up.conn.close()
        out = self.c.until("Trying again")
        self.assertIn("another connection logged in as S55OO", out)
        self.assertNotIn("was lost - connecting again", out)

    def test_people_spots_only_with_cluster_on(self) -> None:
        self.c.until("your call")
        self.srv.pass_people = lambda: False                 # Cluster off
        self.up.conn.sendall(b"DX de S50ARX:    14205.0  EA6RJU       59 tnx                  1201Z\r\n"
                             b"WWV de VE7CC: SFI=150\r\n")
        out = self.c.until("SFI")
        self.assertIn("SFI=150", out)                       # announcements pass
        self.assertNotIn("EA6RJU", out)

    def test_own_line_not_glued_to_a_half_line(self) -> None:
        self.c.until("your call")
        self.up.conn.sendall(b"S55OO de AZURE 7-Nov-2026 1200Z >")
        self.c.until("1200Z >")
        time.sleep(0.2)
        self.c.buf = b""
        self.srv.publish(spot(call="G2VSR", khz=3525.0))
        out = self.c.until("G2VSR")
        self.assertTrue(out.startswith("\r\nDX de"), out[:30])

    def test_parse_dx(self) -> None:
        from pouncer.clusterserver import parse_dx
        p = parse_dx("DX de S50ARX:    14205.0  EA6RJU       59 tnx QSO           1201Z JN76")
        self.assertEqual((p["call"], p["freq_khz"], p["spotter"]), ("EA6RJU", 14205.0, "S50ARX"))
        self.assertIn("tnx", p["comment"])
        self.assertIsNone(parse_dx("WWV de VE7CC: SFI=150"))


class NoUpstreamTests(unittest.TestCase):
    def test_unreachable_cluster_note(self) -> None:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
        s.close()
        srv = ClusterServer(port=0, upstream=("127.0.0.1", dead), retry_s=30)
        self.assertTrue(srv.start())
        try:
            c = Client(srv._sock.getsockname()[1])
            c.until("login:")
            c.send("S55OO")
            self.assertIn("POUNCer's spots only", c.until("spots only"))
            self.assertFalse(srv.relay_connected())
            for _ in range(50):
                if srv.client_count():
                    break
                time.sleep(0.02)
            self.assertTrue(srv.publish(spot()))
            self.assertIn("DL1ABC", c.until("DL1ABC"))
            c.s.close()
        finally:
            srv.stop()


class AppRelayTests(unittest.TestCase):
    def test_n1mm_broadcast_ignored_while_relayed(self) -> None:
        import types
        from tests.test_so2r import _import_app
        app = _import_app()
        srv = types.SimpleNamespace(relay_connected=lambda: True,
                                    echo=lambda *a: self.fail("not reached"))
        f = types.SimpleNamespace(_dxc=srv)
        # returns at once (the bare fake has nothing else)
        app.PouncerApp._on_n1mm_spot(f, {"call": "EA6RJU", "freq_khz": 14205.0, "action": "add"})

    def test_relay_line_goes_the_n1mm_way(self) -> None:
        import types
        from tests.test_so2r import _import_app
        app = _import_app()
        got = []
        f = types.SimpleNamespace(_on_n1mm_spot=lambda pkt, via: got.append((pkt["call"], via)),
                                  _relay_clients=lambda: [])
        app.PouncerApp._on_relay_line(f, "DX de S50ARX:    14205.0  EA6RJU   59        1201Z")
        app.PouncerApp._on_relay_line(f, "S55OO de AZURE >")
        self.assertEqual(got, [("EA6RJU", "relay")])

    def test_upstream_default_is_the_ssb_cluster(self) -> None:
        import types
        from tests.test_so2r import _import_app
        app = _import_app()
        cfg = types.SimpleNamespace(cluster_relay=True, cluster_upstream="", ssb_host="azure.s53m.com",
                                    ssb_port=8000)
        f = types.SimpleNamespace(cfg=cfg)
        up = app.PouncerApp._cluster_upstream
        self.assertEqual(up(f), ("azure.s53m.com", 8000))
        cfg.cluster_upstream = "dxc.example.org:7300"
        self.assertEqual(up(f), ("dxc.example.org", 7300))
        cfg.cluster_relay = False
        self.assertIsNone(up(f))


class ClusterButtonTests(unittest.TestCase):
    """The 'Cluster' source: spots of people only when switched on (not on
    a team slave)."""

    def _fake(self, sources, role="alone"):
        import types
        from tests.test_so2r import _import_app
        app = _import_app()
        team = None if role == "alone" else types.SimpleNamespace(started=0.0, role=lambda: role)
        f = types.SimpleNamespace(cfg=types.SimpleNamespace(rbn_source=sources,
                                                            ssb_host="azure.s53m.com"), team=team)
        return app.PouncerApp._ssb_wanted, f

    def test_on_off(self) -> None:
        fn, f = self._fake("rbn,cluster")
        self.assertTrue(fn(f))
        fn, f = self._fake("rbn")
        self.assertFalse(fn(f))

    def test_slave_never(self) -> None:
        fn, f = self._fake("rbn,cluster", role="slave")
        self.assertFalse(fn(f))
        fn, f = self._fake("rbn,cluster", role="master")
        self.assertTrue(fn(f))


class OneLoginTests(unittest.TestCase):
    """N1MM through the relay and POUNCer's own telnet, same call, same node:
    only one connection (else the node throws one out)."""

    def test_own_client_steps_aside_and_reads_the_relay(self) -> None:
        import queue
        from pouncer.rbn import RBNClient
        up = FakeCluster()                       # POUNCer's own client connects first
        q = queue.Queue()
        own = RBNClient("127.0.0.1", up.port, "S55OO", "", q, history=0)
        own.start()
        try:
            self.assertIn("S55OO", up.wait_got("S55OO"))
            states = []
            srv = ClusterServer(port=0, upstream=("127.0.0.1", up.port),
                                on_relay_state=lambda h, p, call, on: (
                                    states.append(on), own.share(h if on else None, p, call)))
            # the relay's connection: the fake accepts once, so a second fake
            relay_up = FakeCluster()
            srv.upstream = ("127.0.0.1", relay_up.port)
            srv.on_relay_state = lambda h, p, call, on: (
                states.append(on), own.share("127.0.0.1" if on else None, up.port, call))
            self.assertTrue(srv.start())
            c = Client(srv._sock.getsockname()[1])
            c.until("login:")
            c.send("S55OO")
            relay_up.wait_got("S55OO")
            for _ in range(100):
                if states and own._is_shared():
                    break
                time.sleep(0.02)
            self.assertEqual(states, [True])
            for _ in range(100):                 # our own connection was closed
                try:
                    if up.conn.recv(1) == b"":
                        break
                except OSError:
                    break
                time.sleep(0.02)
            own.feed_shared("DX de S53WW-#:  14025.0  DL1ABC       CW    23 dB  28 WPM  CQ      1200Z")
            spot = q.get(timeout=2)
            self.assertEqual(spot.dxcall, "DL1ABC")
            c.s.close()
            for _ in range(200):
                if states == [True, False]:
                    break
                time.sleep(0.02)
            self.assertEqual(states, [True, False])
            self.assertFalse(own._is_shared())
            srv.stop()
            relay_up.close()
        finally:
            own.stop()
            up.close()

    def test_other_login_or_node_not_shared(self) -> None:
        import queue
        from pouncer.rbn import RBNClient
        own = RBNClient("azure.s53m.com", 8000, "S55OO-77", "", queue.Queue())
        own.share("azure.s53m.com", 8000, "S55OO")
        self.assertFalse(own._is_shared())            # the my-call feed: its own login
        own2 = RBNClient("telnet.reversebeacon.net", 7000, "S55OO", "", queue.Queue(),
                         backup=("azure.s53m.com", 8000))
        own2.share("AZURE.s53m.com", 8000, "s55oo")
        self.assertFalse(own2._is_shared())           # on RBN: nothing to share
        own2.on_backup = True
        self.assertTrue(own2._is_shared())            # on the telnet backup: shared


if __name__ == "__main__":
    unittest.main()
