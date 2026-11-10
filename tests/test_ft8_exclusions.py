"""FT8 / FT4 spots POUNCer keeps out of the cluster server (port 10000) are
lines in the traffic console's FT8 group (v0.7.174): dropped by a feed,
not served, or held until PSK Reporter answers."""

import queue
import types
import unittest

from pouncer import traffic
from pouncer.rbn import RBNClient, SpotterFilter, Spot

try:
    from pouncer.app import PouncerApp
except Exception:  # noqa: BLE001 - no Tk here
    PouncerApp = None


def _spot(call="DL1ABC", khz=14074.5, mode="FT8", source="cluster"):
    s = Spot(dxcall=call, freq_khz=khz, mode=mode, snr=None, wpm=None, spot_type="",
             spotter="S50ABC", time_utc="1200Z", received=0.0)
    s.source = source
    return s


class _Server:
    port = 10000

    def __init__(self):
        self.sent = []

    def relay(self):
        return True

    def publish(self, spot):
        self.sent.append(spot.dxcall)
        return True


class TapTests(unittest.TestCase):
    def setUp(self) -> None:
        traffic.start()

    def tearDown(self) -> None:
        traffic.stop()

    def test_group_and_line(self) -> None:
        self.assertEqual(traffic.group_of("FT8 server"), "FT8")
        self.assertIn("FT8", traffic.GROUPS)
        traffic.digi_out("server", _spot(), "not served")
        (_, src, text), = traffic.drain()
        self.assertEqual(src, "FT8 server")
        self.assertIn("DL1ABC", text)
        self.assertIn("14074.5", text)
        self.assertIn("not served", text)

    def test_nothing_kept_while_console_closed(self) -> None:
        traffic.stop()
        traffic.digi_out("server", _spot(), "x")
        self.assertEqual(traffic.drain(), [])

    def test_feed_drop_is_listed(self) -> None:
        q = queue.Queue()
        c = RBNClient("h", 1, "S55OO", SpotterFilter("S53WW"), q, modes=("CW",))
        c._consume("DX de S53WW-#:   14074.5  DL1ABC       FT8 -12 dB              1200Z\n", "")
        self.assertTrue(q.empty())
        lines = [x for x in traffic.drain() if x[1].startswith("FT8")]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("dropped", lines[0][2])


@unittest.skipIf(PouncerApp is None, "tkinter not available")
class ServeTests(unittest.TestCase):
    def setUp(self) -> None:
        traffic.start()
        self.srv = _Server()
        self.f = types.SimpleNamespace(cfg=types.SimpleNamespace(follow_modes=("CW",)),
                                       _dxc=self.srv, _overlap_keys=set())
        for n in ("_serve", "_serve_verdict", "_digi_wanted"):
            setattr(self.f, n, getattr(PouncerApp, n).__get__(self.f))

    def tearDown(self) -> None:
        traffic.stop()

    def _ft8_lines(self):
        return [t for _, src, t in traffic.drain() if traffic.group_of(src) == "FT8"]

    def test_ft8_spot_not_served(self) -> None:
        self.f._serve(_spot())
        self.assertEqual(self.srv.sent, [])
        lines = self._ft8_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("not served to port 10000", lines[0])

    def test_ft8_served_when_followed(self) -> None:
        self.f.cfg.follow_modes = ("CW", "FT8")
        self.f._serve(_spot())
        self.assertEqual(self.srv.sent, ["DL1ABC"])
        self.f._serve(_spot(mode="FT4"))
        self.assertEqual(self.srv.sent, ["DL1ABC"])     # FT4 not followed

    def test_cw_spot_served(self) -> None:
        self.f._serve(_spot(khz=14025.0, mode="CW"))
        self.assertEqual(self.srv.sent, ["DL1ABC"])
        self.assertEqual(self._ft8_lines(), [])

    def test_held_until_pskreporter_says_ft8(self) -> None:
        s = _spot(mode="CW")
        self.f._overlap_keys.add(s.key)
        self.f._serve(s)
        self.assertEqual(self.srv.sent, [])
        self.f._serve_verdict(s.key, "FT8")
        self.assertEqual(self.srv.sent, [])
        lines = self._ft8_lines()
        self.assertIn("held", lines[0])
        self.assertIn("PSK Reporter has him on FT8", lines[1])

    def test_held_then_served_without_report(self) -> None:
        s = _spot(mode="CW")
        self.f._overlap_keys.add(s.key)
        self.f._serve(s)
        self.f._serve_verdict(s.key)
        self.assertEqual(self.srv.sent, ["DL1ABC"])
        self.assertIn("served now", self._ft8_lines()[-1])


if __name__ == "__main__":
    unittest.main()
