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


if __name__ == "__main__":
    unittest.main()
