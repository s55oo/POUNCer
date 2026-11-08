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



class DistributorTests(unittest.TestCase):
    """Relay = the output switch: no connection of its own; commands go over
    POUNCer's telnet connection, its answers come back."""

    def setUp(self) -> None:
        self.relay = [True]
        self.sent = []
        self.why = [""]
        self.srv = ClusterServer(port=0, relay=lambda: self.relay[0],
                                 send_upstream=lambda line: (self.sent.append(line), self.why[0])[1])
        self.assertTrue(self.srv.start())
        self.c = Client(self.srv._sock.getsockname()[1])
        self.c.until("login:")
        self.c.send("S55OO")
        self.c.until("de POUNCER >")
        for _ in range(50):
            if self.srv.client_count():
                break
            time.sleep(0.02)

    def tearDown(self) -> None:
        self.c.s.close()
        self.srv.stop()

    def test_spots_only_with_relay_on(self) -> None:
        self.assertTrue(self.srv.publish(spot()))
        self.assertIn("DL1ABC", self.c.until("DL1ABC"))
        self.relay[0] = False
        self.assertFalse(self.srv.publish(spot(call="F5BY", khz=7012.0)))

    def test_commands_over_pouncers_connection_and_answers_back(self) -> None:
        self.c.send("DX 14030.0 K2BWC tnx")
        for _ in range(50):
            if self.sent:
                break
            time.sleep(0.02)
        self.assertEqual(self.sent, ["DX 14030.0 K2BWC tnx"])
        self.srv.to_clients("Spot: K2BWC 14030.0 sent")
        self.assertIn("K2BWC 14030.0 sent", self.c.until("sent"))

    def test_no_telnet_connection_note(self) -> None:
        self.why[0] = "no telnet connection (switch on Telnet or Cluster)"
        self.c.buf = b""
        self.c.send("DX 14030.0 K2BWC")
        self.assertIn("not sent - no telnet connection", self.c.until("Cluster)"))

    def test_spots_made_in_n1mm_kept_local(self) -> None:
        self.srv.spots_out = lambda: False
        self.c.buf = b""
        self.c.send("DX 14030.0 K2BWC tnx")
        self.assertIn("spot not sent to the DX cluster", self.c.until("Cluster server)"))
        self.c.send("sh/dx")                          # other commands still go
        for _ in range(50):
            if self.sent:
                break
            time.sleep(0.02)
        self.assertEqual(self.sent, ["sh/dx"])

    def test_relay_off_nothing_forwarded(self) -> None:
        self.relay[0] = False
        self.c.buf = b""
        self.c.send("DX 14030.0 K2BWC")
        self.c.until("de POUNCER >")
        self.assertEqual(self.sent, [])
        self.srv.to_clients("WWV de VE7CC: SFI=150")
        time.sleep(0.2)
        self.c.buf = b""
        self.assertNotIn("SFI", self.c.until("SFI", timeout=0.5))

    def test_parse_dx(self) -> None:
        from pouncer.clusterserver import parse_dx
        p = parse_dx("DX de S50ARX:    14205.0  EA6RJU       59 tnx QSO           1201Z JN76")
        self.assertEqual((p["call"], p["freq_khz"], p["spotter"]), ("EA6RJU", 14205.0, "S50ARX"))
        self.assertIsNone(parse_dx("WWV de VE7CC: SFI=150"))


class ClientUpstreamTests(unittest.TestCase):
    def test_send_line_and_answers(self) -> None:
        import queue
        from pouncer.rbn import RBNClient
        c = RBNClient("h", 1, "S55OO", "", queue.Queue())
        self.assertFalse(c.send_line("DX 14030 K2BWC"))      # not connected
        got = []
        c.on_line = got.append
        c._consume("Hello S55OO\nDX de S53WW-#:   14025.0  DL1ABC       CW    23 dB  28 WPM  CQ      1200Z\n", "")
        self.assertEqual(got, ["Hello S55OO"])                 # spots stay POUNCer's


if __name__ == "__main__":
    unittest.main()
