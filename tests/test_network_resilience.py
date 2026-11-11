"""Regression coverage for audit F03/F05/F07/F14; only isolated sockets/data."""
import dataclasses
import json
import queue
import socket
import tempfile
import threading
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pouncer import diag, team
from pouncer.clusterserver import ClusterServer, _Client, CLIENT_PENDING_BYTES, CLIENT_PENDING_ITEMS
from pouncer.rbn import OnFreqTracker
from tests.test_clusterserver import spot
from tests.test_so2r import _import_app


class DiagnosticTeamSecretTests(unittest.TestCase):
    def test_multiline_team_key_accepted_by_config_is_also_redacted(self):
        from pouncer.config import load_config
        key = team.derive_key('audit-only-password').hex()
        text = '[team]\nkey =\n    ' + key + '\n[ui]\nfkeys = F1:CQ\n'
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder, 'config.ini')
            config.write_text(text)
            self.assertEqual(load_config(str(config)).team_key, key)
            out = diag.redact_ini(text)
            self.assertNotIn(key, out)
            self.assertIn('fkeys = F1:CQ', out)

    def test_real_team_key_and_commented_variants_are_absent_from_zip(self):
        from pouncer.config import load_config
        key = team.derive_key('audit-only-password').hex()
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder, 'config.ini')
            config.write_text('[team]\nkey = ' + key + '\n; key: ' + key + '\n# KEY = ' + key +
                              '\n[ui]\nfkeys = F1:CQ\nkey = ordinary-key-binding\n')
            self.assertEqual(load_config(str(config)).team_key, key)
            out = Path(folder, 'diagnostics.zip')
            diag.build_zip(str(out), str(config), folder, {'team': 'password set'})
            with zipfile.ZipFile(out) as archive:
                for name in archive.namelist():
                    self.assertNotIn(key.encode(), archive.read(name), name)
                cleaned = archive.read('config.ini').decode()
            self.assertIn('key = ***', cleaned)
            self.assertIn('; key: ***', cleaned)
            self.assertIn('# KEY = ***', cleaned)
            self.assertIn('fkeys = F1:CQ', cleaned)
            self.assertIn('key = ordinary-key-binding', cleaned)
            self.assertEqual(diag.redact_ini('[team]\nkey =\n'), '[team]\nkey =\n')
            self.assertNotIn(key, diag.redact_ini('[TEAM]\nKEY=' + key))


