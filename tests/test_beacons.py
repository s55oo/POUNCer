"""Beacon spots are dropped as they come in (v0.7.177): the NCDXF beacons on
their frequencies, type BEACON / NCDXF, /B calls - each one a line in the
traffic console's BEACON group."""

import types
import unittest

from pouncer import traffic
from pouncer.rbn import Spot, beacon_why, is_beacon, ncdxf_beacon

try:
    from pouncer.app import PouncerApp
except Exception:  # noqa: BLE001 - no Tk here
    PouncerApp = None


def _spot(call, khz, spot_type="CQ"):
    return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=10, wpm=22, spot_type=spot_type,
                spotter="S53WW", time_utc="1200Z", received=0.0)


class NcdxfTests(unittest.TestCase):
    def test_on_beacon_frequencies(self) -> None:
        for call, khz in (("4U1UN", 14100.0), ("4U1UN", 14100.3), ("W6WX", 18110.0),
                          ("OH2B", 21149.6), ("4X6TU", 24930.1), ("YV5B", 28200.0),
                          ("4u1un/b", 14100.1), ("ZS6DN", 28199.5)):
            self.assertTrue(ncdxf_beacon(call, khz), (call, khz))

    def test_real_operation_elsewhere_stays(self) -> None:
        for call, khz in (("4U1UN", 14025.0), ("4U1UN", 7010.0), ("4U1UN", 14101.0),
                          ("OH2B", 21155.0), ("DL1ABC", 14100.0), ("4U1UNX", 14100.0)):
            self.assertFalse(ncdxf_beacon(call, khz), (call, khz))

    def test_why(self) -> None:
        self.assertIn("NCDXF", beacon_why(_spot("4U1UN", 14100.1)))   # typed CQ by a skimmer
        self.assertIn("BEACON", beacon_why(_spot("DK0WCY", 10144.0, "BEACON")))
        self.assertIn("/B", beacon_why(_spot("DL0IGI/B", 28205.0)))
        self.assertEqual(beacon_why(_spot("4U1UN", 14025.0)), "")
        self.assertTrue(is_beacon(_spot("4U1UN", 14100.0)))
        self.assertFalse(is_beacon(_spot("4U1UN", 14025.0)))


@unittest.skipIf(PouncerApp is None, "tkinter not available")
class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        traffic.start()
        self.f = types.SimpleNamespace(cfg=types.SimpleNamespace(hide_beacons=True,
                                                                cluster_port=10000))
        self.f._beacon_out = PouncerApp._beacon_out.__get__(self.f)

    def tearDown(self) -> None:
        traffic.stop()

    def test_dropped_and_listed(self) -> None:
        self.assertTrue(self.f._beacon_out(_spot("4U1UN", 14100.0)))
        self.assertFalse(self.f._beacon_out(_spot("4U1UN", 14025.0)))   # the real op
        lines = [(src, t) for _, src, t in traffic.drain()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(traffic.group_of(lines[0][0]), "BEACON")
        self.assertIn("4U1UN", lines[0][1])
        self.assertIn("not on port 10000", lines[0][1])

    def test_option_off_keeps_them(self) -> None:
        self.f.cfg.hide_beacons = False
        self.assertFalse(self.f._beacon_out(_spot("4U1UN", 14100.0)))
        self.assertEqual(traffic.drain(), [])


if __name__ == "__main__":
    unittest.main()
