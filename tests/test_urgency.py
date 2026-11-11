"""Duration/provenance-aware hints and conservative presentation."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import shlex
import tempfile
import unittest

from pouncer.urgency import classify, format_hours
from pouncer.runhist import RunHistory
from tests.test_runhist import cabrillo


def history(run=80, hours=0.8, contest='TTC-SPCWC'):
    return {'run': run, 'hours': hours, 'history_contests': [contest], 'hours_unrounded': True}


class UrgencyTests(unittest.TestCase):
    def test_full_short_contest_is_not_automatically_urgent(self):
        for contest in ('TTC-SPCWC', 'UDC_TTC-SPCWC', 'MINI40', 'MINI80', 'MINITESTCW', 'OK1WC', 'UDC_OK1WC'):
            info = history(contest=contest)
            result = classify(info, contest)
            self.assertFalse(result['rare'], contest)
            self.assertEqual(result['rare_hours_limit'], 0.25)
            self.assertEqual(result['hours_fraction'], 0.8)

    def test_short_participation_threshold_is_strict(self):
        for hours, flagged in ((0, True), (0.2, True), (0.25, False), (0.3, False), (1, False)):
            with self.subTest(hours=hours):
                result = classify(history(hours=hours), 'TTC-SPCWC')
                self.assertEqual(result['rare'], flagged)

    def test_rounded_legacy_hours_cannot_drive_either_priority_threshold(self):
        for marker in (None, False, 'true', 1):
            info = {**history(hours=0.2), 'hours_unrounded': marker}
            result = classify(info, 'TTC-SPCWC')
            self.assertFalse(result['rare'])
            self.assertEqual(result['urgency'], 'normal')
            self.assertIsNone(result['rare_hours_limit'])
            self.assertIsNone(result['hours_fraction'])
            self.assertEqual(result['hours_text'], '~0.2h')
            self.assertIn('rounded', result['urgency_note'])
            self.assertEqual(result['urgency_confidence'], 'limited')
            self.assertTrue(classify({**info, 'run': 20}, 'TTC-SPCWC')['rare'])

    def test_long_contests_keep_six_hour_cap(self):
        for name, archive in [('CQWWCW', 'CQWW'), ('CQWPXSSB', 'CQWPX'), ('ARRLDXCW', 'ARRL')]:
            self.assertTrue(classify(history(hours=5.9, contest=archive), name)['rare'])
            result = classify(history(hours=6, contest=archive), name)
            self.assertFalse(result['rare'])
            self.assertEqual(result['rare_hours_limit'], 6)

    def test_unknown_and_fallback_history_do_not_use_hours(self):
        for info, contest in [(history(hours=0.1, contest='CQWW'), 'TTC-SPCWC'),
                              ({'run': 80, 'hours': 0.1}, 'CQWWCW'),
                              (history(hours=0.1, contest='OTHER'), 'OTHER'),
                              ({**history(hours=0.1), 'history_contests': ['TTC-SPCWC', 'CQWW']}, 'TTC-SPCWC')]:
            result = classify(info, contest)
            self.assertFalse(result['rare'])
            self.assertIsNone(result['rare_hours_limit'])
            self.assertIn('unavailable', result['urgency_note'])
        self.assertTrue(classify({'run': 20, 'hours': 0.1}, 'UNKNOWN')['rare'])

    def test_explicit_duration_override_still_requires_matching_history(self):
        result = classify(history(hours=0.2, contest='CUSTOM'), 'CUSTOM', 60)
        self.assertTrue(result['rare'])
        self.assertEqual(result['rare_hours_limit'], 0.25)
        self.assertFalse(classify(history(hours=0.2, contest='CQWW'), 'CUSTOM', 60)['rare'])
        self.assertEqual(classify(history(hours=0.2, contest='CUSTOM'), 'CUSTOM', 120)['rare_hours_limit'], 0.5)

    def test_missing_or_invalid_evidence_is_not_confidence(self):
        for info in ({}, {'run': None, 'hours': None}, {'run': float('nan'), 'hours': float('inf')},
                     {'run': -1, 'hours': -1}, {'run': 101, 'hours': True}):
            self.assertFalse(classify(info, 'TTC-SPCWC')['rare'])
        self.assertIsNone(classify(history(hours=2), 'TTC-SPCWC')['rare_hours_limit'])
        self.assertEqual(classify(history(run=29, hours=0.8), 'TTC-SPCWC')['rare_reasons'],
                         ['Historically ran for 29% of measurable QSOs (below 30%).'])
        self.assertFalse(classify(history(run=30), 'TTC-SPCWC')['rare'])

    def test_short_hours_use_minutes_and_explanations_are_cautious(self):
        self.assertEqual(format_hours(0.2), '~12m')
        self.assertEqual(format_hours(18.4), '~18h')
        from tests.test_so2r import _import_app
        explain = _import_app().PouncerApp.run_explain
        for info in (history(run=20), history(), {**history(hours=0.1), 'rec': True}):
            result = {**info, **classify(info, 'TTC-SPCWC')}
            text = explain('S53ZO', result, result['rare'])
            self.assertIn('no probability or departure time', text)
            for claim in ('work it now', 'can wait', 'usually stays', "won't last"):
                self.assertNotIn(claim, text)

    def test_app_fields_and_desktop_classification_share_policy(self):
        from types import SimpleNamespace
        from tests.test_so2r import _import_app
        app = _import_app()
        fake = SimpleNamespace(runhist=SimpleNamespace(get=lambda *a, **k: history()),
                               worked=SimpleNamespace(contest_name='TTC-SPCWC'),
                               cfg=SimpleNamespace(urgency_contest_minutes=0))
        s = SimpleNamespace(dxcall='S53ZO', freq_khz=7020, mode='CW')
        fields = app.PouncerApp._run_fields(fake, s)
        self.assertFalse(fields['rare'])
        self.assertEqual(fields['rare_hours_limit'], 0.25)
        self.assertEqual(fields['hours_text'], '~48m')
        self.assertTrue(fields['hours_unrounded'])
        self.assertIn('48 minutes', app.PouncerApp.run_explain('S53ZO', fields, False))
        legacy = {**history(hours=0.2), 'hours_unrounded': False}
        text = app.PouncerApp.run_explain('S53ZO', {**legacy, **classify(legacy, 'TTC-SPCWC')}, False)
        self.assertIn('0.2 hours (rounded)', text)
        self.assertNotIn('12 minutes', text)

    def test_config_override_and_existing_archive_aliases(self):
        from pouncer.config import load_config
        from pouncer.settings import BY_KEY
        self.assertTrue(classify(history(hours=1, contest='OK_OM_DX_Contest'),
                                 'OKOMDX', 24 * 60)['rare'])
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder, 'config.ini')
            for value, expected in [('60', 60), ('-1', 0), ('invalid', 0), ('10081', 10080)]:
                p.write_text('[ui]\nurgency_contest_minutes = ' + value)
                self.assertEqual(load_config(str(p)).urgency_contest_minutes, expected)
        self.assertTrue(BY_KEY['ui.urgency_contest_minutes']['live'])


    def test_duration_setting_refreshes_desktop_and_browser_without_traffic(self):
        from unittest.mock import MagicMock
        from pouncer.config import Config
        from tests.test_so2r import _import_app
        app = _import_app()
        with tempfile.TemporaryDirectory() as folder:
            cfg = Config()
            cfg.config_path = os.path.join(folder, 'config.ini')
            fake = MagicMock()
            fake.cfg = cfg
            fake._dirty = False
            fake._web_built = 123
            result = app.PouncerApp._save_setting(fake, 'ui.urgency_contest_minutes', '60')
            self.assertTrue(result['ok'])
            self.assertEqual(cfg.urgency_contest_minutes, 60)
            self.assertTrue(fake._dirty)
            self.assertEqual(fake._web_built, 0)
            fake._act_on_spot.assert_not_called()



class HistoryContextTests(unittest.TestCase):
    def test_archive_minutes_survive_averaging_classification_cache_and_team(self):
        def log(minutes):
            return '\n'.join(
                f'QSO: 7020.0 CW 2026-11-11 {1700 + i * minutes // 31:04d} '
                'S53ZO 599 001 OK1WC 599 001' for i in range(32))

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'source.sqlite')
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE logs (path TEXT, callsign TEXT, contest TEXT, year INT, mode TEXT)')
            # Every row is selected by the real archive lookup, including the
            # two-log average. No precomputed float bypasses the measurement.
            rows = [(f'{m}.log', f'M{m}', 'CQWW', 2026, 'CW') for m in (14, 15, 16, 29, 30, 31)]
            rows += [('14.log', 'AVERAGE', 'CQWW', 2026, 'CW'),
                     ('16.log', 'AVERAGE', 'CQWW', 2025, 'CW')]
            con.executemany('INSERT INTO logs VALUES (?,?,?,?,?)', rows)
            con.commit(); con.close()
            shard = path.read_bytes()
            rh = RunHistory(os.path.join(folder, 'cache'), base_url='X/', fetch=lambda url:
                            shard if '/SH6/' in url else log(int(Path(url).stem)).encode())
            for minutes in (14, 15, 16, 29, 30, 31):
                with self.subTest(minutes=minutes):
                    info = rh._lookup(f'M{minutes}', 'CQWW', 'CW')
                    self.assertAlmostEqual(info['hours'], minutes / 60)
                    self.assertTrue(info['hours_unrounded'])
                    result = classify(info, 'CQWWCW', 60)
                    self.assertEqual(result['rare'], minutes < 15)
                    self.assertEqual(result['urgency'], 'urgent' if minutes < 15 else
                                     'medium' if minutes < 30 else 'normal')
                    self.assertEqual(result['hours_text'], f'~{minutes}m')
            averaged = rh._lookup('AVERAGE', 'CQWW', 'CW')
            self.assertEqual(averaged['hours'], 0.25)
            self.assertFalse(classify(averaged, 'CQWWCW', 60)['rare'])
            rh._results['AVERAGE|CQWW|CW'] = averaged
            rh._save()
            restored = RunHistory(rh.cache_dir)
            peer = RunHistory(os.path.join(folder, 'peer'))
            peer.merge(rh.snapshot())
            for history_store in (restored, peer):
                result = classify(history_store.get('AVERAGE', 'CQWWCW', 'CW'), 'CQWWCW', 60)
                self.assertFalse(result['rare'])
                self.assertEqual(result['hours_text'], '~15m')

    def test_warmup_upgrades_legacy_cache_with_verified_context(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'runhist5.json').write_text(json.dumps({'S53ZO|CQWW|CW': {'run': 80, 'hours': 1}}))
            path = os.path.join(folder, 'warm.sqlite')
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INT, hours REAL, rec INT)')
            con.execute("INSERT INTO warm VALUES ('S53ZO','CQWW','CW',80,20,0)")
            con.commit(); con.close()
            rh = RunHistory(folder, run_list=path)
            info = rh.get('S53ZO', 'CQWWCW', 'CW')
            self.assertEqual(info['history_contests'], ['CQWW'])
            self.assertEqual(info['hours'], 20)
            self.assertFalse(classify(info, 'CQWWCW')['rare'])

    def test_archive_fallback_context_survives_cache_and_team(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'source.sqlite')
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE logs (path TEXT, callsign TEXT, contest TEXT, year INT, mode TEXT)')
            con.executemany('INSERT INTO logs VALUES (?,?,?,?,?)',
                            [('a.log', 'MATCH', 'CQWW', 2026, 'CW'),
                             ('b.log', 'OTHER', 'CQWPX', 2026, 'CW')])
            con.commit(); con.close()
            shard = Path(path).read_bytes()
            rh = RunHistory(folder, base_url='X/', fetch=lambda url: shard if '/SH6/' in url else
                            cabrillo([14025.0] * 40).encode())
            matching = rh._lookup('MATCH', 'CQWW', 'CW')
            fallback = rh._lookup('OTHER', 'CQWW', 'CW')
            self.assertEqual(matching['history_contests'], ['CQWW'])
            self.assertEqual(fallback['history_contests'], ['CQWPX'])
            self.assertIsNone(classify(fallback, 'CQWWCW')['rare_hours_limit'])
            rh._results['MATCH|CQWW|CW'] = matching
            rh._save()
            restored = RunHistory(folder)
            self.assertEqual(restored.get('MATCH', 'CQWWCW', 'CW')['history_contests'], ['CQWW'])
            peer = RunHistory(os.path.join(folder, 'peer'))
            peer.merge(rh.snapshot())
            self.assertEqual(peer.get('MATCH', 'CQWWCW', 'CW')['history_contests'], ['CQWW'])


class BrowserUrgencyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for browser policy tests')
    def test_browser_uses_server_decision_and_duration_aware_sort(self):
        result = subprocess.run([shutil.which('node'), 'tests/browser_urgency.cjs'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ServerPackagingTests(unittest.TestCase):
    def test_runlist_imports_from_docker_copy_layout(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            for line in (root / 'server/Dockerfile').read_text().splitlines():
                if line.startswith('COPY '):
                    fields = shlex.split(line)
                    dest = target / fields[-1]
                    dest.mkdir(parents=True, exist_ok=True)
                    for source in fields[1:-1]:
                        shutil.copy2(root / source, dest / Path(source).name)
            result = subprocess.run([sys.executable, 'server/runlist.py', '--help'],
                                    cwd=folder, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('build,serve', result.stdout)
