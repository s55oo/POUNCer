"""Phone debug: hold a tile > Debug explains why it looks as it does."""

import json
import time
import types
import unittest
import urllib.request

from pouncer.rbn import Spot


def _spot(call, khz, spotter, snr, age):
    return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=snr, wpm=28, spot_type="CQ",
                spotter=spotter, time_utc="1200Z", received=time.time() - age)


class ExplainTests(unittest.TestCase):
    def _fake(self):
        try:
            from pouncer.app import PouncerApp
        except ImportError as exc:              # Python without Tk
            self.skipTest(str(exc))
        from pouncer.cty import Entity
        spots = {1: _spot("3B8X", 14025.0, "S53WW-#", 18, 40),
                 2: _spot("3B8X", 14025.1, "DL1SKM-#", 22, 10),
                 3: _spot("3B8X", 7012.0, "S53WW-#", 9, 5),
                 4: _spot("K1AA", 14030.0, "S53WW-#", 9, 5)}
        worked = types.SimpleNamespace(
            zone_is_itu=False, contest_name="CQWWCW", log_open=True,
            is_worked=lambda c, f, m: False,
            mult_detail=lambda f, p, z, w: [{"type": "ZN", "value": z, "where": "20m", "new": True},
                                            {"type": "CountryPrefix", "value": p, "where": "20m",
                                             "new": False}])
        f = types.SimpleNamespace(
            spots=spots, worked=worked, home_cont="EU", scp=None,
            cfg=types.SimpleNamespace(fade_minutes=5, max_age_minutes=10, tried_minutes=30,
                                      min_snr=0),
            _entity=lambda c: Entity("Mauritius", "AF", "3B8", 39, 53),
            _is_dx=lambda c: True, _mults=lambda s: 1,
            _run_fields=lambda s: {"run": 12, "hours": 3, "rec": False, "run_band": "20m"},
            _tried=lambda s: (2, 4), _radio_khz=lambda: 14025.0,
            _first_heard_utc=lambda s: "1150Z", _cq_minutes=lambda s: 7)
        return PouncerApp._explain_spot.__get__(f)

    def test_facts_of_the_newest_spot_on_that_band(self) -> None:
        d = self._fake()("3b8x", 14025.0)
        self.assertTrue(d["ok"])
        self.assertEqual(d["spotter"], "DL1SKM-#")          # the newest spot on 20 m
        self.assertEqual(d["snr"], 22)
        self.assertEqual([k["spotter"] for k in d["skimmers"]], ["DL1SKM-#", "S53WW-#"])
        self.assertEqual(d["entity"]["cq"], 39)
        self.assertEqual(d["mults"][0], {"type": "ZN", "value": 39, "where": "20m", "new": True})
        self.assertEqual(d["tried"], {"times": 2, "minutes": 4})
        self.assertEqual(d["run"]["run"], 12)
        self.assertEqual(d["raw"]["spotter"], "DL1SKM-#")   # the exact spot, every field
        self.assertEqual(d["raw"]["freq_khz"], 14025.1)
        self.assertIn("received_utc", d["raw"])
        json.dumps(d)                                       # goes to the phone as JSON

    def test_gone(self) -> None:
        d = self._fake()("ZZ9ZZ", 14025.0)
        self.assertFalse(d["ok"])
        self.assertIn("no longer", d["error"])

    def test_api_and_page(self) -> None:
        from pouncer.web import WebServer
        web = WebServer("127.0.0.1", 0, lambda **k: {}, lambda *a, **k: None, lambda *a, **k: None,
                        lambda: None, lambda *a, **k: None, lambda *a: None, lambda *a, **k: None,
                        lambda *a: None, lambda *a: None)
        got = []
        web.set_explain_provider(lambda c, f: got.append((c, f)) or {"ok": True, "call": c})
        web.start()
        try:
            base = f"http://127.0.0.1:{web.port}"
            d = json.loads(urllib.request.urlopen(base + "/api/explain?call=3B8X&freq=14025.1",
                                                  timeout=5).read())
            self.assertEqual(got, [("3B8X", 14025.1)])
            self.assertTrue(d["ok"])
            page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
            self.assertIn('id="rmdbg"', page)
            self.assertIn("/api/explain?", page)
        finally:
            web.stop()


if __name__ == "__main__":
    unittest.main()


class PingTests(unittest.TestCase):
    def test_ping_answers_at_once(self) -> None:
        from pouncer.web import WebServer, INDEX_HTML
        web = WebServer("127.0.0.1", 0, lambda **k: {}, lambda *a, **k: None, lambda *a, **k: None,
                        lambda: None, lambda *a, **k: None, lambda *a: None, lambda *a, **k: None,
                        lambda *a: None, lambda *a: None)
        web.start()
        try:
            d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{web.port}/api/ping",
                                                  timeout=5).read())
            self.assertTrue(d["ok"])
        finally:
            web.stop()
        self.assertIn("/api/ping", INDEX_HTML)
        self.assertIn("PING_MS = 60000", INDEX_HTML)       # once a minute


class PingTests(unittest.TestCase):
    def test_ping_answers_at_once(self) -> None:
        from pouncer.web import WebServer, INDEX_HTML
        web = WebServer("127.0.0.1", 0, lambda **k: {}, lambda *a, **k: None, lambda *a, **k: None,
                        lambda: None, lambda *a, **k: None, lambda *a: None, lambda *a, **k: None,
                        lambda *a: None, lambda *a: None)
        web.start()
        try:
            d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{web.port}/api/ping",
                                                  timeout=5).read())
            self.assertTrue(d["ok"])
        finally:
            web.stop()
        self.assertIn("/api/ping", INDEX_HTML)
        self.assertIn("PING_MS = 60000", INDEX_HTML)       # once a minute
        self.assertIn('id="pingtag"', INDEX_HTML)
