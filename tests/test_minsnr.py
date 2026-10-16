"""Minimum SNR: list filter (window + phone), /api/minsnr, the setting."""

import json
import types
import unittest
import urllib.error
import urllib.request

from pouncer.web import WebServer


def _app():
    from tests.test_so2r import _import_app
    return _import_app()


def spot(app, call, snr, freq=14025.0, mode="CW"):
    return app.Spot(dxcall=call, freq_khz=freq, mode=mode, snr=snr, wpm=28, spot_type="CQ",
                    spotter="S53WW", time_utc="1200Z", received=1.0)


class FilterTests(unittest.TestCase):
    def _fake(self, min_snr, keep_mults=True, mults=()):
        app = _app()
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(min_snr=min_snr, min_snr_keep_mults=keep_mults,
                                      hide_beacons=False),
            spots={}, _removed=set(), _last_band=None,
            hide_worked_var=types.SimpleNamespace(get=lambda: False),
            own_call=lambda: "S55OO", _current_band=lambda radio=None: "20m",
            _effective_modes=lambda radio=None: ["CW", "SSB"],
            _mults=lambda s: 1 if s.dxcall in mults else 0,
        )
        f._visible_spots = app.PouncerApp._visible_spots.__get__(f)
        for i, (call, snr) in enumerate([("K1ABC", 25), ("W2XYZ", 8), ("N3AA", 12),
                                         ("VP2MAA", 4), ("EA8SSB", None)]):
            s = spot(app, call, snr, freq=14020.0 + i, mode="SSB" if snr is None else "CW")
            f.spots[s.key] = s
        return f

    def calls(self, f):
        return sorted(s.dxcall for s in f._visible_spots())

    def test_off_shows_all(self) -> None:
        f = self._fake(0)
        self.assertEqual(len(self.calls(f)), 5)
        self.assertEqual(f._snr_hidden, 0)

    def test_weaker_hidden_ssb_and_mults_kept(self) -> None:
        f = self._fake(10, mults=("VP2MAA",))
        self.assertEqual(self.calls(f), ["EA8SSB", "K1ABC", "N3AA", "VP2MAA"])
        self.assertEqual(f._snr_hidden, 1)          # W2XYZ 8 dB
        self.assertEqual(f._last_weak, 1)

    def test_mults_hidden_too_when_asked(self) -> None:
        f = self._fake(10, keep_mults=False, mults=("VP2MAA",))
        self.assertEqual(self.calls(f), ["EA8SSB", "K1ABC", "N3AA"])
        self.assertEqual(f._snr_hidden, 2)


class SetTests(unittest.TestCase):
    def test_set_clamps_and_saves_later(self) -> None:
        app = _app()
        afters, saved, msgs = [], [], []
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(min_snr=0), _dirty=False, _min_snr_save=None,
            root=types.SimpleNamespace(after=lambda ms, fn: afters.append((ms, fn)) or len(afters),
                                       after_cancel=lambda i: None),
            status_label=types.SimpleNamespace(configure=lambda **kw: msgs.append(kw["text"])),
            _save_setting=lambda k, v: saved.append((k, v)),
        )
        for n in ("_set_min_snr", "_save_min_snr"):
            setattr(f, n, getattr(app.PouncerApp, n).__get__(f))
        f._set_min_snr(99)
        self.assertEqual(f.cfg.min_snr, 60)
        self.assertTrue(f._dirty)
        self.assertEqual(saved, [])                 # not yet: a second later
        afters[-1][1]()
        self.assertEqual(saved, [("ui.min_snr", "60")])
        f._set_min_snr(-3)
        self.assertEqual(f.cfg.min_snr, 0)
        self.assertIn("all spots", msgs[-1])


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.got = []
        self.web = WebServer("127.0.0.1", 0, lambda: {"spots": []}, lambda *a: None,
                             lambda k: None)
        self.web.start()
        self.url = f"http://127.0.0.1:{self.web.port}"

    def tearDown(self) -> None:
        self.web.stop()

    def _get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=30) as r:
            return json.loads(r.read())

    def test_minsnr(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/minsnr?db=10")           # no handler yet
        cm.exception.close()
        self.web.set_min_snr_handler(self.got.append)
        self.assertTrue(self._get("/api/minsnr?db=10")["ok"])
        for bad in ("-1", "61", "abc"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get("/api/minsnr?db=" + bad)
            self.assertEqual(cm.exception.code, 400)
            cm.exception.close()
        self.assertEqual(self.got, [10])

    def test_page_has_the_button(self) -> None:
        with urllib.request.urlopen(self.url + "/", timeout=30) as r:
            page = r.read().decode()
        for part in ('id="snrsort"', 'id="snrsheet"', "snrbadge", "/api/minsnr?", "weak)"):
            self.assertIn(part, page)


if __name__ == "__main__":
    unittest.main()