class SlowClusterClientTests(unittest.TestCase):
    def test_idle_logged_in_client_survives_read_timeouts(self):
        from tests.test_clusterserver import Client
        with patch('pouncer.clusterserver.CLIENT_IO_TIMEOUT_S', 0.05):
            server = ClusterServer(port=0, host='127.0.0.1')
            self.assertTrue(server.start())
            client = Client(server._sock.getsockname()[1])
            try:
                client.until('login:')
                client.send('S53ZO')
                client.until('de POUNCER >')
                time.sleep(0.15)
                self.assertEqual(server.client_count(), 1)
                server.publish(spot())
                self.assertIn('DL1ABC', client.until('DL1ABC'))
            finally:
                client.s.close()
                server.stop()

    @staticmethod
    def fill(sock):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        sock.setblocking(False)
        while True:
            try:
                sock.send(b'x' * 65536)
            except BlockingIOError:
                break
        sock.setblocking(True)

    def test_slow_client_cannot_block_publish_healthy_delivery_or_shutdown(self):
        slow, unread = socket.socketpair()
        healthy, reader = socket.socketpair()
        self.fill(slow)
        a, b = _Client(slow, ('slow', 1)), _Client(healthy, ('healthy', 2))
        a.call, b.call = 'SLOW', 'HEALTHY'
        server = ClusterServer()
        server._sock = object()  # no listener needed: exercise the real publish path
        server.clients = [a, b]
        done = threading.Event()
        def publish():
            server.publish(spot())
            done.set()
        worker = threading.Thread(target=publish, daemon=True)
        try:
            worker.start()
            self.assertTrue(done.wait(0.5), 'publish blocked on non-reading client')
            reader.settimeout(1)
            self.assertIn(b'DL1ABC', reader.recv(4096))
            t0 = time.monotonic()
            server._sock = None
            server.stop()
            self.assertLess(time.monotonic() - t0, 0.5)
            for client in (a, b):
                writer = getattr(client, '_writer', None)
                if writer is not None:
                    writer.join(1)
                    self.assertFalse(writer.is_alive(), 'writer survived server shutdown')
        finally:
            server._sock = None
            server.stop()
            for sock in (slow, unread, healthy, reader):
                sock.close()
            worker.join(2)

    def test_output_is_bounded_by_bytes_and_items_and_disconnects_slow_client(self):
        for by_bytes in (True, False):
            with self.subTest(by_bytes=by_bytes):
                left, right = socket.socketpair()
                self.fill(left)
                client = _Client(left, ('slow', 1))
                try:
                    t0 = time.monotonic()
                    if by_bytes:
                        self.assertTrue(client.send_raw(b'x' * CLIENT_PENDING_BYTES))
                        self.assertFalse(client.send_raw(b'y'))
                    else:
                        for _ in range(CLIENT_PENDING_ITEMS + 2):
                            if not client.send_raw(b'x'):
                                break
                        else:
                            self.fail('unbounded output item queue')
                    self.assertLess(time.monotonic() - t0, 0.5)
                    self.assertTrue(client.closed)
                    self.assertLessEqual(client._pending_bytes, CLIENT_PENDING_BYTES)
                    client._writer.join(1)
                    self.assertFalse(client._writer.is_alive())
                finally:
                    client.close()
                    right.close()

    def test_async_output_preserves_prompt_and_own_line_framing(self):
        left, right = socket.socketpair()
        client = _Client(left, ('local', 1))
        try:
            client.send_raw(b'login: ')
            client.send_raw(b'DX example\r\n', own_line=True)
            client.send_raw(b'reply\r\n')
            right.settimeout(1)
            data = b''
            while b'reply\r\n' not in data:
                data += right.recv(4096)
            self.assertEqual(data, b'login: \r\nDX example\r\nreply\r\n')
        finally:
            client.close()
            right.close()


class TeamReceiveRecoveryTests(unittest.TestCase):
    def test_malformed_packets_then_valid_traffic_in_unsigned_and_signed_modes(self):
        for key in (b'', team.derive_key('audit-only-password')):
            with self.subTest(signed=bool(key)):
                receiver = team.Team(0, port=0, key=key)
                sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                address = ('127.0.0.1', receiver._sock.getsockname()[1])
                errors = []
                def listen():
                    try:
                        receiver._listen()
                    except Exception as exc:
                        errors.append(exc)
                worker = threading.Thread(target=listen, daemon=True)
                worker.start()
                def send(msg):
                    sender.sendto(team.seal(json.dumps(msg).encode(), key=key), address)
                try:
                    for fields in ({'web': 'oops'}, {'started': float('nan')},
                                   {'started': float('inf')}, {'started': []},
                                   {'web': True}, {'web': 65536}, {'healthy': []},
                                   {'bands': {'40m': {'spots': float('nan')}}}):
                        send({'t': 'hi', 'id': 'bad', 'started': time.time(), **fields})
                    sender.sendto(team.seal(b'\xff', key=key), address)
                    sender.sendto(team.seal(b'[' * 2000 + b']' * 2000, key=key), address)
                    send({'t': 'hi', 'id': ['bad'], 'web': 8080})
                    send({'t': 'hi', 'id': 'good', 'web': '8080', 'started': time.time() - 100})
                    end = time.monotonic() + 2
                    while time.monotonic() < end and 'good' not in receiver.peers and not errors:
                        time.sleep(0.01)
                    self.assertFalse(errors, repr(errors))
                    self.assertTrue(worker.is_alive())
                    self.assertIn('good', receiver.peers)
                    self.assertNotIn('bad', receiver.peers)
                    self.assertEqual(receiver.peers['good']['web'], 8080)
                finally:
                    receiver.stop()
                    sender.close()
                    worker.join(1)

    def test_bad_shared_history_is_rejected_before_callback_or_cache(self):
        delivered = []
        receiver = team.Team(0, port=0, on_runhist=delivered.append)
        try:
            for value in ({'run': float('nan')}, {'hours': '1'}, {'hours': True},
                          {'bands': {'40m': float('inf')}}, {'history_contests': [123]}):
                receiver.handle({'t': 'rh', 'id': 'peer', 'd': {'S53ZO|CQWW|CW': value}}, '127.0.0.1')
            self.assertFalse(delivered)
            valid = {'S53ZO|CQWW|CW': {'run': 20, 'hours': 0.25, 'hours_unrounded': True}}
            receiver.handle({'t': 'rh', 'id': 'peer', 'd': valid}, '127.0.0.1')
            self.assertEqual(delivered, [valid])
        finally:
            receiver.stop()

    def test_callback_failure_does_not_terminate_receiver(self):
        delivered = []
        def callback(data):
            if 'FAIL|CQWW|CW' in data:
                raise RuntimeError('isolated application callback failure')
            delivered.append(data)
        receiver = team.Team(0, port=0, on_runhist=callback)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        address = ('127.0.0.1', receiver._sock.getsockname()[1])
        errors = []
        def listen():
            try:
                receiver._listen()
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=listen, daemon=True)
        worker.start()
        try:
            for call in ('FAIL', 'GOOD'):
                sender.sendto(json.dumps({'t': 'rh', 'id': 'peer', 'd': {
                    call + '|CQWW|CW': {'run': 50}}}).encode(), address)
            end = time.monotonic() + 2
            while time.monotonic() < end and not delivered and not errors:
                time.sleep(0.01)
            self.assertFalse(errors, repr(errors))
            self.assertTrue(worker.is_alive())
            self.assertEqual(delivered, [{'GOOD|CQWW|CW': {'run': 50}}])
        finally:
            receiver.stop()
            sender.close()
            worker.join(1)


