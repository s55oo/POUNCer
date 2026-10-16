"""CQ zones: who hears me (spots of my call), what I hear (skimmers near me)."""

import types
import unittest

from pouncer.zones import ZoneMap

ENT = {  # call -> (CQ zone, continent)
    "S50XX": (15, "EU"), "DL1SKM": (14, "EU"), "W3SKM": (5, "NA"), "JA1SKM": (25, "AS"),
    "VK2AA": (30, "OC"), "K1AA": (5, "NA"), "OH2SKM": (15, "EU"), "ZS6AA": (38, "AF"),
}


def entity(call):
    z = ENT.get(call.upper())
    return types.SimpleNamespace(cq=z[0], continent=z[1]) if z else None


def spot(call, freq, skimmer, snr, t):
    return types.SimpleNamespace(dxcall=call, freq_khz=freq, spotter=skimmer + "-#", snr=snr, received=t)


class ZoneMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = [10_000.0]
        self.z = ZoneMap(entity, lambda: "EU", lambda: "S50XX", clock=lambda: self.now[0])

    def test_hears_me_by_skimmer_zone(self) -> None:
        self.z.add(spot("S50XX", 14025, "W3SKM", 12, 9_990))
        self.z.add(spot("S50XX/P", 14025, "W3SKM", 18, 9_995))      # portable = me too
        self.z.add(spot("S50XX", 14025, "JA1SKM", 7, 9_900))
        self.z.add(spot("S50XX", 7025, "DL1SKM", 25, 9_990))
        t = self.z.table(900)["hears_me"]
        self.assertEqual(t["20m"][5]["snr"], 18)
        self.assertEqual(t["20m"][5]["calls"], 1)
        self.assertEqual(t["20m"][5]["n"], 2)
        self.assertEqual(t["20m"][25]["who"], ["JA1SKM"])
        self.assertEqual(t["40m"][14]["snr"], 25)
        self.assertNotIn("i_hear_me", t)

    def test_i_hear_from_skimmers_on_my_continent(self) -> None:
        self.z.add(spot("K1AA", 14010, "DL1SKM", 20, 9_990))       # EU skimmer: counts
        self.z.add(spot("VK2AA", 14012, "OH2SKM", 9, 9_990))
        self.z.add(spot("ZS6AA", 14014, "W3SKM", 30, 9_990))       # NA skimmer: not near me
        t = self.z.table(900)["i_hear"]
        self.assertEqual(set(t["20m"]), {5, 30})
        self.assertEqual(t["20m"][30]["snr"], 9)

    def test_window_and_unknown(self) -> None:
        self.z.add(spot("K1AA", 14010, "DL1SKM", 20, 10_000 - 1000))   # 16 min ago
        self.z.add(spot("XX9ZZ", 14010, "DL1SKM", 20, 9_990))          # zone unknown
        self.z.add(spot("K1AA", 1000.0, "DL1SKM", 20, 9_990))          # not a contest band
        self.assertEqual(self.z.table(900)["i_hear"], {})
        self.assertIn(5, self.z.table(1800)["i_hear"]["20m"])

    def test_web_page_and_api(self) -> None:
        import json
        import urllib.request
        from pouncer.web import WebServer
        web = WebServer("127.0.0.1", 0, lambda **k: {}, lambda *a, **k: None, lambda *a, **k: None,
                        lambda: None, lambda *a, **k: None, lambda *a: None, lambda *a, **k: None,
                        lambda *a: None, lambda *a: None)
        got = {}
        web.set_zones_provider(lambda m: got.setdefault("m", m) and {"own": "S50XX", "minutes": m})
        web.start()
        try:
            port = web.port
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/zones", timeout=5).read().decode()
            self.assertIn("who hears me", page)
            self.assertIn("let minutes = 15", page)
            d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/zones", timeout=5).read())
            self.assertEqual(got["m"], 15)                 # the last 15 minutes by default
            self.assertEqual(d["own"], "S50XX")
        finally:
            web.stop()


if __name__ == "__main__":
    unittest.main()
