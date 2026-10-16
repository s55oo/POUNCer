"""Band open lights: what skimmers on our continent hear, per band."""

import json
import queue
import time
import types
import unittest

from pouncer.bandopen import BandActivity, spotter_call, state_of
from pouncer.rbn import RBNClient
from pouncer.rbnhttp import RBNHttpFeed
from pouncer.team import Team

CONT = {"S53WW": "EU", "DK9IP": "EU", "W3LPL": "NA", "K1ABC": "NA", "W2XYZ": "NA",
        "JA1AA": "AS", "S50A": "EU", "LZ1AA": "EU", "PY2AA": "SA", "VK2AA": "OC"}


def sp(call, freq, spotter, t):
    return types.SimpleNamespace(dxcall=call, freq_khz=freq, spotter=spotter, received=t)


class CountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = [10000.0]
        self.b = BandActivity(CONT.get, lambda: "EU", clock=lambda: self.now[0])

    def test_only_skimmers_on_our_continent(self) -> None:
        self.b.add(sp("K1ABC", 14020, "S53WW-#", 9990))
        self.b.add(sp("JA1AA", 14021, "W3LPL-#", 9990))       # NA skimmer: not counted
        self.b.add(sp("S50A", 14022, "DK9IP-2", 9990))
        s = self.b.summary()["20m"]
        self.assertEqual((s["spots"], s["calls"], s["dx"]), (2, 2, 1))

    def test_open_local_closed(self) -> None:
        for i, call in enumerate(["K1ABC", "W2XYZ", "JA1AA", "PY2AA", "VK2AA", "K1ABC"]):
            self.b.add(sp(call, 7010 + i, "S53WW-#", 9990))
        self.b.add(sp("S50A", 3510, "DK9IP-#", 9990))
        self.b.add(sp("LZ1AA", 3511, "DK9IP-#", 9990))
        s = self.b.summary()
        self.assertEqual(s["40m"]["dx"], 5)          # K1ABC twice = one station
        self.assertEqual(s["40m"]["state"], "open")
        self.assertEqual(s["80m"]["state"], "local")
        self.assertEqual(s["10m"]["state"], "closed")
        self.assertEqual(set(s), {"160m", "80m", "40m", "20m", "15m", "10m"})

    def test_ten_minute_window(self) -> None:
        self.b.add(sp("K1ABC", 14020, "S53WW-#", 9000))       # 1000 s ago
        self.b.add(sp("W2XYZ", 14021, "S53WW-#", 9900))
        self.assertEqual(self.b.summary()["20m"]["spots"], 1)

    def test_helpers(self) -> None:
        self.assertEqual(spotter_call("OH6BG-2"), "OH6BG")
        self.assertEqual(state_of(None), "closed")
        self.assertEqual(state_of({"spots": 3, "dx": 0}), "local")


class ChipTextTests(unittest.TestCase):
    def test_band_and_value(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        f = types.SimpleNamespace(home_cont="EU")
        t = lambda b, c: app.PouncerApp._band_chip_text(f, b, c)
        self.assertEqual(t("40m", {"state": "open", "dx": 34, "calls": 40}), "40m: 34 DX 85%")
        self.assertEqual(t("80m", {"state": "local", "dx": 0, "calls": 9}), "80m: EU only")
        self.assertEqual(t("15m", {"state": "local", "dx": 2, "calls": 2}), "15m: 2 DX 100%")
        self.assertEqual(t("160m", {"state": "closed"}), "160m: \u2013")


class WarmBoldTests(unittest.TestCase):
    def test_cw_and_ssb_counts_bold(self) -> None:
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        self.assertEqual(P._bold_warm("CW 46044 + SSB 60917 stations known in advance"),
                         "CW **46044** + SSB **60917** stations known in advance")
        self.assertEqual(P._bold_warm("38120 stations known"), "**38120** stations known")


class ShareColorTests(unittest.TestCase):
    def test_orange_to_green_by_dx_share(self) -> None:
        from pouncer.bandopen import dx_pct, share_color
        self.assertEqual(dx_pct({"calls": 40, "dx": 10}), 25)
        self.assertEqual(dx_pct({"calls": 0, "dx": 0}), 0)
        local, _ = share_color(0)
        dx, _ = share_color(100)
        r0, g0 = int(local[1:3], 16), int(local[3:5], 16)
        r1, g1 = int(dx[1:3], 16), int(dx[3:5], 16)
        self.assertGreater(r0, g0)          # orange: more red than green
        self.assertGreater(g1, r1)          # green
        self.assertEqual(share_color(50)[1], "#1f1f1f")   # dark text on the bright middle
        self.assertEqual(share_color(-5), share_color(0))
        self.assertEqual(share_color(250), share_color(100))

    def test_summary_has_the_share(self) -> None:
        now = [10000.0]
        b = BandActivity(CONT.get, lambda: "EU", clock=lambda: now[0])
        for call in ("K1ABC", "W2XYZ", "S50A", "LZ1AA"):
            b.add(sp(call, 14020, "S53WW-#", 9990))
        self.assertEqual(b.summary()["20m"]["pct"], 50)


class FeedHookTests(unittest.TestCase):
    def test_telnet_counts_every_live_spot_not_history(self) -> None:
        raw = []
        c = RBNClient("h", 7000, "S55OO", "S53WW", queue.Queue(), on_raw=raw.append)
        c._history_lines = 0
        c._consume("DX de DK9IP-#:     14014.0  K1ABC          CW    13 dB  25 WPM  CQ      1135Z\n"
                   "DX de S53WW-#:     14015.0  W2XYZ          CW    20 dB  28 WPM  CQ      1135Z\n", "")
        self.assertEqual([s.spotter for s in raw], ["DK9IP-#", "S53WW-#"])   # both counted
        self.assertEqual(c.queue.qsize(), 1)                                  # list: S53WW only

    def test_http_feed_counts_every_spot(self) -> None:
        raw = []
        doc = {"cursor": "1", "spots": [
            {"spotter": "DK9IP", "dxCall": "K1ABC", "freqKHz": 14020, "mode": "CW", "ts": time.time() * 1000},
            {"spotter": "S53WW", "dxCall": "W2XYZ", "freqKHz": 14021, "mode": "CW", "ts": time.time() * 1000}]}
        f = RBNHttpFeed("http://x", types.SimpleNamespace(accept=lambda s: s.startswith("S53WW")),
                        queue.Queue(), fetch=lambda u: json.dumps(doc).encode(), on_raw=raw.append)
        f.poll_once()
        self.assertEqual(len(raw), 2)
        self.assertEqual(f.spots_in, 1)


class TeamTests(unittest.TestCase):
    def test_slave_gets_the_masters_counts(self) -> None:
        t = Team(0, port=18131)
        try:
            bands = {"20m": {"spots": 40, "calls": 30, "dx": 12}}
            t.handle({"t": "hi", "id": "m1", "started": 1.0, "web": 0, "bands": bands}, "192.168.1.9")
            with mock_master(t, "m1"):
                self.assertEqual(t.master_bands(), bands)
        finally:
            t.stop()


class mock_master:
    def __init__(self, team, mid):
        self.team, self.mid = team, mid

    def __enter__(self):
        self.old = self.team.master_id
        self.team.master_id = lambda: self.mid

    def __exit__(self, *a):
        self.team.master_id = self.old


if __name__ == "__main__":
    unittest.main()
