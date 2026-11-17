"""Spot memory: a wider filter brings back the spots it kept out (v0.7.210)."""

import queue
import time
import types
import unittest

from pouncer.rbn import SpotterFilter, parse_spot

S5 = "DX de S50ABC-#:   7025.0  DL1AAA         CW    20 dB  28 WPM  CQ      0700Z"
DE = "DX de DL1ABC-#:   7030.0  K1ABC          CW    24 dB  28 WPM  CQ      0700Z"
FT8 = "DX de DL1ABC-#:   7074.0  JA1ABC         FT8  -12 dB         CQ      0700Z"


def _mod():
    from tests.test_so2r import _import_app
    return _import_app()


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.m = _mod()
        except Exception as exc:  # noqa: BLE001 - the app needs tkinter
            self.skipTest(f"app not importable here: {exc}")
        zone = {"S50ABC": 15, "DL1ABC": 14}
        self.sf = SpotterFilter("", zone_of=lambda c: zone.get(c),
                                continent_of=lambda c: "EU", home_cont=lambda: "EU")
        self.sf.configure("zones", zones=(15,))
        self.app = types.SimpleNamespace(
            spots={}, spot_queue=queue.Queue(), spotter_filter=self.sf,
            cfg=types.SimpleNamespace(rbn_source="telnet", follow_modes=("CW",)))
        for line in (S5, DE, FT8):
            self.m.remember_spot(self.app, parse_spot(line))

    def queued(self):
        out = []
        while not self.app.spot_queue.empty():
            out.append(self.app.spot_queue.get_nowait().dxcall)
        return out

    def test_wider_filter_brings_back_what_it_kept_out(self) -> None:
        m = self.m
        s5 = parse_spot(S5)
        self.app.spots[s5.key] = s5                      # S5 skimmer's spot is listed already
        self.assertEqual(m.readmit_spots(self.app), 0)   # zone 15 only: DE skimmer still out
        self.sf.configure("ww")                          # Spotters: worldwide
        self.assertEqual(m.readmit_spots(self.app), 1)
        self.assertEqual(self.queued(), ["K1ABC"])       # not the listed one, not the FT8 one

    def test_old_spots_are_forgotten(self) -> None:
        for s in self.app._spot_memory:
            s.received = time.time() - 20 * 60
        self.sf.configure("ww")
        self.assertEqual(self.m.readmit_spots(self.app), 0)


if __name__ == "__main__":
    unittest.main()
