"""N1MM's own <spot> packets: parsing, and (v0.7.213) only counted - never listed."""


import queue
import types
import unittest

from pouncer import lights
from pouncer.n1mm import parse_spot_packet
from pouncer.rbn import spot_from_n1mm

PKT = (b'<?xml version="1.0" encoding="utf-8"?>\r\n<spot><app>N1MM</app>'
       b'<StationName>PC1</StationName><dxcall>k1abc</dxcall><frequency>14205.0</frequency>'
       b'<spottercall>S50XYZ</spottercall><comment>59 cq contest</comment>'
       b'<action>add</action><mode>USB</mode><status></status>'
       b'<timestamp>2026-10-24 12:00:00</timestamp></spot>')


def _app():
    from tests.test_so2r import _import_app
    return _import_app()


class ParseTests(unittest.TestCase):
    def test_packet(self) -> None:
        p = parse_spot_packet(PKT)
        self.assertEqual((p["call"], p["freq_khz"], p["spotter"], p["mode"], p["action"]),
                         ("K1ABC", 14205.0, "S50XYZ", "USB", "add"))
        s = spot_from_n1mm(p, now=100.0)
        self.assertEqual((s.dxcall, s.mode, s.spotter, s.received), ("K1ABC", "SSB", "S50XYZ", 100.0))
        self.assertEqual(s.time_utc, "0001Z")            # received time (N1MM sends none)

    def test_other_packets_and_units(self) -> None:
        self.assertIsNone(parse_spot_packet(b"<RadioInfo><Freq>1420500</Freq></RadioInfo>"))
        self.assertIsNone(parse_spot_packet(b"not xml"))
        p = parse_spot_packet(PKT.replace(b"14205.0", b"1420500"))      # 10 Hz units
        self.assertAlmostEqual(p["freq_khz"], 14205.0)

    def test_skimmer_comment_and_band_plan(self) -> None:
        p = parse_spot_packet(PKT.replace(b"<mode>USB</mode>", b"<mode></mode>")
                              .replace(b"14205.0", b"14025.0")
                              .replace(b"59 cq contest", b"CW 22 dB 28 WPM CQ"))
        s = spot_from_n1mm(p)
        self.assertEqual((s.mode, s.snr, s.wpm, s.spot_type), ("CW", 22, 28, "CQ"))

    def test_light(self) -> None:
        L = lights
        self.assertEqual(L.telnet_light([]), (L.OFF, "Telnet off"))
        self.assertEqual(L.telnet_light([(1, "logged in as S55OO")]), (L.OK, "Telnet #1"))
        self.assertEqual(L.telnet_light([(1, "logged in"), (3, "logged in")]),
                         (L.OK, "Telnet #1+#3"))
        self.assertEqual(L.telnet_light([(1, "logged in"), (2, "connecting to x")]),
                         (L.WAIT, "Telnet #1+#2 (#2 not yet)"))
        self.assertEqual(L.telnet_light([(2, "disconnected, reconnecting")])[0], L.BAD)
        self.assertEqual(L.telnet_light([(1, "same")])[0], L.HOLD)
        self.assertEqual(L.telnet_light([(1, "logged in"), (2, "same")]), (L.OK, "Telnet #1"))
        self.assertEqual(L.telnet_light([(1, "x")], "slave"), (L.OK, "Telnet via master"))
        self.assertEqual(L.http_light(True, False, False), (L.WAIT, "HTTP"))
        self.assertEqual(L.http_light(True, True, True)[0], L.OK)
        self.assertEqual(L.http_light(True, True, False)[0], L.BAD)
        self.assertEqual(L.http_light(False, False, False)[0], L.OFF)


class N1MMSpotsTests(unittest.TestCase):
    """v0.7.213: N1MM's spots no longer stand in for a telnet source - they
    are counted (N1MM's telnet is alive) and checked for my call, but never
    put into the list (every Telnet brings skimmer and people's spots)."""

    def test_counted_not_listed(self) -> None:
        P = _app().PouncerApp
        f = types.SimpleNamespace(
            N1MM_SPOT_BREAK_S=P.N1MM_SPOT_BREAK_S, _n1mm_gap=None, _n1mm_streak=0,
            _n1mm_spot_first=None, _n1mm_spot_last=0.0, _n1mm_spots=0,
            spot_queue=queue.Queue(), _dxc=None)
        P._on_n1mm_spot(f, parse_spot_packet(PKT))
        P._on_n1mm_spot(f, parse_spot_packet(PKT))
        self.assertEqual(f._n1mm_spots, 2)
        self.assertIsNotNone(f._n1mm_spot_first)
        self.assertTrue(f.spot_queue.empty())


