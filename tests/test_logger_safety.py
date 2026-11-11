"""Audit regressions: keyboard target, resilient reception and contact identity."""
import os
import socket
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pouncer.n1mm import RadioListener, RadioState, parse_app_info, parse_radio_info
from pouncer.worked import WorkedLog
from tests.test_so2r import _import_app, two_radios


class KeyboardTargetTests(unittest.TestCase):
    def fake(self, focus=True):
        app = _import_app()
        fake = SimpleNamespace(state=two_radios(), radio=2,
            cfg=SimpleNamespace(focus_n1mm=focus, type_mode='keys'),
            status_label=Mock(), cwspeed=Mock(), _cw_win=7777, _cw_radio=2,
            _cw_error='', _cw_error_n=0, _cw_pumping=False,
            root=Mock(), _filter_width='wide', _filter_cat=lambda width: '')
        for name in ('_radio_nr', '_so2r', '_send_key', '_settext', '_cw', '_cw_open',
                     '_cw_ready', '_cw_close', '_cw_fail', '_cw_pump'):
            method = getattr(app.PouncerApp, name)
            setattr(fake, name, method if name == '_send_key' else method.__get__(fake))
        return app, fake

    def test_transmit_log_spot_and_speed_keys_abort_if_focus_fails(self):
        for key in ('F1', 'Enter', 'SpotIt', 'PgUp', 'PgDn'):
            app, fake = self.fake()
            with self.subTest(key=key), patch.object(app, 'force_foreground', return_value=False), \
                    patch.object(app.win32mod, 'foreground_window', return_value=1111), \
                    patch.object(app.PouncerApp, '_send_key', return_value=True) as send:
                fake._send_key = send
                app.PouncerApp._press_key(fake, key, 2)
                send.assert_not_called()
                fake.cwspeed.expect.assert_not_called()

    def test_no_forced_focus_mode_requires_correct_existing_foreground(self):
        for foreground, sent in ((1111, False), (2222, True)):
            app, fake = self.fake(focus=False)
            fake._send_key = Mock(return_value=True)
            with self.subTest(foreground=foreground), patch.object(app, 'force_foreground') as focus, \
                    patch.object(app.win32mod, 'foreground_window', return_value=foreground):
                app.PouncerApp._press_key(fake, 'F1', 2)
                self.assertEqual(fake._send_key.called, sent)
                focus.assert_not_called()

    def test_missing_target_and_false_positive_focus_never_send(self):
        for hwnd in (None, 2222):
            app, fake = self.fake()
            fake.state.entry_window_hwnd = lambda nr: hwnd
            fake._send_key = Mock(return_value=True)
            with self.subTest(hwnd=hwnd), patch.object(app, 'force_foreground', return_value=True), \
                    patch.object(app.win32mod, 'foreground_window', return_value=1111), \
                    patch.object(app.time, 'sleep'):
                app.PouncerApp._press_key(fake, 'Enter', 2)
                fake._send_key.assert_not_called()

    def test_cw_close_and_text_do_not_send_after_focus_failure(self):
        for op in ('done', 'stop', 'text', 'back'):
            app, fake = self.fake()
            with self.subTest(op=op), patch.object(app.win32mod, 'is_window', return_value=True), \
                    patch.object(app.win32mod, 'foreground_window', return_value=1111), \
                    patch.object(app, 'force_foreground', return_value=False), \
                    patch.object(app, 'send_enter') as enter, \
                    patch.object(app.win32mod, 'send_escape') as escape, \
                    patch.object(app, 'type_text') as text, \
                    patch.object(app.win32mod, 'send_backspace') as back, \
                    patch.object(app.time, 'sleep'):
                fake._cw(op, 'CQ', 1, 2)
                for send in (enter, escape, text, back):
                    send.assert_not_called()

    def test_filter_and_fallback_text_require_target(self):
        app, fake = self.fake(focus=False)
        with patch.object(app.win32mod, 'foreground_window', return_value=1111), \
                patch.object(app, 'type_text') as text, patch.object(app, 'clear_focused_edit') as clear, \
                patch.object(app, 'send_filter_toggle') as toggle:
            app.PouncerApp._set_filter(fake, 'narrow')
            app.PouncerApp._enter_text(fake, 2222, 'K1ABC')
            for send in (text, clear, toggle):
                send.assert_not_called()

    def test_cw_open_and_close_respect_no_forced_focus_choice(self):
        app, fake = self.fake(focus=False)
        fake._cw_win = None
        foreground = [2222]
        with patch.object(app.win32mod, 'foreground_window', side_effect=lambda: foreground[0]), \
                patch.object(app, 'force_foreground') as focus, \
                patch.object(app.win32mod, 'send_ctrl_k', side_effect=lambda: foreground.__setitem__(0, 7777)), \
                patch.object(app, 'window_pid', return_value=10), \
                patch.object(app.win32mod, 'is_window', return_value=True), \
                patch.object(app.win32mod, 'focused_multiline_edit', return_value=8888), \
                patch.object(app, 'send_enter') as enter, patch.object(app.time, 'sleep'):
            self.assertTrue(fake._cw_open(2))
            fake._cw_close(False)
            enter.assert_called_once()
            focus.assert_not_called()

    def test_ctrl_k_race_to_other_entry_or_unrelated_dialog_never_sends_cw(self):
        for candidate in (1111, 9999):
            app, fake = self.fake()
            fake._cw_win = None
            foreground = [2222]
            with self.subTest(candidate=candidate), \
                    patch.object(app.win32mod, 'foreground_window', side_effect=lambda: foreground[0]), \
                    patch.object(app.win32mod, 'send_ctrl_k', side_effect=lambda: foreground.__setitem__(0, candidate)), \
                    patch.object(app, 'force_foreground', side_effect=lambda h: foreground.__setitem__(0, h) or True), \
                    patch.object(app, 'window_pid', return_value=10), \
                    patch.object(app.win32mod, 'is_window', return_value=True), \
                    patch.object(app, 'send_enter') as enter, \
                    patch.object(app, 'type_text') as text, patch.object(app.time, 'sleep'):
                self.assertFalse(fake._cw_open(2))
                fake._cw('text', 'CQ', radio=2)
                fake._cw('done', radio=2)
                text.assert_not_called()
                enter.assert_not_called()

    def test_native_cw_window_close_allows_verified_reopening(self):
        app, fake = self.fake()
        foreground = [2222]
        with patch.object(app.win32mod, 'foreground_window', side_effect=lambda: foreground[0]), \
                patch.object(app.win32mod, 'is_window', side_effect=lambda h: h != 7777), \
                patch.object(app.win32mod, 'send_ctrl_k', side_effect=lambda: foreground.__setitem__(0, 8888)) as ctrl_k, \
                patch.object(app.win32mod, 'focused_multiline_edit', side_effect=lambda h: 9999 if h == 8888 else None), \
                patch.object(app, 'window_pid', return_value=10), patch.object(app.time, 'sleep'):
            self.assertTrue(fake._cw_open(2))
            self.assertEqual(fake._cw_win, 8888)
            ctrl_k.assert_called_once()



