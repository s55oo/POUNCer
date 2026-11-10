"""Selected browser bands share existing filtering and per-radio view caches."""
import types
import shutil
import subprocess
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from pouncer.bands import normalize_band_view
from tests import test_minsnr as fixtures

_app = fixtures._app
spot = fixtures.spot


class MultiBandTests(unittest.TestCase):
    def test_selection_validation_and_canonical_cache_key(self):
        for value, expected in ((None, None), ('', None), ('ALL', 'ALL'),
                                ('40m', '40m'), ('40m,80m,40m', '80m,40m'),
                                (' 20m,40m,80m ', '80m,40m,20m'),
                                ('40m,6m', None), ('ALL,40m', None),
                                ('40m,', None)):
            with self.subTest(value=value):
                self.assertEqual(normalize_band_view(value), expected)

    def test_combines_two_or_more_bands_with_existing_filters(self):
        app = _app()
        f = fixtures.FilterTests._fake(self, 10, mults=('MULT80',))
        for call, khz, snr, mode in (
            ('MULT80', 3520, 4, 'CW'), ('WORKED40', 7020, 20, 'CW'),
            ('GOOD40', 7021, 15, 'CW'), ('WEAK80', 3521, 3, 'CW'),
            ('WRONGMODE', 3522, 20, 'RTTY'), ('REMOVED', 3523, 20, 'CW'),
        ):
            s = spot(app, call, snr, freq=khz, mode=mode)
            f.spots[s.key] = s
        f._removed.add(('REMOVED', '80m'))
        f.hide_worked_var.get = lambda: True
        f.worked = types.SimpleNamespace(is_worked=lambda call, freq, mode: call == 'WORKED40')
        for selection, expected in (
            ('80m,40m', {'MULT80', 'GOOD40'}),
            ('80m,40m,20m', {'MULT80', 'GOOD40', 'K1ABC', 'N3AA', 'EA8SSB'}),
        ):
            with self.subTest(selection=selection):
                self.assertEqual({s.dxcall for s in f._visible_spots(1, selection)}, expected)

    def test_cache_is_canonical_separate_per_radio_and_refreshes(self):
        app = _app()
        base_a = {'radio': 1, 'band': '20m'}
        base_b = {'radio': 2, 'band': '40m'}
        f = types.SimpleNamespace(radio=1, _web_cache={1: base_a, 2: base_b},
                                  _web_views={}, _web_view_cache={})
        get = app.PouncerApp._web_spots.__get__(f)
        self.assertEqual(get(2, '40m,80m')['pending'], '80m,40m')
        self.assertIn((2, '80m,40m'), f._web_views)
        ready = {'radio': 2, 'band': '80m,40m', 'spots': ['fresh']}
        f._web_view_cache[(2, '80m,40m')] = ready
        self.assertIs(get(2, '80m,40m,40m'), ready)
        self.assertEqual(get(1, '40m,80m')['radio'], 1)
        self.assertIn((1, '80m,40m'), f._web_views)
        self.assertIs(get(1, '40m,bad'), base_a)
        self.assertIs(get(2), base_b)

    def test_payload_keeps_mults_from_each_band_and_sorts_before_limit(self):
        app = _app()
        spots = [spot(app, 'LOUD40', 30, freq=7020),
                 spot(app, 'MULT80', 4, freq=3520),
                 spot(app, 'MULT40', 12, freq=7021)]
        f = MagicMock()
        f.radio = 1
        f._visible_spots.return_value = spots
        f._current_band.return_value = '20m'
        f._mults.side_effect = lambda s: int(s.dxcall.startswith('MULT'))
        f.state.radio_numbers.return_value = [1, 2]
        f.cwspeed = None
        f.cfg.band_lights = False
        f._checks = []
        f._last_tap = None
        f.cfg.fkeys = []
        f._tried_fields.return_value = {}
        f._run_fields.return_value = {}
        with patch.object(app, 'WEB_MAX_SPOTS_ALL', 2):
            payload = app.PouncerApp._build_web_payload(f, 2, '80m,40m')
        self.assertEqual(payload['band'], '80m,40m')
        self.assertEqual(payload['radio'], 2)
        self.assertTrue(payload['view'])
        self.assertEqual([s['call'] for s in payload['spots']], ['MULT40', 'MULT80'])
        self.assertEqual([s['mult'] for s in payload['spots']], [1, 1])
        f._visible_spots.assert_called_once_with(2, '80m,40m')


class BrowserBandMenuTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser menu tests")
    def test_browser_selection_and_persistence(self):
        r = subprocess.run([shutil.which("node"), "tests/browser_bands.cjs"],
                           cwd=Path(__file__).resolve().parents[1], timeout=30,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
