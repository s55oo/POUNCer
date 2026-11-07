"""HamQTH.com backup feeds: its JSON (as served on 2026-11-07) -> spots."""

import json
import queue
import time
import types
import unittest

from pouncer.hamqth import HamQTHFeed, cluster_spot, rbn_spots

RBN = {"spots": [{"dxcall": "S51DX", "band": "40M", "mode": "CW", "freq": 7010.0, "age": 19,
                  "skimmers": [{"call": "RN4WA", "snr": 34}, {"call": "S53WW", "snr": 29}]},
                 {"dxcall": "G0TYX", "band": "40M", "mode": "CW", "freq": 7010.9, "age": 200,
                  "skimmers": [{"call": "LZ2HQ", "snr": 15}]}]}
SPOTS = {"spots": [{"spotter": "K7MSO", "dxcall": "KW5CW", "freq": 14043.1, "band": "20M",
                    "mode": "CW", "comment": "CW", "time": "2026-11-07T20:32:00Z", "dxcc": 291,
                    "country": "United States", "continent": "NA", "lotw": True, "eqsl": True},
                   {"spotter": "KU4BD", "dxcall": "W8F", "freq": 7150.0, "band": "40M",
                    "mode": None, "comment": "", "time": "2026-11-07T20:27:00Z"}]}


class ParseTests(unittest.TestCase):
    def test_rbn_row_one_spot_per_skimmer(self) -> None:
        now = time.time()
        got = rbn_spots(RBN["spots"][0], now)
        self.assertEqual([(s.dxcall, s.spotter, s.snr) for s in got],
                         [("S51DX", "RN4WA-#", 34), ("S51DX", "S53WW-#", 29)])
        self.assertAlmostEqual(got[0].received, now - 19, delta=1)
        self.assertEqual(got[0].source, "hamqth")

    def test_cluster_spot(self) -> None:
        sp = cluster_spot(SPOTS["spots"][1], time.time())
        self.assertEqual((sp.dxcall, sp.spotter, sp.mode), ("W8F", "KU4BD", "SSB"))
        self.assertEqual(sp.time_utc, "2027Z")
        self.assertEqual(cluster_spot(SPOTS["spots"][0], time.time()).mode, "CW")   # HamQTH's
        self.assertIsNone(cluster_spot({"spotter": "S53WW-#", "dxcall": "X", "freq": 7000},
                                       time.time()))


class FeedTests(unittest.TestCase):
    def feed(self, kind, doc, filt=None):
        q = queue.Queue()
        f = HamQTHFeed(kind, filt, q, fetch=lambda url: json.dumps(doc).encode())
        return f, q

    def test_rbn_skimmer_filter_and_new_only(self) -> None:
        only = types.SimpleNamespace(accept=lambda sp: sp.startswith("S53WW"))
        f, q = self.feed("rbn", RBN, only)
        self.assertIn("/rbn?", f.url())
        self.assertIn("maxAge=300", f.url())
        f.poll_once()
        self.assertEqual([q.get_nowait().spotter for _ in range(q.qsize())], ["S53WW-#"])
        self.assertIn("maxAge=90", f.url())
        f.poll_once()                       # age 19 s is still 'new' (< 30 s) ...
        self.assertEqual(q.qsize(), 1)
        RBN2 = {"spots": [dict(RBN["spots"][0], age=45)]}
        f._fetch = lambda url: json.dumps(RBN2).encode()
        f.poll_once()                       # ... 45 s: heard before the last poll
        self.assertEqual(q.qsize(), 1)

    def test_spots_each_once(self) -> None:
        f, q = self.feed("spots", SPOTS)
        f.first = False                     # (the first poll skips spots older than 15 min)
        f.poll_once()
        f.poll_once()
        self.assertEqual(sorted(q.get_nowait().dxcall for _ in range(q.qsize())), ["KW5CW", "W8F"])
        self.assertIn("/spots?", f.url())

    def test_polite_interval(self) -> None:
        self.assertGreaterEqual(HamQTHFeed("rbn", None, queue.Queue(), poll_s=5).poll_s, 15)


if __name__ == "__main__":
    unittest.main()