class ReceiverSafetyTests(unittest.TestCase):
    def test_incomplete_appinfo_cannot_block_startup_or_erase_confirmed_selection(self):
        for invalid in (None, 'not-a-number', True):
            state = RadioState()
            state.set_app({'kind': 'AppInfo', 'dbname': 'unknown.s3db',
                           'contest_nr': invalid, 'contest_name': ''})
            state.set_app({'kind': 'contactinfo', 'contest_nr': 1, 'contest_name': 'CQWWCW'})
            self.assertEqual((state.app()['contest_nr'], state.app()['contest_name']), (1, 'CQWWCW'))
            state.set_app({'kind': 'AppInfo', 'dbname': 'current.s3db',
                           'contest_nr': 2, 'contest_name': 'CQWPXCW'})
            state.set_app({'kind': 'AppInfo', 'dbname': 'unknown.s3db',
                           'contest_nr': invalid, 'contest_name': ''})
            self.assertEqual((state.app()['contest_nr'], state.app()['contest_name']), (2, 'CQWPXCW'))
        state = RadioState()
        listener = RadioListener('127.0.0.1', 0, state)
        listener._handle_packet(b'<AppInfo><dbname>unknown.s3db</dbname></AppInfo>')
        self.assertIsNone(state.app())
        listener._handle_packet(b'<contactinfo><contestnr>1</contestnr><contestname>CQWWCW</contestname></contactinfo>')
        self.assertEqual(state.app()['contest_nr'], 1)

    def test_confirmed_name_spelling_and_udc_alias_accept_legitimate_events(self):
        state = RadioState()
        state.set_app({'kind': 'AppInfo', 'dbname': 'custom.s3db', 'contest_nr': 1,
                       'contest_name': 'UDC_OK1WC'})
        state.set_app({'kind': 'contactinfo', 'contest_nr': 1, 'contest_name': ' ok1wc '})
        self.assertEqual(state.app()['contest_name'], 'UDC_OK1WC')
        self.assertEqual(len(state.contact_events()), 1)

    def test_same_contest_number_cannot_replace_appinfo_contest_name(self):
        for kind in ('contactinfo', 'contactreplace', 'lookupinfo'):
            state = RadioState()
            state.set_app({'kind': 'AppInfo', 'dbname': 'new-db.s3db',
                           'contest_nr': 1, 'contest_name': 'CQWPXCW'})
            state.set_app({'kind': kind, 'contest_nr': 1, 'contest_name': 'CQWWCW'})
            self.assertEqual(state.app()['contest_name'], 'CQWPXCW')
            self.assertEqual(state.contact_events(), [])

    def test_bad_encoding_is_ignored_by_parsers(self):
        data = b'<?xml version="1.0" encoding="unknown-encoding"?><RadioInfo/>'
        self.assertIsNone(parse_radio_info(data))
        self.assertIsNone(parse_app_info(data))

    def test_real_receiver_survives_bad_encoding_then_delivers_valid_packet(self):
        state, received = RadioState(), threading.Event()
        updates = []
        def on_update(info):
            updates.append(info)
            if len(updates) == 1:
                raise ValueError('simulated rejected callback input')
            received.set()
        listener = RadioListener('127.0.0.1', 0, state, on_update=on_update)
        listener.start()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            end = time.monotonic() + 2
            while listener._sock is None and time.monotonic() < end:
                time.sleep(.005)
            self.assertIsNotNone(listener._sock)
            address = listener._sock.getsockname()
            with self.assertLogs('pouncer.n1mm', level='ERROR') as errors:
                sender.sendto(b'<?xml version="1.0" encoding="unknown-encoding"?><RadioInfo/>', address)
                sender.sendto(b'<RadioInfo><RadioNr>1</RadioNr><Freq>1402500</Freq><Mode>CW</Mode></RadioInfo>', address)
                sender.sendto(b'<RadioInfo><RadioNr>1</RadioNr><Freq>1402600</Freq><Mode>CW</Mode></RadioInfo>', address)
                self.assertTrue(received.wait(2))
            self.assertIn('simulated rejected callback input', errors.output[0])
            self.assertEqual(state.snapshot(1)['freq_khz'], 14026)
        finally:
            listener.stop()
            sender.close()
            # Thread._stop must remain callable so normal join/is_alive work.
            listener.join(2)
        self.assertFalse(listener.is_alive())

    def test_appinfo_authoritative_for_all_late_contact_kinds(self):
        for kind in ('contactinfo', 'contactreplace', 'contactdelete', 'lookupinfo'):
            state = RadioState()
            state.set_app({'kind': 'AppInfo', 'dbname': '', 'contest_nr': 2,
                           'contest_name': 'CQWPXCW'})
            state.set_app({'kind': kind, 'contest_nr': 1, 'contest_name': 'CQWWCW'})
            self.assertEqual((state.app()['contest_nr'], state.app()['contest_name']), (2, 'CQWPXCW'))

    def test_contact_startup_fallback_and_same_contest_blank_name(self):
        state = RadioState()
        state.set_app({'kind': 'contactinfo', 'contest_nr': 1, 'contest_name': 'CQWWCW'})
        self.assertEqual(state.app()['contest_nr'], 1)
        state.set_app({'kind': 'AppInfo', 'dbname': 'test.s3db', 'contest_nr': 2,
                       'contest_name': 'CQWPXCW'})
        state.set_app({'kind': 'contactdelete', 'contest_nr': 2, 'contest_name': ''})
        self.assertEqual((state.app()['contest_nr'], state.app()['contest_name']), (2, 'CQWPXCW'))
        self.assertEqual(state.app()['contacts'], 2)


