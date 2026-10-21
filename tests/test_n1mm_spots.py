"""SSB spots from N1MM's own <spot> packets instead of a second cluster connection."""

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
        self.assertEqual(lights.ssb_light("via N1MM"), (lights.OK, "SSB via N1MM"))


class SwitchTests(unittest.TestCase):
    def _fake(self, want=True, use="auto"):
        app = _app()
        P = app.PouncerApp
        stopped = []
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(ssb_use_n1mm=use, ssb_spotters="ALL"),
            N1MM_SPOT_START_S=P.N1MM_SPOT_START_S, N1MM_SPOT_QUIET_S=P.N1MM_SPOT_QUIET_S,
            N1MM_SPOT_HOLD_S=P.N1MM_SPOT_HOLD_S, _ssb_back_at=0.0, N1MM_SPOT_START_N=P.N1MM_SPOT_START_N,
            N1MM_SPOT_BREAK_S=P.N1MM_SPOT_BREAK_S, N1MM_SPOT_QUIET_X=P.N1MM_SPOT_QUIET_X,
            _n1mm_gap=None, _n1mm_streak=0,
            _n1mm_spot_first=None, _n1mm_spot_last=0.0, _n1mm_spots=0, _ssb_via_n1mm=False,
            _n1mm_filter=None, spot_queue=queue.Queue(), ssb_status="off",
            _ssb_client=types.SimpleNamespace(stop=lambda: stopped.append(1)),
            _continent=lambda call: "EU", home_cont="EU", raw=[])
        f._on_raw_spot = f.raw.append
        f._ssb_wanted = lambda: want
        for name in ("_on_n1mm_spot", "_n1mm_spots_ok", "_ensure_ssb_client", "_n1mm_quiet_limit"):
            setattr(f, name, getattr(P, name).__get__(f))
        return f, stopped

    def test_stream_replaces_cluster_and_quiet_brings_it_back(self) -> None:
        f, stopped = self._fake()
        for _ in range(3):
            f._on_n1mm_spot(parse_spot_packet(PKT))
        f._ensure_ssb_client()
        self.assertFalse(f._ssb_via_n1mm)                  # a minute first
        self.assertTrue(f.spot_queue.empty())              # (own cluster still feeds the list)
        f._n1mm_spot_first -= 61
        f._ensure_ssb_client()
        self.assertTrue(f._ssb_via_n1mm)
        self.assertEqual(stopped, [1])
        self.assertIsNone(f._ssb_client)
        self.assertEqual(f.ssb_status, "via N1MM")
        f._on_n1mm_spot(parse_spot_packet(PKT))
        self.assertEqual(f.spot_queue.get_nowait().dxcall, "K1ABC")
        self.assertEqual(len(f.raw), 1)
        # CW skimmer spots from N1MM are not used (RBN has them all)
        f._on_n1mm_spot(parse_spot_packet(PKT.replace(b"<mode>USB</mode>", b"<mode>CW</mode>")))
        self.assertTrue(f.spot_queue.empty())
        # 5 minutes without a spot from N1MM: our own cluster again
        f._n1mm_spot_last -= 21
        self.assertFalse(f._n1mm_spots_ok())

    def test_quiet_20_s_back_to_own_cluster_and_hold(self) -> None:
        f, _ = self._fake()
        import pouncer.app as appmod
        real, real_client = appmod.time.time, appmod.RBNClient
        t = [10000.0]
        appmod.time.time = lambda: t[0]
        appmod.RBNClient = lambda *a, **k: types.SimpleNamespace(start=lambda: None,
                                                                stop=lambda: None)
        f.cfg.ssb_host, f.cfg.ssb_port, f.cfg.ssb_login, f.cfg.rbn_callsign = "h", 1, "", "X"
        f.cfg.ssb_history, f.cfg.ssb_commands = 0, ()
        f._ssb_status_put = lambda s: None
        try:
            for _ in range(25):                        # a spot every 3 s
                f._on_n1mm_spot(parse_spot_packet(PKT))
                t[0] += 3
            f._ensure_ssb_client()
            self.assertTrue(f._ssb_via_n1mm)
            t[0] += 21                                    # N1MM stopped sending
            f._ensure_ssb_client()
            self.assertFalse(f._ssb_via_n1mm)             # own cluster again
            for _ in range(30):                           # N1MM back at once ...
                f._on_n1mm_spot(parse_spot_packet(PKT))
                t[0] += 3
            f._ensure_ssb_client()
            self.assertFalse(f._ssb_via_n1mm)             # ... but own cluster kept 5 min
            for _ in range(80):
                f._on_n1mm_spot(parse_spot_packet(PKT))
                t[0] += 3
            f._ensure_ssb_client()
            self.assertTrue(f._ssb_via_n1mm)              # then N1MM again
        finally:
            appmod.time.time, appmod.RBNClient = real, real_client

    def test_slow_band_still_hands_over_and_doesnt_flip(self) -> None:
        f, _ = self._fake()
        import pouncer.app as appmod
        real = appmod.time.time
        t = [10000.0]
        appmod.time.time = lambda: t[0]
        try:
            for gap in (30, 25, 40, 30):               # a quiet evening: gaps over 20 s
                f._on_n1mm_spot(parse_spot_packet(PKT))
                t[0] += gap
            f._on_n1mm_spot(parse_spot_packet(PKT))
            self.assertTrue(f._n1mm_spots_ok())          # handed over anyway
            f._ensure_ssb_client()
            self.assertTrue(f._ssb_via_n1mm)
            self.assertGreater(f._n1mm_quiet_limit(), 100)   # 4 x ~31 s
            t[0] += 60                                   # a normal pause there: stays
            self.assertTrue(f._n1mm_spots_ok())
            t[0] += 200
            self.assertFalse(f._n1mm_spots_ok())         # really stopped
        finally:
            appmod.time.time = real

    def test_off_or_not_wanted(self) -> None:
        f, _ = self._fake(use="off")
        f._on_n1mm_spot(parse_spot_packet(PKT))
        f._n1mm_spot_first -= 61
        self.assertFalse(f._n1mm_spots_ok())
        f, stopped = self._fake(want=False)
        f._on_n1mm_spot(parse_spot_packet(PKT))
        f._n1mm_spot_first -= 61
        f._ensure_ssb_client()
        self.assertFalse(f._ssb_via_n1mm)
        self.assertEqual(f.ssb_status, "off")


if __name__ == "__main__":
    unittest.main()
