"""★ in the spotter column: our own skimmer heard this station lately."""

import types
import unittest

from pouncer.rbn import Spot, SpotterFilter


def _app():
    from tests.test_so2r import _import_app
    return _import_app()


def spot(call, khz, spotter):
    return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=20, wpm=28, spot_type="CQ",
                spotter=spotter, time_utc="1200Z", received=0.0)


class OwnMarkTests(unittest.TestCase):
    def _fake(self, spotters):
        P = _app().PouncerApp
        f = types.SimpleNamespace(spotter_filter=SpotterFilter(spotters), _own_heard={},
                                  OWN_HEARD_S=P.OWN_HEARD_S)
        f._note_own = P._note_own.__get__(f)
        f._own_mark = P._own_mark.__get__(f)
        return f

    def test_marked_when_others_are_listed_too(self) -> None:
        f = self._fake("TK0C, IT9ABC, ZONE15")
        f._note_own(spot("K1ABC", 14025.0, "TK0C-2-#"))
        f._note_own(spot("W1AW", 14030.0, "IT9ABC-#"))
        self.assertEqual(f._own_mark(spot("K1ABC", 14025.2, "IT9ABC-#")), "TK0C-2")
        self.assertEqual(f._own_mark(spot("W1AW", 14030.0, "IT9ABC-#")), "")
        self.assertEqual(f._own_mark(spot("K1ABC", 7025.0, "IT9ABC-#")), "")   # other band

    def test_not_with_one_skimmer(self) -> None:
        f = self._fake("S53WW")
        f._note_own(spot("K1ABC", 14025.0, "S53WW-#"))
        self.assertEqual(f._own_mark(spot("K1ABC", 14025.0, "S53WW-#")), "")


if __name__ == "__main__":
    unittest.main()
