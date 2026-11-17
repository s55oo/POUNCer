"""Port 10000, S53ZO spot-collector style (v0.7.214): one spot per station -
the same call within 0.5 kHz from another server / skimmer within 30 s is
not resent, unless its Q:n is clearly higher."""

import types
import unittest

from pouncer.rbn import parse_spot

A = "DX de G4ZFE-#:    7022.0  SQ6OEP       CW  14dB Q:9* Z:14,15          0948Z"
B = "DX de OE9GHV-#:   7022.0  SQ6OEP       CW  26dB Q:9* Z:14,15          0948Z"


def _fns():
    from tests.test_so2r import _import_app
    app = _import_app()
    return app.merged_away, app.note_served


class MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.away, self.note = _fns()
        except Exception as exc:  # noqa: BLE001 - needs tkinter
            self.skipTest(f"app not importable here: {exc}")
        self.app = types.SimpleNamespace(cfg=types.SimpleNamespace(cluster_merge=True))

    def test_quality_read(self) -> None:
        self.assertEqual(parse_spot(A).quality, 9)
        self.assertEqual(parse_spot("DX de S53WW-#:   14025.0  DL1ABC       CW    23 dB  "
                                    "28 WPM  CQ      1200Z").quality, 0)

    def test_second_server_held_back_then_free(self) -> None:
        a, b = parse_spot(A), parse_spot(B)
        self.assertEqual(self.away(self.app, a, now=100.0), "")
        self.note(self.app, a, now=100.0)
        self.assertIn("same station 1 s ago", self.away(self.app, b, now=101.0))
        self.assertEqual(self.away(self.app, b, now=131.0), "")          # 30 s later: sent

    def test_other_band_or_qrg_goes_out(self) -> None:
        a = parse_spot(A)
        self.note(self.app, a, now=100.0)
        far = parse_spot(A.replace("7022.0", "7022.6"))
        band = parse_spot(A.replace("7022.0", "14022.0"))
        near = parse_spot(A.replace("7022.0", "7022.4"))
        self.assertEqual(self.away(self.app, far, now=101.0), "")
        self.assertEqual(self.away(self.app, band, now=101.0), "")
        self.assertNotEqual(self.away(self.app, near, now=101.0), "")

    def test_clearly_higher_q_once_more(self) -> None:
        low = parse_spot(A.replace("Q:9*", "Q:2"))
        self.note(self.app, low, now=100.0)
        self.assertNotEqual(self.away(self.app, parse_spot(A.replace("Q:9*", "Q:4*")), now=105.0), "")
        self.assertEqual(self.away(self.app, parse_spot(A), now=105.0), "")   # Q:9 vs Q:2

    def test_off_sends_everything(self) -> None:
        a = parse_spot(A)
        self.note(self.app, a, now=100.0)
        self.app.cfg.cluster_merge = False
        self.assertEqual(self.away(self.app, parse_spot(B), now=101.0), "")


if __name__ == "__main__":
    unittest.main()
