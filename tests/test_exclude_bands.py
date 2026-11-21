"""Spots > Exclude: WARC (30 / 17 / 12 m), 60 m and beacons - each its own
switch, in the Spots menu and Settings > Spots > Exclude (v0.7.245)."""
import os
import tempfile
import types
import unittest

from pouncer import settings
from pouncer.bands import band_hidden
from pouncer.collector import Collector
from pouncer.config import load_config
from pouncer.rbn import parse_cluster_spot


def cfg(**kw):
    return types.SimpleNamespace(**kw)


class BandHiddenTests(unittest.TestCase):
    def test_warc_and_60m_apart(self):
        c = cfg(hide_warc=True, hide_60m=False)
        self.assertEqual(band_hidden(c, 10110.0), "WARC")
        self.assertEqual(band_hidden(c, 18080.0), "WARC")
        self.assertEqual(band_hidden(c, 24900.0), "WARC")
        self.assertEqual(band_hidden(c, 5357.0), "")
        self.assertEqual(band_hidden(c, 14025.0), "")
        c = cfg(hide_warc=False, hide_60m=True)
        self.assertEqual(band_hidden(c, 5357.0), "60M")
        self.assertEqual(band_hidden(c, 10110.0), "")

    def test_collector_wanted_follows_it(self):
        line = "DX de S51DX:      5357.0  K1ABC        CW                             1200Z"
        s = parse_cluster_spot(line)
        s.feed_id = s.source = "telnet"
        base = dict(follow_modes=("CW",), hide_beacons=True, telnet1_role="cluster")
        self.assertFalse(Collector.wanted(types.SimpleNamespace(
            cfg=cfg(hide_warc=False, hide_60m=True, **base)), s))
        self.assertTrue(Collector.wanted(types.SimpleNamespace(
            cfg=cfg(hide_warc=True, hide_60m=False, **base)), s))


class ConfigTests(unittest.TestCase):
    def load(self, text):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "config.ini")
        with open(p, "w") as fh:
            fh.write(text)
        return p, load_config(p)

    def test_60m_unset_follows_old_warc_switch(self):
        self.assertTrue(self.load("[rbn]\nhide_warc = true\n")[1].hide_60m)
        self.assertFalse(self.load("[rbn]\nhide_warc = false\n")[1].hide_60m)
        self.assertFalse(self.load("[rbn]\nhide_warc = true\nhide_60m = false\n")[1].hide_60m)

    def test_saved_from_settings(self):
        p, c = self.load("[rbn]\nhide_warc = true\n")
        self.assertTrue(settings.save(c, p, "rbn.hide_60m", "false")["ok"])
        self.assertFalse(load_config(p).hide_60m)
        self.assertTrue(load_config(p).hide_warc)

    def test_one_exclude_group_on_the_spots_page(self):
        for key in ("rbn.hide_warc", "rbn.hide_60m", "rbn.hide_beacons"):
            self.assertEqual(settings.place(key)[1], "Exclude", key)


if __name__ == "__main__":
    unittest.main()
