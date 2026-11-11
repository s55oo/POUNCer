"""Exercise displayed-list ordering and quiet ticks through real app/history paths."""
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from pouncer.rbn import Spot, OnFreqTracker
from pouncer.summary import TapLog
from pouncer.runhist import RunHistory
from tests.test_so2r import _import_app


def spot(call='S53ZO', freq=14025, received=1000, label='1200Z'):
    return Spot(call, freq, 'CW', 20, 25, 'CQ', 'S53WW-#', label, received)


def app_fake(history, item):
    P = _import_app().PouncerApp
    fake = types.SimpleNamespace(
        cfg=types.SimpleNamespace(zo_mode=False, urgency_contest_minutes=0, fade_minutes=5),
        runhist=history, worked=types.SimpleNamespace(contest_name='CQWWCW', is_worked=lambda *a: False),
        _dirty=False, _last_radio_khz=14025, _desktop_refreshed_at=1000,
        _history_revision=getattr(history, 'revision', 0), _perf={'rebuilds': 0},
        spots={item.key: item}, web=None, updater=types.SimpleNamespace(maybe_check=lambda: None),
        root=types.SimpleNamespace(after=lambda *a: None), _tick=lambda: None,
        tree=Mock(), rows=Mock(), scp=None, _alerts={}, _check_alerts=Mock(),
        _list_kind=lambda: 'rows', _sorted_visible=lambda: [item], _on_freq_spot=lambda _: None,
        _mults=lambda _: 1, _is_dx=lambda _: False, _tried=lambda _: None)
    fake.tree.selection.return_value = ()
    fake.tree.get_children.return_value = ()
    for name in ('_check_auto_restart', '_drain_status', '_check_self_feed_filter', '_update_net_bar',
                 '_update_source_buttons', '_update_filter_buttons', '_check_late_qsy', '_drain_spots',
                 '_drain_wpm', '_ensure_rbn_clients', '_reload_worked', '_prune', '_remember_band_freq',
                 '_update_labels', '_maybe_rate_line', '_drain_web'):
        setattr(fake, name, lambda: None)
    fake._radio_khz = lambda: 14025
    for name in ('_run_fields', '_row_item', '_rebuild'):
        setattr(fake, name, types.MethodType(getattr(P, name), fake))
    fake._age_text, fake.run_explain = P._age_text, P.run_explain
    return P, fake


class DesktopOrderingTests(unittest.TestCase):
    def test_final_sort_preserves_pin_and_releases_on_worked_or_timeout(self):
        P = _import_app().PouncerApp
        items = [spot('DL1AA', 14010), spot('OK1BB', 14012), spot('W1CC', 14014), spot('I2GG', 14016)]
        worked = set()
        f = types.SimpleNamespace(_pin=(('I2GG', '20m'), 1, 1000), sort_col='freq', sort_reverse=False,
                worked=types.SimpleNamespace(is_worked=lambda c, *a: c in worked),
                _visible_spots=lambda **k: items, _view_band_now=lambda: '20m')
        f._apply_pin = types.MethodType(P._apply_pin, f)
        f._sort_key = types.MethodType(P._sort_key, f)
        with patch('pouncer.app.time.time', return_value=1030):
            self.assertEqual([s.dxcall for s in P._sorted_visible(f)], ['DL1AA', 'I2GG', 'OK1BB', 'W1CC'])
            worked.add('I2GG')
            self.assertEqual([s.dxcall for s in P._sorted_visible(f)], [s.dxcall for s in items])
        worked.clear()
        f._pin = (('I2GG', '20m'), 1, 1000)
        with patch('pouncer.app.time.time', return_value=1061):
            self.assertEqual([s.dxcall for s in P._sorted_visible(f)], [s.dxcall for s in items])

    def test_newest_first_uses_chronological_source_time(self):
        P = _import_app().PouncerApp
        items = [spot('OLD', received=1000, label='2359Z'), spot('NEW', received=1120, label='0001Z')]
        f = types.SimpleNamespace(sort_col='time', sort_reverse=True,
                                 _visible_spots=lambda **k: items, _view_band_now=lambda: '20m')
        f._sort_key = types.MethodType(P._sort_key, f)
        self.assertEqual([s.dxcall for s in P._sorted_visible(f)], ['NEW', 'OLD'])


