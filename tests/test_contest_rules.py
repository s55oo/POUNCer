"""Weekly sessions and packet/database reconciliation, with a deterministic clock."""
import os
import shutil
import subprocess
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import MagicMock
from dataclasses import replace

from pouncer.contest_rules import (Contact, SessionLog, PROFILES, profile_for,
                                   suffix_character, utc_timestamp)
from pouncer.n1mm import RadioState, parse_app_info
from pouncer.worked import WorkedLog


def ts(value):
    return utc_timestamp(value)


class ProfileTests(unittest.TestCase):
    def test_exact_detection_and_override(self):
        for name, expected in [('TTC-SPCWC', 'ttc'), ('UDC_TTC-SPCWC', 'ttc'),
                               ('UDC_OK1WC', 'mwc'), ('OK1WC', 'mwc'), ('MINITESTCW', 'mini80')]:
            self.assertEqual(profile_for(name), PROFILES[expected])
        for name in ('MINI40', 'MINI80', 'MWC', 'CQWWCW', 'MY-TTC', 'RCWCMINI'):
            self.assertIsNone(profile_for(name))
            self.assertEqual(profile_for(name, 'mini40'), PROFILES['mini40'])
        self.assertIsNone(profile_for('TTC-SPCWC', 'off'))

    def test_all_boundaries_and_exact_end(self):
        for key, date in [('ttc', '2026-11-10'), ('mini40', '2026-11-11'),
                          ('mini80', '2026-11-11'), ('mwc', '2026-11-09')]:
            p = PROFILES[key]
            start = ts(f'{date} {p.minute // 60:02d}:{p.minute % 60:02d}:00')
            with self.subTest(profile=key):
                self.assertFalse(p.session(start - 1).active)
                for i in range(3600 // p.period_seconds):
                    boundary = start + i * p.period_seconds
                    self.assertEqual(p.session(boundary).period, i)
                    self.assertEqual(p.session(boundary + 1).period, i)
                    if i:
                        self.assertEqual(p.session(boundary - 1).period, i - 1)
                self.assertTrue(p.session(start + 3599).active)
                self.assertFalse(p.session(start + 3600).active)
                self.assertFalse(p.session(start + 3601).active)
                self.assertNotEqual(p.session(start).start, p.session(start + 7 * 86400).start)
                self.assertIn('outside session', p.session(start + 3600).status())

    def test_suffix_and_timestamp(self):
        for call, expected in [('OK1WC', 'C'), ('DL/OK1WC/P', 'C'), ('S53ZO/7', 'O'), ('EA8/C4Z/P', 'Z'), ('3Z0', '0')]:
            self.assertEqual(suffix_character(call), expected)
        self.assertEqual(ts('2026-11-10T19:30:00Z'), ts('2026-11-10 19:30:00'))
        self.assertIsNone(ts('broken'))


class PacketReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.log = SessionLog()
        self.a = Contact('S53ZO', '40m', 'CW', ts('2026-11-10 19:35:00'), 'a')
        self.b = replace(self.a, guid='b', timestamp=ts('2026-11-10 19:46:00'))

    def test_repeats_edit_delete_and_stale_database(self):
        self.log.reconcile([self.a, self.b])
        self.log.event('contactdelete', replace(self.a, mode=''))
        self.log.reconcile([self.a, self.b])  # pre-delete background read
        self.assertEqual(list(self.log.records.values()), [self.b])
        edited = replace(self.b, timestamp=ts('2026-11-10 20:01:00'))
        self.log.event('contactreplace', edited)
        self.log.reconcile([self.b])  # delete confirmed, edit not yet visible
        self.assertEqual(list(self.log.records.values()), [edited])
        self.log.reconcile([edited])
        self.assertFalse(self.log.pending)
        self.assertFalse(self.log.event('contactinfo', self.b))  # delayed creation
        self.assertFalse(self.log.event('contactdelete', self.b))  # delayed pre-edit delete
        self.assertEqual(list(self.log.records.values()), [edited])
        self.assertFalse(self.log.snapshot(PROFILES['ttc'].session(ts('2026-11-10 19:46:00')))[0])
        self.assertTrue(self.log.snapshot(PROFILES['ttc'].session(ts('2026-11-10 20:02:00')))[0])

    def test_add_during_read_delayed_packet_and_duplicate(self):
        self.log.reconcile([self.b])
        self.log.event('contactinfo', self.a)  # old period, arrives after b
        self.log.event('contactinfo', self.a)
        self.log.reconcile([self.b])
        keys, rows, mults = self.log.snapshot(PROFILES['ttc'].session(ts('2026-11-10 19:47:00')))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(keys), 1)
        self.assertEqual(mults, {('*', 'S53ZO')})
        self.log.reconcile([self.a, self.b])
        self.assertFalse(self.log.pending)

    def test_idless_edit_oldtimestamp_and_exact_delete(self):
        a, b = replace(self.a, guid=''), replace(self.b, guid='')
        self.log.reconcile([a, b])
        edited = replace(a, call='S55OO', timestamp=ts('2026-11-10 19:50:00'))
        self.log.event('contactreplace', edited, a.call, a.timestamp)
        self.log.reconcile([a, b])
        self.assertEqual(set(self.log.records.values()), {b, edited})
        self.log.event('contactdelete', replace(b, mode=''))
        self.assertEqual(set(self.log.records.values()), {edited})

    def test_missing_timestamp_never_uses_arrival_time(self):
        self.log.event('contactinfo', replace(self.a, timestamp=None))
        self.assertFalse(self.log.records)
        self.assertIn('timestamp missing', self.log.warning)

    def test_five_second_grace_then_authoritative_database_recovery(self):
        log = SessionLog(clock=lambda: 10)
        log.reconcile([self.a])
        edited = replace(self.a, call='S55OO')
        log.event('contactreplace', edited)
        log.reconcile([self.a], read_started=9)  # stale pre-packet snapshot
        self.assertEqual(list(log.records.values()), [edited])
        log.reconcile([self.a], read_started=14.9)
        self.assertEqual(list(log.records.values()), [edited])
        log.reconcile([self.a], read_started=15)
        self.assertEqual(list(log.records.values()), [self.a])
        self.assertFalse(log.pending)

    def test_idless_creation_is_acknowledged_by_database_identity(self):
        self.log.event('contactinfo', replace(self.a, guid=''))
        self.log.reconcile([self.a])
        self.assertFalse(self.log.pending)
        self.assertEqual(list(self.log.records.values()), [self.a])
        # Distinct GUIDs still represent distinct rows, even with identical times.
        second = replace(self.a, guid='other')
        self.log.event('contactinfo', second)
        self.assertEqual(len(self.log.records), 2)

    def test_session_mults_persist_across_every_repeat_period(self):
        for name, timestamp in [('ttc', '2026-11-10 19:30:00'),
                                ('mini40', '2026-11-11 17:00:00'),
                                ('mini80', '2026-11-11 18:00:00')]:
            p = PROFILES[name]
            start = ts(timestamp)
            log = SessionLog()
            log.reconcile([Contact('S53ZO', p.bands[0], 'CW', start + 1, 'a')])
            for i in range(3600 // p.period_seconds):
                keys, rows, mults = log.snapshot(p.session(start + i * p.period_seconds + 2))
                self.assertEqual(bool(keys), i == 0)
                self.assertEqual(len(rows), 1)
                self.assertEqual(mults, {('*', 'S53ZO')})


    def test_pre_creation_read_cannot_acknowledge_later_delete(self):
        log = SessionLog(clock=lambda: 10)
        log.event('contactinfo', self.a)
        log.event('contactdelete', self.a)
        log.reconcile([], read_started=9)
        self.assertTrue(log.pending)
        log.reconcile([self.a], read_started=11)
        self.assertFalse(log.records)
        log.reconcile([], read_started=12)
        self.assertFalse(log.pending)


    def test_late_delete_of_original_band_keeps_corrected_contact(self):
        self.log.reconcile([self.a])
        corrected = replace(self.a, band='80m')
        self.log.event('contactreplace', corrected)
        self.assertFalse(self.log.event('contactdelete', replace(self.a, mode='')))
        self.assertEqual(list(self.log.records.values()), [corrected])
        keys, rows, mults = self.log.snapshot(PROFILES['ttc'].session(self.a.timestamp + 1))
        self.assertEqual(keys, {('S53ZO', '80m', 'CW')})
        self.assertEqual(len(rows), 1)
        self.assertEqual(mults, {('*', 'S53ZO')})

    def test_idless_replay_after_confirmed_edit_cannot_resurrect_original(self):
        for guid in ('', 'row:1', 'guid-a'):
            with self.subTest(database_identity=guid):
                log = SessionLog(clock=lambda: 10)
                original = replace(self.a, guid=guid)
                corrected = replace(original, call='S55OO', timestamp=original.timestamp + 60)
                log.reconcile([original])
                self.assertTrue(log.event('contactreplace', replace(corrected, guid=''),
                                          original.call, original.timestamp))
                log.reconcile([corrected], read_started=11)
                self.assertFalse(log.pending)
                self.assertFalse(log.event('contactinfo', replace(original, guid='')))
                self.assertEqual(list(log.records.values()), [corrected])
                # A real repeat with a different QSO time is still accepted.
                repeat = replace(original, guid='', timestamp=original.timestamp + 900)
                self.assertTrue(log.event('contactinfo', repeat))
                self.assertEqual(len(log.records), 2)



class WorkedSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'test.s3db')
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('CREATE TABLE DXLOG (ID TEXT, TS TEXT, Call TEXT, Band REAL, Mode TEXT, ContestNR INT)')
        self.now = ts('2026-11-10 19:35:30')
        self.state = RadioState()
        self.state.set_app({'kind': 'AppInfo', 'dbname': self.db, 'contest_nr': 1,
                            'contest_name': 'TTC-SPCWC'})
        self.w = self.new_reader()

    def tearDown(self):
        self.tmp.cleanup()

    def new_reader(self, profile='auto'):
        w = WorkedLog(self.db, clock=lambda: self.now, profile=profile)
        w.app_info = self.state.app
        w.contact_events = self.state.contact_events
        return w

    def insert(self, guid, timestamp, call='S53ZO', band=7, nr=1):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('INSERT INTO DXLOG VALUES (?, ?, ?, ?, ?, ?)',
                        (guid, timestamp, call, band, 'CW', nr))

    def packet(self, kind, guid, timestamp, call='S53ZO', band='7'):
        self.state.set_app(parse_app_info(f'<{kind}><contestnr>1</contestnr><contestname>TTC-SPCWC</contestname>'
            f'<ID>{guid}</ID><timestamp>{timestamp}</timestamp><call>{call}</call>'
            f'<band>{band}</band><mode>CW</mode></{kind}>'.encode()))

    def test_boundary_without_new_traffic_and_whole_session_mults(self):
        self.insert('a', '2026-11-10 19:35:00')
        self.insert('old', '2026-11-03 19:50:00', 'OLD')
        self.w.maybe_reload()
        self.assertTrue(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.assertFalse(self.w.is_worked('S53ZO', 3520, 'CW'))
        self.assertEqual(self.w.qso_count, 1)
        self.assertEqual(self.w.new_mults(3520, None, None, None, 'S53ZO', mode='CW'), 0)
        self.assertEqual(self.w.new_mults(3520, None, None, None, 'S55OO', mode='CW'), 1)
        # Force database signature and reload timer stable: only the clock moves.
        self.w._loaded_at = self.now
        self.w._last_check = self.now
        self.now = ts('2026-11-10 19:45:00')
        self.w._loaded_at = self.now
        self.w._last_check = self.now
        self.assertTrue(self.w.maybe_reload())
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.assertEqual(self.w.qso_count, 1)
        self.assertIn('period 2/4', self.w.session_text)
        self.assertEqual(self.w.new_mults(7020, None, None, None, 'S53ZO'), 0)

    def test_udp_edit_delete_and_database_reconciliation(self):
        self.insert('a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        self.now = ts('2026-11-10 19:46:30')
        self.packet('contactinfo', 'b', '2026-11-10 19:46:00')
        self.w.maybe_reload()
        self.assertTrue(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.assertEqual(self.w.qso_count, 2)
        self.packet('contactdelete', 'a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        self.assertTrue(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.packet('contactreplace', 'b', '2026-11-10 19:44:00')
        self.w.maybe_reload()
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("DELETE FROM DXLOG WHERE ID='a'")
            con.execute("INSERT INTO DXLOG VALUES ('b', '2026-11-10 19:44:00', 'S53ZO', 7, 'CW', 1)")
        self.w.maybe_reload()
        self.assertEqual(self.w.qso_count, 1)
        self.assertFalse(self.w._session_log.pending)
        restored = self.new_reader()
        restored.maybe_reload()
        self.assertEqual(restored.qso_count, 1)
        self.assertFalse(restored.is_worked('S53ZO', 7020, 'CW'))

    def test_restart_clock_changes_and_weekly_round(self):
        self.insert('a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        other = self.new_reader()
        other.maybe_reload()
        self.assertTrue(other.is_worked('S53ZO', 7020, 'CW'))
        self.now = ts('2026-11-10 20:00:00')
        self.w.maybe_reload()
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.now = ts('2026-11-10 19:36:00')  # clock moves back
        self.w.maybe_reload()
        self.assertTrue(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.now = ts('2026-11-17 19:35:30')
        self.w.maybe_reload()
        self.assertEqual(self.w.qso_count, 0)
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))

    def test_mwc_suffix_scopes_no_period_reset(self):
        self.now = ts('2026-11-09 16:35:00')
        self.w = self.new_reader('mwc')
        self.insert('a', '2026-11-09 16:34:00', 'OK1WC')
        self.w.maybe_reload()
        self.now = ts('2026-11-09 17:15:00')
        self.w.maybe_reload()
        self.assertTrue(self.w.is_worked('OK1WC', 7020, 'CW'))
        self.assertEqual(self.w.new_mults(7020, None, None, None, 'OK2ABC'), 0)
        self.assertEqual(self.w.new_mults(3520, None, None, None, 'OK2ABC'), 1)
        self.assertEqual(self.w.new_mults(7020, None, None, None, '3Z0'), 1)

    def test_mini_profiles_and_ordinary_regression(self):
        for profile, start, band in [('mini40', '17', 7), ('mini80', '18', 3.5)]:
            self.now = ts(f'2026-11-11 {start}:05:00')
            self.insert(profile, f'2026-11-11 {start}:04:00', band=band)
            w = self.new_reader(profile)
            w.maybe_reload()
            self.assertTrue(w.is_worked('S53ZO', 7020 if band == 7 else 3520, 'CW'))
            self.now = ts(f'2026-11-11 {start}:10:00')
            w.maybe_reload()
            self.assertFalse(w.is_worked('S53ZO', 7020 if band == 7 else 3520, 'CW'))
            self.assertEqual(w.qso_count, 1)
        ordinary = self.new_reader('off')
        ordinary.maybe_reload()
        self.assertTrue(ordinary.is_worked('S53ZO', 7020, 'CW'))
        self.assertTrue(ordinary.is_worked('S53ZO', 3520, 'CW'))

    def test_appinfo_does_not_reset_contact_sequence(self):
        self.packet('contactinfo', 'a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        self.state.set_app({'kind': 'AppInfo', 'dbname': self.db, 'contest_nr': 1,
                            'contest_name': 'TTC-SPCWC'})
        self.packet('contactinfo', 'b', '2026-11-10 19:36:00', call='S55OO')
        self.w.maybe_reload()
        self.assertEqual(self.w.qso_count, 2)


    def test_contest_switch_and_late_packet_does_not_switch_back(self):
        self.insert('a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        self.state.set_app({'kind': 'AppInfo', 'dbname': self.db, 'contest_nr': 2,
                            'contest_name': 'CQWWCW'})
        self.packet('contactinfo', 'b', '2026-11-10 19:36:00')
        self.w.maybe_reload()
        self.assertEqual(self.w.contest_nr, 2)
        self.assertIsNone(self.w.profile)
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.state.set_app({'kind': 'AppInfo', 'dbname': self.db, 'contest_nr': 1,
                            'contest_name': 'TTC-SPCWC'})
        self.w.maybe_reload()
        self.assertTrue(self.w.is_worked('S53ZO', 7020, 'CW'))

    def test_summary_session_mults_and_missing_timestamp(self):
        from pouncer import summary
        self.insert('old', '2026-11-03 19:35:00', 'OLD')
        self.insert('a', '2026-11-10 19:31:00')
        self.insert('b', '2026-11-10 19:32:00', band=3.5)
        self.insert('no-time', '')
        self.w.maybe_reload()
        qsos, slots, session = self.w.session_summary()
        out = summary.build(qsos, [], slots, now=session.now)
        self.assertEqual(out['qsos'], 2)
        self.assertEqual(out['mults'], 1)
        self.assertIn('1 QSOs without UTC timestamp excluded', self.w.session_text)

    def test_desktop_browser_and_combined_view_boundary_refresh(self):
        from tests.test_so2r import _import_app
        from tests.test_minsnr import spot
        app = _import_app()
        self.insert('a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        f = MagicMock()
        f.worked = self.w
        f.radio = 1
        f._dirty = False
        f._web_built = self.now
        f._rate_at = self.now
        f._web_asked = 0
        f._web_views = {(2, '80m,40m'): time.time()}
        f._web_view_cache = {}
        f._web_cache = {}
        f.state.radio_numbers.return_value = [1, 2]
        f.cwspeed = None
        f.cfg.band_lights = False
        f.cfg.cw_speed_popup = False
        f.cfg.min_snr = 0
        f.cfg.hide_beacons = False
        f.cfg.fkeys = []
        f._checks = []
        f._last_tap = None
        f._removed = set()
        f.hide_worked_var.get.return_value = True
        f.own_call.return_value = 'S55OO'
        f._current_band.return_value = '40m'
        f._effective_modes.return_value = ['CW']
        f._mults.side_effect = lambda s: self.w.new_mults(s.freq_khz, None, None, None, s.dxcall, mode=s.mode)
        f._tried_fields.return_value = {}
        f._run_fields.return_value = {}
        station = spot(app, 'S53ZO', 22, freq=7020)
        f.spots = {station.key: station}
        f._visible_spots = app.PouncerApp._visible_spots.__get__(f)
        f._build_web_payload = app.PouncerApp._build_web_payload.__get__(f)
        f._web_due = app.PouncerApp._web_due.__get__(f)
        f.WEB_IDLE_S = app.PouncerApp.WEB_IDLE_S
        f.WEB_IDLE_EVERY_S = app.PouncerApp.WEB_IDLE_EVERY_S
        app.PouncerApp._refresh_web_payloads(f)
        self.assertEqual(f._web_view_cache[(2, '80m,40m')]['spots'], [])
        self.now = ts('2026-11-10 19:45:00')
        # No packet, no spot, and no file mutation at this boundary.
        app.PouncerApp._reload_worked(f)
        self.assertTrue(f._dirty)
        self.assertTrue(f._web_due())
        self.assertEqual(len(f._visible_spots(1)), 1)  # desktop's shared filter
        app.PouncerApp._refresh_web_payloads(f)
        payload = f._web_view_cache[(2, '80m,40m')]
        self.assertEqual(payload['radio'], 2)
        self.assertEqual(payload['spots'][0]['call'], 'S53ZO')
        self.assertFalse(payload['spots'][0]['worked'])
        self.assertEqual(payload['spots'][0]['mult'], 0)
        self.assertIn('period 2/4', payload['session'])
        f.hide_worked_var.get.return_value = False
        self.now = ts('2026-11-10 19:36:00')
        app.PouncerApp._reload_worked(f)
        app.PouncerApp._refresh_web_payloads(f)
        self.assertTrue(f._web_view_cache[(2, '80m,40m')]['spots'][0]['worked'])
        f._web_qsy.assert_not_called()


    def test_background_read_cannot_undo_newer_packet_or_contest_switch(self):
        self.insert('a', '2026-11-10 19:35:00')
        self.w.maybe_reload()
        self.w.background = True
        read, release = threading.Event(), threading.Event()
        real = self.w._read

        def slow(*args):
            result = real(*args)
            read.set()
            release.wait(5)
            return result

        self.w._read = slow
        self.w.request_recheck()
        self.w.maybe_reload()
        self.assertTrue(read.wait(5))
        try:
            self.packet('contactinfo', 'b', '2026-11-10 19:35:20', 'S55OO')
            self.w.maybe_reload()
            self.assertEqual(self.w.qso_count, 2)
        finally:
            release.set()
            self.w._job['thread'].join(5)
        self.w._take_job()
        self.assertEqual(self.w.qso_count, 2)
        self.assertTrue(self.w.is_worked('S55OO', 7020, 'CW'))
        # A completed read of contest 1 is discarded if AppInfo switches to 2.
        self.w._read = real
        self.w.request_recheck()
        self.w.maybe_reload()
        self.w._job['thread'].join(5)
        self.state.set_app({'kind': 'AppInfo', 'dbname': self.db, 'contest_nr': 2,
                            'contest_name': 'CQWWCW'})
        self.assertFalse(self.w._take_job())
        self.w.maybe_reload(wait=True)
        self.assertEqual(self.w.contest_nr, 2)
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))

    def test_config_override_validation_and_restart_setting(self):
        from pouncer.config import load_config
        from pouncer.settings import BY_KEY
        ini = os.path.join(self.tmp.name, 'config.ini')
        for value, expected in [('mini40', 'mini40'), ('TTC', 'ttc'), ('bogus', 'auto')]:
            with open(ini, 'w') as f:
                f.write('[worked]\nprofile = ' + value + '\n')
            self.assertEqual(load_config(ini).worked_profile, expected)
        self.assertFalse(BY_KEY['worked.profile']['live'])

    def test_session_end_and_no_guid_duplicate_rows(self):
        self.insert(None, '2026-11-10 19:35:00')
        self.insert(None, '2026-11-10 19:35:00')
        self.w.maybe_reload()
        self.assertEqual(self.w.qso_count, 2)
        self.now = ts('2026-11-10 20:30:00')
        self.w.maybe_reload()
        self.assertFalse(self.w.is_worked('S53ZO', 7020, 'CW'))
        self.assertEqual(self.w.qso_count, 2)
        self.assertEqual(self.w.new_mults(7020, None, None, None, 'NEW'), 0)
        self.assertIn('outside session', self.w.session_text)

    def test_background_reference_log_ignores_other_database_appinfo(self):
        self.insert('a', '2026-11-10 19:35:00')
        self.state.set_app({'kind': 'AppInfo', 'dbname': 'different.s3db',
                            'contest_nr': 9, 'contest_name': 'CQWWCW'})
        w = self.new_reader()
        w.background = True
        self.assertTrue(w.maybe_reload(wait=True))
        self.assertTrue(w.log_open)
        self.assertEqual(w.contest_nr, 1)
        self.assertEqual(w.qso_count, 1)
        self.assertTrue(w.is_worked('S53ZO', 7020, 'CW'))


class DesktopSessionStatusTests(unittest.TestCase):
    def test_status_visible_in_clean_and_detailed_layout_and_hidden_without_session(self):
        from tests.test_so2r import _import_app
        app = _import_app()
        f = MagicMock()
        f.worked.log_open = True
        f.worked.session_text = 'TTC-SPCWC · period 2/4 · 15:00 to next period'
        label = MagicMock()
        state = {'manager': '', 'text': ''}
        label.winfo_manager.side_effect = lambda: state['manager']
        label.cget.side_effect = lambda key: state[key]
        label.configure.side_effect = lambda **kw: state.update(kw)
        label.pack.side_effect = lambda **kw: state.update(manager='pack')
        label.pack_forget.side_effect = lambda: state.update(manager='')
        f.session_label = label
        app.PouncerApp._update_session_label(f)
        for clean in (True, False, True):
            f._clean.return_value = clean
            app.PouncerApp._apply_layout(f)
            self.assertEqual(state['manager'], 'pack')
            self.assertEqual(state['text'], f.worked.session_text)
        f.worked.session_text = ''
        app.PouncerApp._update_session_label(f)
        self.assertEqual(state['manager'], '')
        f.worked.session_text = 'TTC-SPCWC · period 3/4 · 15:00 to next period'
        app.PouncerApp._update_session_label(f)
        self.assertEqual(state['manager'], 'pack')
        self.assertEqual(state['text'], f.worked.session_text)
        f.worked.log_open = False
        app.PouncerApp._update_session_label(f)
        self.assertEqual(state['manager'], '')


class BrowserProfileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for browser profile tests')
    def test_session_status_and_worked_style(self):
        result = subprocess.run([shutil.which('node'), 'tests/browser_contest_profiles.cjs'],
                                cwd=Path(__file__).resolve().parents[1], timeout=30,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
