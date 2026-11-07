"""The one Spotters filter: calls / DXCC / zones / continents / worldwide,
for skimmers and people's spots alike."""

import types
import unittest

from pouncer.rbn import PeopleFilter, SpotterFilter

CONT = {"S53WW": "EU", "DL8LAS": "EU", "OH6BG": "EU", "W3LPL": "NA", "K1TTT": "NA",
        "S50ARX": "EU", "VE7CC": "NA", "JA1ABC": "AS"}
ZONE = {"S53WW": 15, "DL8LAS": 14, "OH6BG": 15, "W3LPL": 5, "S50ARX": 15, "VE7CC": 3,
        "JA1ABC": 25, "K1TTT": 5}
ENT = {"S53WW": "Slovenia", "S50ARX": "Slovenia", "S5": "Slovenia", "DL8LAS": "Germany",
       "9A": "Croatia", "W3LPL": "United States"}


def sf():
    f = SpotterFilter("S53WW", continent_of=CONT.get, zone_of=ZONE.get,
                      home_cont=lambda: "EU")
    f.entity_of = lambda c: types.SimpleNamespace(name=ENT[c]) if c in ENT else None
    return f


class FilterTests(unittest.TestCase):
    def test_calls(self) -> None:
        f = sf()
        f.configure("calls", calls=["S53WW"])
        self.assertTrue(f.accept("S53WW-#"))
        self.assertFalse(f.accept("DL8LAS-#"))
        self.assertTrue(f.people_accept("S50ARX"))        # people: S53WW's continent
        self.assertFalse(f.people_accept("VE7CC"))
        self.assertEqual(f.primary, "S53WW")
        self.assertFalse(f.multi)

    def test_dxcc(self) -> None:
        f = sf()
        f.configure("dxcc", dxcc=["S5", "9A"])
        self.assertTrue(f.accept("S53WW-#"))
        self.assertFalse(f.accept("DL8LAS-#"))
        self.assertTrue(f.people_accept("S50ARX"))
        self.assertFalse(f.people_accept("W3LPL"))
        self.assertEqual(f.label(), "DXCC S5, 9A")

    def test_zones(self) -> None:
        f = sf()
        f.configure("zones", zones=[14, 15])
        for sp in ("S53WW-#", "DL8LAS-#", "OH6BG-#"):
            self.assertTrue(f.accept(sp), sp)
        self.assertFalse(f.accept("W3LPL-#"))
        self.assertTrue(f.people_accept("S50ARX"))
        self.assertFalse(f.people_accept("JA1ABC"))

    def test_continents(self) -> None:
        f = sf()
        f.configure("cont", conts=["NA"])
        self.assertTrue(f.accept("W3LPL-#"))
        self.assertFalse(f.accept("S53WW-#"))
        self.assertTrue(PeopleFilter(f).accept("VE7CC"))
        self.assertFalse(PeopleFilter(f).accept("S50ARX"))

    def test_worldwide(self) -> None:
        f = sf()
        f.configure("ww")
        self.assertTrue(f.accept("JA1ABC-#"))
        self.assertTrue(f.people_accept("JA1ABC"))
        self.assertFalse(f.active())


class AppFilterTests(unittest.TestCase):
    def test_apply_from_config_and_button(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        P = app.PouncerApp
        cfg = types.SimpleNamespace(rbn_spotter="S53WW", filter_mode="calls", filter_dxcc="S5, 9A",
                                    filter_zones="14, ZONE15", filter_conts="eu")
        saved = []
        f = types.SimpleNamespace(cfg=cfg, spotter_filter=sf(), skimwatch=None,
                                  FILTER_NAMES=P.FILTER_NAMES, _list=P._list,
                                  status_label=types.SimpleNamespace(configure=lambda **k: None))
        f._apply_filter = lambda log_it=True: P._apply_filter(f, log_it)

        def save(k, v):
            saved.append((k, v))
            cfg.filter_mode = v
            f._apply_filter()
        f._save_setting = save
        f._apply_filter()
        self.assertEqual(f.spotter_filter.calls, ("S53WW",))
        P._set_spotter_filter(f, "zones")
        self.assertEqual(saved, [("filter.mode", "zones")])
        self.assertEqual(f.spotter_filter.zones, frozenset({14, 15}))
        self.assertIn("zones", P._filter_tip(f, "zones").lower())
        cfg.filter_dxcc = ""
        P._set_spotter_filter(f, "dxcc")                     # empty list: refused
        self.assertEqual(len(saved), 1)


class SpottedTests(unittest.TestCase):
    def sf(self):
        from pouncer.rbn import SpottedFilter
        ents = {"JA1ABC": ("Japan", "AS", 25), "JA": ("Japan", "AS", 25),
                "K1ABC": ("United States", "NA", 5), "DL1ABC": ("Germany", "EU", 14),
                "VK2ABC": ("Australia", "OC", 30)}
        return SpottedFilter(entity_of=lambda c: types.SimpleNamespace(
            name=ents[c][0], continent=ents[c][1], cq=ents[c][2]) if c in ents else None)

    def test_modes(self) -> None:
        f = self.sf()
        self.assertTrue(f.accept("ANY1"))                       # ww
        f.configure("calls", calls=["3Y0", "VK2"])
        self.assertTrue(f.accept("VK2ABC"))
        self.assertFalse(f.accept("K1ABC"))
        f.configure("dxcc", dxcc=["JA"])
        self.assertTrue(f.accept("JA1ABC"))
        self.assertFalse(f.accept("K1ABC"))
        f.configure("zones", zones=[5, 30])
        self.assertTrue(f.accept("K1ABC") and f.accept("VK2ABC"))
        self.assertFalse(f.accept("DL1ABC"))
        f.configure("cont", conts=["NA", "OC"])
        self.assertTrue(f.accept("VK2ABC"))
        self.assertFalse(f.accept("JA1ABC"))
        self.assertFalse(f.accept("XX9ZZZ"))                    # unknown country
        self.assertEqual(f.label(), "continent NA, OC")
        f.configure("dxcc", dxcc=[])                            # empty list = everyone
        self.assertTrue(f.accept("K1ABC"))

    def test_menu_and_apply(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        P = app.PouncerApp
        cfg = types.SimpleNamespace(spotted_mode="ww", spotted_calls="", spotted_dxcc="JA, VK",
                                    spotted_zones="", spotted_conts="NA")
        saved = []
        f = types.SimpleNamespace(cfg=cfg, spotted_filter=self.sf(), FILTER_NAMES=P.FILTER_NAMES,
                                  _list=P._list,
                                  status_label=types.SimpleNamespace(configure=lambda **k: None))
        f._apply_spotted = lambda log_it=True: P._apply_spotted(f, log_it)

        def save(k, v):
            saved.append((k, v))
            cfg.spotted_mode = v
            f._apply_spotted()
        f._save_setting = save
        P._set_spotted_filter(f, "dxcc")
        self.assertEqual(saved, [("spotted.mode", "dxcc")])
        self.assertTrue(f.spotted_filter.accept("JA1ABC"))
        self.assertFalse(f.spotted_filter.accept("K1ABC"))
        P._set_spotted_filter(f, "calls")                        # empty list: refused
        self.assertEqual(len(saved), 1)


if __name__ == "__main__":
    unittest.main()
