"""Tests for the local web (phone) server."""

import json
import unittest
import urllib.error
import urllib.request

from pouncer.web import WebServer, local_ipv4s, parse_ipconfig, pick_web_address


class WebServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, float, str]] = []
        self.keys: list[str] = []
        self.reconnects = 0
        self.bands: list[str] = []
        self.hides: list[tuple[str, float, bool]] = []
        self.tunes: list[int] = []
        self.filters: list[tuple[str, bool]] = []
        self.clears: list[bool] = []
        self.payload = {
            "band": "20m",
            "modes": ["CW"],
            "worked": "1 calls",
            "spots": [
                {"call": "K1ABC", "freq": 14015.5, "mode": "CW",
                 "snr": 22, "wpm": 25, "time": "1135Z"},
            ],
        }
        self.web = WebServer(
            "127.0.0.1", 0, lambda: self.payload,
            lambda call, freq, mode: self.calls.append((call, freq, mode)),
            lambda key: self.keys.append(key),
            self._on_reconnect,
            self.bands.append,
            lambda call, freq, hide: self.hides.append((call, freq, hide)),
            self.tunes.append,
            lambda w, fix: self.filters.append((w, fix)),
            self.clears.append,
        )
        self.web.start()
        self.url = f"http://127.0.0.1:{self.web.port}"

    def tearDown(self) -> None:
        self.web.stop()

    def _on_reconnect(self) -> None:
        self.reconnects += 1

    def _get(self, path: str):
        # generous: the macOS CI runners are sometimes very slow (5 s timed out)
        return urllib.request.urlopen(self.url + path, timeout=30)

    def test_tiles_are_kept_not_redrawn(self) -> None:
        # the page updates its tiles in place (keyed by call + MHz) instead
        # of emptying the grid every second (contest traffic: no flicker)
        with self._get("/") as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("const tiles = new Map()", page)
        self.assertIn("el._spot = s", page)
        self.assertNotIn("grid.textContent = ''", page)

    def test_index_serves_mobile_page(self) -> None:
        with self._get("/") as resp:
            body = resp.read().decode("utf-8")
        self.assertEqual(resp.status, 200)
        self.assertIn("POUNCer", body)
        self.assertIn("/api/spots", body)
        self.assertIn("/api/qsy", body)
        self.assertIn("/api/key", body)
        for key in ("time", "freq", "snr"):
            self.assertIn(f'data-sort="{key}"', body)
        self.assertIn('id="logwarn"', body)
        from pouncer import __version__
        self.assertIn(f"<title>POUNCer {__version__}</title>", body)
        self.assertIn("/api/reconnect", body)

    def test_tune_steps(self) -> None:
        for q in ("25", "-25"):
            with self._get("/api/tune?hz=" + q) as resp:
                self.assertTrue(json.load(resp)["ok"])
        self.assertEqual(self.tunes, [25, -25])

    def test_filter_buttons(self) -> None:
        with self._get("/") as resp:
            body = resp.read().decode("utf-8")
        self.assertNotIn('data-filter=', body)      # NAR/WIDE buttons removed
        self.assertIn('id="next"', body)             # NEXT took their place
        for q in ("w=narrow", "w=WIDE", "w=wide&fix=1"):
            with self._get("/api/filter?" + q) as resp:
                self.assertTrue(json.loads(resp.read())["ok"])
        self.assertEqual(
            self.filters, [("narrow", False), ("wide", False), ("wide", True)]
        )

    def test_help_page(self) -> None:
        with self._get("/") as resp:
            self.assertIn('href="/help"', resp.read().decode("utf-8"))
        with self._get("/help") as resp:
            body = resp.read().decode("utf-8")
        self.assertEqual(resp.status, 200)
        from pouncer import __version__
        self.assertIn(f"POUNCer {__version__} help", body)
        for word in ("CLR", "MLT", "Run history", "cq3m", "WPM", "NEXT", "ENTER"):
            self.assertIn(word, body)

    def test_clear_and_undo(self) -> None:
        with self._get("/") as resp:
            self.assertIn('id="clr"', resp.read().decode("utf-8"))
        for q in ("", "?undo=1"):
            with self._get("/api/clear" + q) as resp:
                self.assertTrue(json.loads(resp.read())["ok"])
        self.assertEqual(self.clears, [False, True])

    def test_filter_rejects_bad_width(self) -> None:
        for q in ("", "w=medium"):
            with self.assertRaises(urllib.error.HTTPError):
                self._get("/api/filter?" + q)
        self.assertEqual(self.filters, [])

    def test_tune_rejects_bad_step(self) -> None:
        for q in ("", "0", "abc", "20000"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get("/api/tune?hz=" + q)
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
        self.assertEqual(self.tunes, [])

    def test_cw_speed_keys(self) -> None:
        for key in ("PgUp", "pgdn"):
            with self._get("/api/key?key=" + key) as resp:
                self.assertTrue(json.load(resp)["ok"])
        self.assertEqual(self.keys, ["PgUp", "PgDn"])

    def test_hide_and_unhide(self) -> None:
        with self._get("/api/hide?call=k1abc&freq=14015.5") as resp:
            self.assertTrue(json.load(resp)["ok"])
        with self._get("/api/unhide?call=K1ABC&freq=14015.5") as resp:
            self.assertTrue(json.load(resp)["ok"])
        self.assertEqual(self.hides, [("K1ABC", 14015.5, True), ("K1ABC", 14015.5, False)])

    def test_hide_requires_call_and_freq(self) -> None:
        for q in ("", "?call=K1ABC", "?freq=14015"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get("/api/hide" + q)
            self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.hides, [])

    def test_band_invokes_handler(self) -> None:
        with self._get("/api/band?band=40m") as resp:
            data = json.load(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(self.bands, ["40m"])

    def test_band_rejects_non_contest_band(self) -> None:
        for bad in ("30m", "6m", "", "x"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get("/api/band?band=" + bad)
            self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.bands, [])

    def test_reconnect_invokes_handler(self) -> None:
        with self._get("/api/reconnect") as resp:
            data = json.load(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(self.reconnects, 1)

    def test_reconnect_without_handler_is_404(self) -> None:
        web = WebServer("127.0.0.1", 0, lambda: self.payload,
                        lambda *a: None, lambda key: None)
        web.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{web.port}/api/reconnect", timeout=5)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            web.stop()

    def test_spots_json(self) -> None:
        with self._get("/api/spots") as resp:
            data = json.load(resp)
        self.assertEqual(data["band"], "20m")
        self.assertEqual(data["spots"][0]["call"], "K1ABC")

    def test_qsy_invokes_handler(self) -> None:
        with self._get("/api/qsy?call=R9MM&freq=14015.5&mode=CW") as resp:
            data = json.load(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(self.calls, [("R9MM", 14015.5, "CW")])

    def test_qsy_requires_call(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/qsy")
        self.assertEqual(ctx.exception.code, 400)

    def test_key_fkey_invokes_handler(self) -> None:
        with self._get("/api/key?key=F4") as resp:
            data = json.load(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(self.keys, ["F4"])

    def test_key_enter_invokes_handler(self) -> None:
        with self._get("/api/key?key=Enter") as resp:
            data = json.load(resp)
        self.assertTrue(data["ok"])
        self.assertEqual(self.keys, ["Enter"])

    def test_key_rejects_bad_key(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/key?key=F13")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_qsy_handler_error_is_contained(self) -> None:
        web = WebServer(
            "127.0.0.1", 0, lambda: self.payload,
            lambda call, freq, mode: (_ for _ in ()).throw(RuntimeError("boom")),
            lambda key: None,
        )
        web.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{web.port}/api/qsy?call=X", timeout=5
                )
            self.assertEqual(ctx.exception.code, 500)
            # server is still alive afterwards
            with urllib.request.urlopen(
                f"http://127.0.0.1:{web.port}/api/spots", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            web.stop()

    def test_local_ipv4s_shape(self) -> None:
        for ip in local_ipv4s():
            self.assertNotIn("127.", ip)


IPCONFIG_SL = """
Windows IP Configuration


Ethernet adapter Ethernet:

   Pripona DNS za povezavo . . . . . : home
   Krajevni naslov IPv6 povezave . . : fe80::1c2d%7
   Naslov IPv4 . . . . . . . . . . . : 192.168.1.20
   Maska podomrezja . . . . . . . . : 255.255.255.0

Ethernet adapter ZeroTier One [8056c2e21c000001]:

   IPv4 Address. . . . . . . . . . . : 10.66.66.5(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
"""


class PageTests(unittest.TestCase):
    def test_forgets_radio_b_when_n1mm_has_one_radio(self) -> None:
        """Radio B remembered from an SO2V session + one radio now: the A/B
        key is hidden, so the page must drop B (else the band menu saved the
        view for A and looked it up for B: the band never changed)."""
        from pouncer.web import INDEX_HTML
        self.assertIn("myRadio !== data.radio && (data.radios || []).length <= 1", INDEX_HTML)
        self.assertIn("localStorage.removeItem('pouncer.radio')", INDEX_HTML)


class AddressTests(unittest.TestCase):
    def test_parse_ipconfig_localised(self) -> None:
        self.assertEqual(parse_ipconfig(IPCONFIG_SL), [
            ("Ethernet adapter Ethernet", "192.168.1.20"),
            ("Ethernet adapter ZeroTier One [8056c2e21c000001]", "10.66.66.5"),
        ])

    def test_zerotier_preferred(self) -> None:
        self.assertEqual(
            pick_web_address("0.0.0.0", parse_ipconfig(IPCONFIG_SL), "192.168.1.20"),
            ("10.66.66.5", "ZeroTier"),
        )

    def test_lan_when_no_zerotier(self) -> None:
        adapters = [("Ethernet adapter Ethernet", "192.168.1.20")]
        self.assertEqual(
            pick_web_address("0.0.0.0", adapters, "192.168.1.20"),
            ("192.168.1.20", "LAN"),
        )

    def test_specific_bind_host_shown_as_is(self) -> None:
        self.assertEqual(pick_web_address("10.66.66.5", []), ("10.66.66.5", "bound"))


if __name__ == "__main__":
    unittest.main()
