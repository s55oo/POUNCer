"""Band opening timeline: spots per band in 10-minute boxes, kept over a restart."""

import os
import tempfile
import types
import unittest

from pouncer.bandopen import BandActivity
from pouncer.timeline import SLOT_S, Timeline


class TimelineTests(unittest.TestCase):
    def test_boxes_count_spots_stations_dx(self) -> None:
        now = [100 * SLOT_S + 30]
        tl = Timeline(clock=lambda: now[0])
        tl.add("20m", now[0], "K1AA", True)
        tl.add("20m", now[0], "K1AA", True)          # same station: 2 spots, 1 station
        tl.add("20m", now[0], "DL1AA", False)
        tl.add("40m", now[0] - SLOT_S, "JA1AA", True)  # the 10 minutes before
        tl.add("6m", now[0], "X", True)               # not a contest band
        g = tl.grid(1)
        self.assertEqual(len(g["slots"]), 6)          # 1 hour = six boxes
        self.assertEqual(g["slots"][-1], 100 * SLOT_S)
        self.assertEqual(g["cells"]["20m"][-1], [3, 2, 1])
        self.assertEqual(g["cells"]["40m"][-2], [1, 1, 1])
        self.assertEqual(g["cells"]["80m"][-1], [0, 0, 0])

    def test_kept_over_a_restart_and_48_hours(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "timeline.json")
            now = [500 * SLOT_S + 10]
            tl = Timeline(path, clock=lambda: now[0])
            tl.add("15m", now[0] - SLOT_S, "ZS6AA", True)      # finished box: counts only
            tl.add("15m", now[0], "ZS6AA", True)               # running box: names kept
            tl.save()
            tl2 = Timeline(path, clock=lambda: now[0])
            tl2.add("15m", now[0], "ZS6AA", True)              # same station: still 1
            g = tl2.grid(1)
            self.assertEqual(g["cells"]["15m"][-1], [2, 1, 1])
            self.assertEqual(g["cells"]["15m"][-2], [1, 1, 1])
            now[0] += 49 * 3600                                # two days later: gone
            self.assertEqual(sum(c[0] for c in Timeline(path, clock=lambda: now[0]).grid(48)["cells"]["15m"]), 0)

    def test_band_counter_feeds_it(self) -> None:
        cont = {"DL1SKM": "EU", "W3SKM": "NA", "K1AA": "NA", "OK1AA": "EU"}
        ba = BandActivity(lambda c: cont.get(c), lambda: "EU", clock=lambda: 1000.0)
        got = []
        ba.on_count = lambda *a: got.append(a)
        sp = lambda call, sk: types.SimpleNamespace(dxcall=call, freq_khz=14025, spotter=sk + "-#",
                                                    received=1000.0)
        ba.add(sp("K1AA", "DL1SKM"))      # EU skimmer, NA station: DX
        ba.add(sp("OK1AA", "DL1SKM"))     # own continent
        ba.add(sp("OK1AA", "W3SKM"))      # NA skimmer: not counted
        self.assertEqual(got, [("20m", 1000.0, "K1AA", True), ("20m", 1000.0, "OK1AA", False)])


if __name__ == "__main__":
    unittest.main()
