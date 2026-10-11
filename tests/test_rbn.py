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


if __name__ == "__main__":
    unittest.main()