if __name__ == "__main__":
    unittest.main()


class DigitalTests(unittest.TestCase):
    def test_ft8_window_is_not_ssb(self) -> None:
        from pouncer.rbn import digital_mode, guess_mode
        self.assertEqual(digital_mode(7076.0), "FT8")
        self.assertEqual(digital_mode(14080.5), "FT4")
        self.assertEqual(digital_mode(7100.0), "")
        self.assertEqual(guess_mode(7076.0, "cq"), "SSB")          # overlap: SSB until PSKR says
        self.assertEqual(guess_mode(14075.0, "cq"), "FT8")         # below the SSB segment
        self.assertEqual(guess_mode(7076.0, "SSB 59"), "SSB")       # says SSB: SSB
        self.assertEqual(guess_mode(7150.0, "59"), "SSB")
        self.assertEqual(guess_mode(14205.0, "FT8 -12dB"), "FT8")   # says FT8

    def test_n1mm_usb_spot_on_14075_is_ft8_on_7076_ssb(self) -> None:
        p = parse_spot_packet(PKT.replace(b"14205.0", b"14075.0").replace(b"59 cq contest", b"-10 dB"))
        self.assertEqual(spot_from_n1mm(p).mode, "FT8")
        p = parse_spot_packet(PKT.replace(b"14205.0", b"7076.0").replace(b"59 cq contest", b"-10 dB"))
        self.assertEqual(spot_from_n1mm(p).mode, "SSB")             # PSK Reporter decides
        p = parse_spot_packet(PKT.replace(b"14205.0", b"7076.0").replace(b"59 cq contest", b"FT8 -10"))
        self.assertEqual(spot_from_n1mm(p).mode, "FT8")
        self.assertEqual(spot_from_n1mm(parse_spot_packet(PKT)).mode, "SSB")

    def test_radio_on_ft8_is_not_phone(self) -> None:
        P = _app().PouncerApp
        snap = {"mode": "USB", "freq_khz": 7076.0}
        f = types.SimpleNamespace(state=types.SimpleNamespace(
            snapshot=lambda nr: snap, radio_numbers=lambda: [1]))
        self.assertFalse(P._phone_radio(f, 1))
        snap["freq_khz"] = 7150.0
        self.assertTrue(P._phone_radio(f, 1))


class RealPacketTests(unittest.TestCase):
    """Packets as N1MM sent them on a Slovenian Windows (decimal comma)."""

    def test_decimal_comma_and_db(self) -> None:
        raw = (b'<?xml version="1.0" encoding="utf-8"?> <spot> <app>N1MM</app> '
               b'<StationName>PC1</StationName> <dxcall>US7IGN</dxcall> '
               b'<frequency>21035,56</frequency> <spottercall>VU2TUM-#</spottercall> '
               b'<timestamp>2026-10-22 08:15:24</timestamp> <action>add</action> <mode>CW</mode> '
               b'<comment>CW 6DB Q:4* Z:14,22,24 </comment> <status>double mult</status> '
               b'<statuslist>double mult</statuslist> </spot>')
        p = parse_spot_packet(raw)
        self.assertAlmostEqual(p["freq_khz"], 21035.56)
        s = spot_from_n1mm(p)
        self.assertEqual((s.dxcall, s.mode, s.snr, s.spotter), ("US7IGN", "CW", 6, "VU2TUM-#"))
        p2 = parse_spot_packet(raw.replace(b"21035,56", b"7018,97"))
        self.assertAlmostEqual(p2["freq_khz"], 7018.97)
        from pouncer.n1mm import _to_float
        self.assertEqual(_to_float("14245"), 14245.0)
        self.assertEqual(_to_float("1.234,5"), 1234.5)
        self.assertEqual(_to_float("1,234.5"), 1234.5)
