"""RBN over HTTP backup feed (fake server responses)."""

import json
import queue
import unittest

from pouncer.rbn import SpotterFilter
from pouncer.rbnhttp import RBNHttpFeed, to_spot


def page(spots, cursor="20261015:100", more=False):
    return json.dumps({"cursor": cursor, "reset": False, "hasMore": more,
                       "spots": spots}).encode()


def raw(call, spotter="S53WW", freq=14025.3, mode="CW", ts=1786112300123):
    return {"spotter": spotter, "spotterRaw": spotter + "-#", "dxCall": call,
            "freqKHz": freq, "band": "20M", "mode": mode, "snr": 18, "speed": 28,
            "txMode": mode, "ts": ts}


class HttpFeedTests(unittest.TestCase):
    def test_to_spot(self) -> None:
        s = to_spot(raw("K1VR"))
        self.assertEqual((s.dxcall, s.freq_khz, s.mode, s.snr, s.wpm, s.spotter),
                         ("K1VR", 14025.3, "CW", 18, 28, "S53WW-#"))
        self.assertEqual(s.spot_type, "CQ")
        self.assertEqual(s.time_utc, "1418Z")
        self.assertIsNone(to_spot({"dxCall": "K1VR"}))           # no frequency

    def test_cursor_pages_filter(self) -> None:
        urls, q = [], queue.Queue()
        pages = [page([raw("K1VR"), raw("DL1AA", spotter="W3LPL")], "c1", more=True),
                 page([raw("JA1ZZ", mode="FT8"), raw("S50A")], "c2")]
        feed = RBNHttpFeed("https://x/cors/rbn/live", SpotterFilter("S53WW"), q,
                           fetch=lambda u: urls.append(u) or pages.pop(0))
        self.assertTrue(feed.poll_once())                        # hasMore: ask again
        self.assertFalse(feed.poll_once())
        self.assertIn("lookbackSeconds=600", urls[0])            # first: the last minutes
        self.assertIn("cursor=c1", urls[1])                      # then: only what's new
        self.assertEqual(feed.cursor, "c2")
        got = [q.get_nowait().dxcall for _ in range(q.qsize())]
        self.assertEqual(got, ["K1VR", "S50A"])                  # S53WW only, CW only
        self.assertEqual(feed.spots_in, 2)

    def test_errors_keep_running(self) -> None:
        q, calls, sleeps = queue.Queue(), [], []

        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("timeout")
            feed.stop()
            return page([raw("K1VR")])
        feed = RBNHttpFeed("https://x/live", None, q, fetch=fetch,
                           sleep=lambda s: sleeps.append(s))
        feed.run()
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps[0], 10)                          # backoff after the error
        self.assertEqual(q.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