class QuietRefreshTests(unittest.TestCase):
    def test_background_lookup_updates_rows_and_alert_candidates_without_other_events(self):
        with tempfile.TemporaryDirectory() as folder:
            complete = threading.Event()
            history = {'run': 10, 'hours': 2, 'hours_unrounded': True, 'history_contests': ['CQWW']}
            with patch('pouncer.runhist.WORKERS', 1), patch.object(RunHistory, '_lookup', return_value=history):
                rh = RunHistory(folder)
                rh.on_learn = lambda *a: complete.set()
                P, fake = app_fake(rh, spot())
                with patch('pouncer.app.time.monotonic', return_value=1000), patch('pouncer.app.time.time', return_value=1100):
                    fake._dirty = True
                    P._tick_body(fake)
                    self.assertTrue(complete.wait(2))
                    fake.rows.reset_mock()
                    fake._check_alerts.reset_mock()
                    P._tick_body(fake)
                    self.assertTrue(fake.rows.set_items.called)
                    row = fake.rows.set_items.call_args.args[0][0]
                    self.assertTrue(row['rare'])
                    self.assertEqual(fake._check_alerts.call_args.args[0][0].dxcall, 'S53ZO')
                rh.flush()

    def test_quiet_refresh_advances_age_and_fade_but_is_bounded(self):
        P, fake = app_fake(None, spot(received=1000))
        with patch('pouncer.app.time.monotonic', return_value=1000), patch('pouncer.app.time.time', return_value=1299):
            fake._dirty = True
            P._tick_body(fake)
            self.assertFalse(fake.rows.set_items.call_args.args[0][0]['stale'])
            self.assertEqual(fake.rows.set_items.call_args.args[0][0]['age'], '4 min')
        fake.rows.reset_mock()
        with patch('pouncer.app.time.monotonic', return_value=1000.5), patch('pouncer.app.time.time', return_value=1299.5):
            P._tick_body(fake)
            fake.rows.set_items.assert_not_called()
        with patch('pouncer.app.time.monotonic', return_value=1006), patch('pouncer.app.time.time', return_value=1305):
            P._tick_body(fake)
            self.assertTrue(fake.rows.set_items.call_args.args[0][0]['stale'])
            self.assertEqual(fake.rows.set_items.call_args.args[0][0]['age'], '5 min')


    def test_quiet_tiles_advance_cq_minutes_and_expire_tried_mark(self):
        item = spot(received=1000)
        P, fake = app_fake(None, item)
        now = [1000.0]
        fake.cfg.mark_tried = True
        fake.cfg.tried_minutes = 1
        fake.taps = TapLog('', clock=lambda: now[0])
        fake.taps.record(item.dxcall, item.freq_khz)
        fake._on_freq = OnFreqTracker(0.3, 300)
        fake._on_freq.add(item, '20m')
        for name in ('_tried', '_tile_item', '_cq_minutes', '_first_heard_utc'):
            setattr(fake, name, types.MethodType(getattr(P, name), fake))
        fake._list_kind = lambda: 'tiles'
        fake.tiles = Mock()
        now[0] = 1059
        with patch('pouncer.app.time.monotonic', return_value=1000), patch('pouncer.app.time.time', side_effect=lambda: now[0]):
            fake._dirty = True
            P._tick_body(fake)
            tile = fake.tiles.set_items.call_args.args[0][0]
            self.assertEqual(tile['tried'], '\u21bb0m')
            self.assertIn('cq0m', tile['line2'])
        now[0] = 1065
        with patch('pouncer.app.time.monotonic', return_value=1006), patch('pouncer.app.time.time', side_effect=lambda: now[0]):
            P._tick_body(fake)
            tile = fake.tiles.set_items.call_args.args[0][0]
            self.assertEqual(tile['tried'], '')
            self.assertIn('cq1m', tile['line2'])


class BulkHistoryTests(unittest.TestCase):
    def test_unknown_call_bulk_request_preserves_rich_history_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            rich = {'run': 80, 'hours': 2, 'hours_unrounded': True, 'rec': True,
                    'bands': {'80m': 10}, 'history_contests': ['CQWW']}
            Path(folder, 'runhist5.json').write_text(json.dumps({'S53ZO|CQWW|CW': rich}))
            fetched = threading.Event()
            def fetch(url):
                fetched.set()
                return json.dumps({'S53ZO': 80, 'NEW': 40}).encode()
            with patch('pouncer.runhist.WORKERS', 0):
                rh = RunHistory(folder, server='http://isolated.invalid', fetch=fetch)
                rh.get('NEW', 'CQWWCW', 'CW')
                self.assertTrue(fetched.wait(2))
                self.assertTrue(rh._lists[('CQWW', 'CW')].wait(2))
                self.assertEqual(rh.get('S53ZO', 'CQWWCW', 'CW', band='80m')['run'], 10)
                self.assertEqual(rh.get('S53ZO', 'CQWWCW', 'CW')['hours_unrounded'], True)
                self.assertEqual(rh.get('NEW', 'CQWWCW', 'CW')['run'], 40)
                rh.flush()
                restored = RunHistory(folder)
                self.assertEqual(restored.get('S53ZO', 'CQWWCW', 'CW'), rich)

    def test_warm_and_team_history_changes_advance_revision_only_on_change(self):
        with tempfile.TemporaryDirectory() as folder, patch('pouncer.runhist.WORKERS', 0):
            rh = RunHistory(folder)
            before = rh.revision
            rh.merge({'S53ZO|CQWW|CW': {'run': 10}})
            self.assertGreater(rh.revision, before)
            merged = rh.revision
            rh.merge({'S53ZO|CQWW|CW': {'run': 80}})
            self.assertEqual(rh.revision, merged)
            path = os.path.join(folder, 'warm.sqlite')
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INT, hours REAL, rec INT)')
            con.execute("INSERT INTO warm VALUES ('NEW','CQWW','CW',80,20,0)")
            con.commit(); con.close()
            rh.load_run_list(path)
            self.assertGreater(rh.revision, merged)
            loaded = rh.revision
            rh.load_run_list(path)
            self.assertEqual(rh.revision, loaded)
            rh.flush()


class BrowserChronologyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node needed for production browser sorting')
    def test_actual_browser_time_sort(self):
        result = subprocess.run([shutil.which('node'), 'tests/browser_chronological.cjs'],
                                cwd=Path(__file__).resolve().parents[1], text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
