"""Window: click a band chip = the list shows that band; a spot tap QSYs there."""

import types
import unittest


def _fake(radio_band="40m"):
    from tests.test_so2r import _import_app
    app = _import_app()
    msgs = []
    f = types.SimpleNamespace(
        _view_band=None, _view_from=None, _bands_last="x", _dirty=False,
        _radio_band=radio_band,
        status_label=types.SimpleNamespace(configure=lambda **kw: msgs.append(kw["text"])),
        _update_band_lights=lambda: None, _update_top_bar=lambda snap: None,
        state=types.SimpleNamespace(snapshot=lambda nr=None: None), radio=1,
    )
    f._current_band = lambda radio=None: f._radio_band
    for n in ("_on_band_chip", "_view_band_now"):
        setattr(f, n, getattr(app.PouncerApp, n).__get__(f))
    return f, msgs


class ViewBandTests(unittest.TestCase):
    def test_click_shows_band_click_again_back(self) -> None:
        f, msgs = _fake()
        f._on_band_chip("20m")
        self.assertEqual(f._view_band_now(), "20m")
        self.assertTrue(f._dirty)
        self.assertIn("radio stays on 40m", msgs[-1])
        f._on_band_chip("20m")                      # again: back to the radio's band
        self.assertIsNone(f._view_band_now())
        self.assertIn("follows the radio", msgs[-1])

    def test_radios_own_band_is_follow(self) -> None:
        f, _ = _fake()
        f._on_band_chip("40m")
        self.assertIsNone(f._view_band_now())

    def test_radio_moves_band_ends_the_view(self) -> None:
        f, _ = _fake()
        f._on_band_chip("20m")
        f._dirty = False
        f._radio_band = "20m"                       # a spot tapped: the radio QSYed to 20m
        self.assertIsNone(f._view_band_now())
        self.assertTrue(f._dirty)


if __name__ == "__main__":
    unittest.main()
