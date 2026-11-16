"""Remove WARC spots: none on 60 / 30 / 17 / 12 m (Settings > Spots)."""

import os
import tempfile
import types
import unittest

from pouncer import settings
from pouncer.bands import WARC_BANDS, is_warc
from pouncer.config import load_config


class WarcTests(unittest.TestCase):
    def test_bands(self) -> None:
        self.assertEqual(WARC_BANDS, {"60m", "30m", "17m", "12m"})
        for khz in (5354.0, 10110.0, 18080.0, 24900.0):
            self.assertTrue(is_warc(khz), khz)
        for khz in (1820.0, 3520.0, 7020.0, 14020.0, 21020.0, 28020.0, 50100.0, None):
            self.assertFalse(is_warc(khz), khz)

    def test_setting_off_by_default_and_saved(self) -> None:
        self.assertFalse(load_config("").hide_warc)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[rbn]\nhide_beacons = true\n")
            cfg = load_config(p)
            self.assertTrue(settings.save(cfg, p, "rbn.hide_warc", "true")["ok"])
            self.assertTrue(cfg.hide_warc)
            self.assertTrue(load_config(p).hide_warc)
        self.assertEqual(settings.place("rbn.hide_warc")[0], "Spots")

    def test_list_console_and_port_10000(self) -> None:
        try:
            from tests.test_so2r import _import_app
            P = _import_app().PouncerApp
        except Exception as exc:  # noqa: BLE001 - the app needs tkinter
            self.skipTest(f"app not importable here: {exc}")
        from tests.test_console_shown import _app
        f = _app(zones=())
        f.spotter_filter.configure("ww")
        line = "DX de DL1ABC-#:   10112.0  K1ABC          CW    24 dB  28 WPM  CQ      1802Z"
        self.assertTrue(f._console_shown("rbn", line))
        f.cfg.hide_warc = True
        self.assertFalse(f._console_shown("rbn", line))
        self.assertTrue(f._console_shown("rbn", line.replace("10112.0", "14025.0")))
        served = []
        g = types.SimpleNamespace(cfg=types.SimpleNamespace(hide_warc=True),
                                  _dxc=types.SimpleNamespace(publish=served.append,
                                                             relay=lambda: True, port=10000))
        from pouncer.rbn import parse_spot
        P._serve(g, parse_spot(line))
        self.assertEqual(served, [])


if __name__ == "__main__":
    unittest.main()