class OrdinaryContactTests(unittest.TestCase):
    def test_queued_old_database_contacts_cannot_cross_same_number_switch(self):
        for old_name in ('CQWWCW', 'CQWPXCW'):
            with self.subTest(old_name=old_name), tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, 'new-db.s3db')
                con = sqlite3.connect(path)
                con.execute('CREATE TABLE DXLOG (ID TEXT, TS TEXT, Call TEXT, Band REAL, Mode TEXT, ContestNR INT)')
                con.commit(); con.close()
                state = RadioState()
                state.set_app({'kind': 'AppInfo', 'dbname': 'old-db.s3db',
                               'contest_nr': 1, 'contest_name': old_name})
                state.set_app(parse_app_info(f'<contactinfo><contestnr>1</contestnr><contestname>{old_name}</contestname>'
                    '<ID>old</ID><call>OLD</call><band>14</band><mode>CW</mode>'
                    '<timestamp>2026-11-11 12:00:00</timestamp></contactinfo>'.encode()))
                state.set_app({'kind': 'AppInfo', 'dbname': path, 'contest_nr': 1, 'contest_name': 'CQWPXCW'})
                worked = WorkedLog(path)
                worked.app_info, worked.contact_events = state.app, state.contact_events
                worked.maybe_reload()
                self.assertFalse(worked.is_worked('OLD', 14025, 'CW'))
                state.set_app(parse_app_info(b'<contactinfo><contestnr>1</contestnr><contestname>cqwpXCW</contestname>'
                    b'<ID>current</ID><call>GOOD</call><band>14</band><mode>CW</mode>'
                    b'<timestamp>2026-11-11 12:01:00</timestamp></contactinfo>'))
                worked.maybe_reload()
                self.assertTrue(worked.is_worked('GOOD', 14025, 'CW'))

    def test_duplicate_and_other_mode_survive_delete_before_and_after_database_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'test.s3db')
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE DXLOG (ID TEXT, TS TEXT, Call TEXT, Band REAL, Mode TEXT, ContestNR INT)')
            con.executemany('INSERT INTO DXLOG VALUES (?,?,?,?,?,?)', [
                ('a', '2026-11-11 12:00:00', 'K1ABC', 14, 'CW', 1),
                ('b', '2026-11-11 12:01:00', 'K1ABC', 14, 'CW', 1),
                ('c', '2026-11-11 12:02:00', 'K1ABC', 14, 'USB', 1)])
            con.commit()
            state = RadioState()
            state.set_app({'kind': 'AppInfo', 'dbname': path, 'contest_nr': 1, 'contest_name': 'CQWWCW'})
            worked = WorkedLog(path)
            worked.app_info, worked.contact_events = state.app, state.contact_events
            worked.maybe_reload()
            def delete(guid, minute):
                state.set_app(parse_app_info(f'<contactdelete><contestnr>1</contestnr><ID>{guid}</ID>'
                    f'<timestamp>2026-11-11 12:{minute}:00</timestamp><call>K1ABC</call><band>14</band></contactdelete>'.encode()))
                worked.maybe_reload()
            delete('a', '00')  # database snapshot still contains the deleted row
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'CW'))
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'USB'))
            con.execute("DELETE FROM DXLOG WHERE ID='a'"); con.commit()
            worked.request_recheck(); worked.maybe_reload()
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'CW'))
            delete('b', '01')
            self.assertFalse(worked.is_worked('K1ABC', 14025, 'CW'))
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'LSB'))
            con.execute("DELETE FROM DXLOG WHERE ID='b'"); con.commit()
            worked.request_recheck(); worked.maybe_reload()
            self.assertFalse(worked.is_worked('K1ABC', 14025, 'CW'))
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'LSB'))
            con.close()

    def test_edit_replays_over_pre_packet_read_without_hiding_other_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'test.s3db')
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE DXLOG (ID TEXT, TS TEXT, Call TEXT, Band REAL, Mode TEXT, ContestNR INT)')
            con.executemany('INSERT INTO DXLOG VALUES (?,?,?,?,?,?)', [
                ('a', '2026-11-11 12:00:00', 'K1ABC', 14, 'CW', 1),
                ('b', '2026-11-11 12:01:00', 'K1ABC', 14, 'CW', 1)])
            con.commit()
            state = RadioState()
            state.set_app({'kind': 'AppInfo', 'dbname': path, 'contest_nr': 1, 'contest_name': 'CQWWCW'})
            worked = WorkedLog(path)
            worked.app_info, worked.contact_events = state.app, state.contact_events
            worked.maybe_reload()
            stale = worked._read(path, 1, 'CQWWCW', 'N1MM')
            state.set_app(parse_app_info(b'<contactreplace><contestnr>1</contestnr><ID>a</ID>'
                b'<timestamp>2026-11-11 12:00:00</timestamp><oldtimestamp>2026-11-11 12:00:00</oldtimestamp>'
                b'<oldcall>K1ABC</oldcall><call>W1XYZ</call><band>14</band><mode>USB</mode></contactreplace>'))
            worked._take_contacts()
            worked._apply(stale, ('stale-read',))
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'CW'))
            self.assertTrue(worked.is_worked('W1XYZ', 14025, 'LSB'))
            self.assertFalse(worked.is_worked('W1XYZ', 14025, 'CW'))
            con.execute("UPDATE DXLOG SET Call='W1XYZ',Mode='USB' WHERE ID='a'"); con.commit()
            worked.request_recheck(); worked.maybe_reload()
            self.assertTrue(worked.is_worked('K1ABC', 14025, 'CW'))
            self.assertTrue(worked.is_worked('W1XYZ', 14025, 'LSB'))
            self.assertFalse(worked._session_log.pending)
            con.close()