class TeamSpotAgeTests(unittest.TestCase):
    def test_age_survives_clock_skew_tracking_and_expiry(self):
        app = _import_app()
        source = spot()
        source.received = 401
        with patch.object(app.time, 'time', return_value=1000):
            data = app.PouncerApp._spot_dict(source)
        self.assertNotIn('received', data)
        self.assertEqual(data['age_s'], 599)
        target = types.SimpleNamespace(_team_queue=queue.Queue())
        with patch.object(app.time, 'time', return_value=5000):
            app.PouncerApp._team_spot(target, data)
        forwarded = target._team_queue.get_nowait()
        self.assertEqual(forwarded.received, 4401)
        tracker = OnFreqTracker()
        tracker.add(forwarded, '20m')
        self.assertEqual(tracker.minutes(forwarded.dxcall, '20m', forwarded.freq_khz, 5000), 9)
        self.assertGreaterEqual(5000 - forwarded.received, 300)  # already faded
        target.spots = {forwarded.key: forwarded}
        target._on_freq = tracker
        target._net_down_since = lambda: None
        target.cfg = types.SimpleNamespace(max_age_minutes=10)
        target._dirty = False
        with patch.object(app.time, 'time', return_value=5000):
            app.PouncerApp._prune(target)
        self.assertTrue(target.spots)
        with patch.object(app.time, 'time', return_value=5002):
            app.PouncerApp._prune(target)
        self.assertFalse(target.spots)

    def test_legacy_age_fallback_and_invalid_age_or_coordinates(self):
        app = _import_app()
        legacy = dataclasses.asdict(spot())
        legacy.pop('received')
        target = types.SimpleNamespace(_team_queue=queue.Queue())
        with patch.object(app.time, 'time', return_value=5000):
            app.PouncerApp._team_spot(target, legacy)
            self.assertEqual(target._team_queue.get_nowait().received, 5000)
            for age in (-1, float('nan'), float('inf'), '599', True, None):
                app.PouncerApp._team_spot(target, {**legacy, 'age_s': age})
            for freq in (float('nan'), float('inf'), 1e308, -1, True):
                app.PouncerApp._team_spot(target, {**legacy, 'freq_khz': freq})
            self.assertTrue(target._team_queue.empty())
            app.PouncerApp._team_spot(target, {**legacy, 'age_s': 0})
            self.assertEqual(target._team_queue.get_nowait().received, 5000)
