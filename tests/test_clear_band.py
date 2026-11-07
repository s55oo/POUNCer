"""CLR BAND / CLR ALL in the header."""

import time
import types
import unittest

from tests.test_so2r import _import_app

_import_app()

from pouncer.rbn import Spot  # noqa: E402


def sp(call, khz):
    return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=20, wpm=28, spot_type="CQ",
                spotter="S53WW-#", time_utc="1200Z", received=time.time())


class ClearTests(unittest.TestCase):
    def fake(self, view=None, band="20m"):
        app = _import_app()
        P = app.PouncerApp
        msgs = []
        spots = {s.key: s for s in (sp("A1A", 14025.0), sp("B1B", 14030.0), sp("C1C", 7012.0))}
        f = types.SimpleNamespace(spots=spots, _dirty=False,
                                  status_label=types.SimpleNamespace(configure=lambda **k: msgs.append(k)),
                                  _view_band_now=lambda: view, _current_band=lambda: band)
        return P, f, msgs

    def test_band(self) -> None:
        P, f, msgs = self.fake()
        P._clear_band(f)
        self.assertEqual(sorted(s.dxcall for s in f.spots.values()), ["C1C"])
        self.assertIn("2 spots of 20m", msgs[-1]["text"])
        P._unclear_spots(f)
        self.assertEqual(len(f.spots), 3)

    def test_viewed_band_first(self) -> None:
        P, f, msgs = self.fake(view="40m")
        P._clear_band(f)
        self.assertEqual(sorted(s.dxcall for s in f.spots.values()), ["A1A", "B1B"])

    def test_all(self) -> None:
        P, f, msgs = self.fake()
        P._clear_spots(f)
        self.assertEqual(f.spots, {})
        self.assertIn("3 spots cleared", msgs[-1]["text"])


if __name__ == "__main__":
    unittest.main()
