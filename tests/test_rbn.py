import unittest

from pouncer.rbn import TelnetFilter, is_beacon, parse_spot, ports_for_modes


class PortsForModesTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(ports_for_modes(("CW",), 7000, 7001), {7000})
        self.assertEqual(ports_for_modes(("RTTY",), 7000, 7001), {7000})
        self.assertEqual(ports_for_modes(("FT8", "FT4"), 7000, 7001), {7001})
        self.assertEqual(ports_for_modes(("CW", "FT8"), 7000, 7001), {7000, 7001})
        self.assertEqual(ports_for_modes((), 7000, 7001), set())


class ParseSpotTests(unittest.TestCase):
    def test_cw_spot(self):
        line = "DX de S53WW-#:     14014.0  K1ABC          CW    13 dB  25 WPM  CQ      1135Z"
        spot = parse_spot(line)
        self.assertIsNotNone(spot)
        self.assertEqual(spot.spotter, "S53WW-#")
        self.assertEqual(spot.dxcall, "K1ABC")
        self.assertEqual(spot.freq_khz, 14014.0)
        self.assertEqual(spot.mode, "CW")
        self.assertEqual(spot.snr, 13)
        self.assertEqual(spot.wpm, 25)
        self.assertEqual(spot.spot_type, "CQ")
        self.assertEqual(spot.time_utc, "1135Z")

    def test_ft8_spot_without_wpm(self):
        line = "DX de S53WW-#:     14074.0  DL1XYZ         FT8   -5 dB            CQ      1200Z"
        spot = parse_spot(line)
        self.assertIsNotNone(spot)
        self.assertEqual(spot.snr, -5)
        self.assertIsNone(spot.wpm)
        self.assertEqual(spot.mode, "FT8")

    def test_non_spot_lines_ignored(self):
        self.assertIsNone(parse_spot("Please enter your call:"))
        self.assertIsNone(parse_spot("DX de S53WW-#: 14014.0"))


class TelnetFilterTests(unittest.TestCase):
    def test_strips_iac_and_replies(self):
        f = TelnetFilter()
        data, reply = f.feed(bytes([255, 251, 1, 65, 66]))
        self.assertEqual(data, b"AB")
        self.assertEqual(reply, bytes([255, 254, 1]))

    def test_escaped_iac_passes_through(self):
        f = TelnetFilter()
        data, reply = f.feed(bytes([255, 255]))
        self.assertEqual(data, bytes([255]))
        self.assertEqual(reply, b"")


class BeaconTests(unittest.TestCase):
    def test_beacon_spots_detected(self) -> None:
        for line in (
            "DX de S53WW-#:  14100.0  4U1UN  CW  20 dB  22 WPM  NCDXF B  1135Z",
            "DX de S53WW-#:  28205.0  DK0TEN  CW  15 dB  12 WPM  BEACON  1135Z",
            "DX de S53WW-#:  50050.0  OH6/B  CW  10 dB  14 WPM  CQ  1135Z",
        ):
            spot = parse_spot(line)
            self.assertIsNotNone(spot, line)
            self.assertTrue(is_beacon(spot), line)

    def test_normal_cq_spot_is_not_a_beacon(self) -> None:
        spot = parse_spot("DX de S53WW-#:  14014.0  K1ABC  CW  13 dB  25 WPM  CQ  1135Z")
        self.assertFalse(is_beacon(spot))


class OnFreqTrackerTests(unittest.TestCase):
    def _spot(self, freq, t, call="M0KTZ", kind="CQ"):
        from pouncer.rbn import Spot
        return Spot(dxcall=call, freq_khz=freq, mode="CW", snr=10, wpm=25,
                    spot_type=kind, spotter="S53WW-#", time_utc="1200Z", received=t)

    def test_time_on_frequency(self) -> None:
        from pouncer.rbn import OnFreqTracker
        tr = OnFreqTracker(stay_khz=0.5, gap_s=600)
        tr.add(self._spot(7012.0, 1000), "40m")
        tr.add(self._spot(7012.1, 1240), "40m")   # drift 0.1 kHz: still there
        self.assertEqual(tr.minutes("M0KTZ", "40m", 7012.1, 1300), 5)
        self.assertEqual(tr.first_heard("M0KTZ", "40m", 7012.1), 1000)
        tr.add(self._spot(7018.0, 1400), "40m")   # moved: starts again
        self.assertIsNone(tr.minutes("M0KTZ", "40m", 7012.1, 1500))
        self.assertEqual(tr.minutes("m0ktz", "40m", 7018.0, 1520), 2)
        tr.add(self._spot(7018.0, 2500), "40m")   # silent > 10 min: new start
        self.assertEqual(tr.minutes("M0KTZ", "40m", 7018.0, 2560), 1)
        tr.prune(3200)
        self.assertIsNone(tr.minutes("M0KTZ", "40m", 7018.0, 3300))

    def test_beacons_ignored(self) -> None:
        from pouncer.rbn import OnFreqTracker
        tr = OnFreqTracker()
        tr.add(self._spot(14100.0, 0, call="4U1UN", kind="NCDXF"), "20m")
        self.assertIsNone(tr.minutes("4U1UN", "20m", 14100.0, 60))


class SpotterFilterTests(unittest.TestCase):
    def test_backup_skimmers_while_primary_is_quiet(self) -> None:
        from pouncer.rbn import SpotterFilter
        now = [1000.0]
        cont = {"S50ARX": "EU", "OE9GHV": "EU", "W3LPL": "NA"}
        f = SpotterFilter("S53WW", continent_of=cont.get, home_cont=lambda: "EU",
                          quiet_s=300, clock=lambda: now[0])
        self.assertTrue(f.accept("S53WW-#"))
        self.assertFalse(f.accept("S50ARX-#"))       # primary alive: only S53WW
        now[0] += 301                                 # S53WW quiet 5 min
        self.assertTrue(f.backup_active())
        self.assertTrue(f.accept("S50ARX-#"))         # same continent
        self.assertTrue(f.accept("OE9GHV-#"))
        self.assertFalse(f.accept("W3LPL-#"))         # other continent
        self.assertTrue(f.accept("S53WW-#"))          # back again
        self.assertFalse(f.backup_active())
        self.assertFalse(f.accept("S50ARX-#"))

    def test_listed_backups_only(self) -> None:
        from pouncer.rbn import SpotterFilter
        now = [0.0]
        f = SpotterFilter("S53WW", backups=["OE9GHV"], continent_of=lambda c: "EU",
                          quiet_s=60, clock=lambda: now[0])
        now[0] = 61
        self.assertTrue(f.accept("OE9GHV-#"))
        self.assertFalse(f.accept("S50ARX-#"))       # not in the list

    def test_no_primary_accepts_all(self) -> None:
        from pouncer.rbn import SpotterFilter
        self.assertTrue(SpotterFilter("").accept("ANY-#"))


if __name__ == "__main__":
    unittest.main()
