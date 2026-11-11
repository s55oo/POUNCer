"""HTTP control permissions and browser intent through real isolated servers."""
import http.client
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import unittest
from unittest.mock import patch

from pouncer.web import WebServer, _Handler


# Every state-changing route belongs here, including changes unrelated to RF.
MUTATIONS = (
    '/api/qsy?call=K1ABC&freq=14025', '/api/key?key=F1', '/api/cw?op=open',
    '/api/tune?hz=25', '/api/band?band=80m', '/api/filter?w=narrow',
    '/api/minsnr?db=10', '/api/hide?call=K1ABC&freq=14025',
    '/api/unhide?call=K1ABC&freq=14025', '/api/clear', '/api/reconnect',
    '/api/settings/set?key=ui.min_snr&value=10', '/api/restart',
)
RADIO_MUTATIONS = MUTATIONS[:6]


class BrowserIntentTests(unittest.TestCase):
    def setUp(self):
        self.actions = []
        def action(*args, **kwargs):
            self.actions.append((args, kwargs))
            return {'ok': True}
        self.web = WebServer('127.0.0.1', 0, lambda **kw: {}, action, action,
                             action, action, action, action, action, action)
        self.web.set_cw_handler(action)
        self.web.set_min_snr_handler(action)
        self.web.set_settings_provider(lambda: {}, action, restart=action)
        self.web.start()
        self.host = f'127.0.0.1:{self.web.port}'

    def tearDown(self):
        self.web.stop()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.web.port, timeout=3)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def token(self):
        code, body, _ = self.request('GET', '/api/session')
        self.assertEqual(code, 200)
        return json.loads(body)['token']

    def test_head_and_get_never_mutate(self):
        for method in ('HEAD', 'GET'):
            for path in MUTATIONS:
                with self.subTest(method=method, path=path):
                    code, body, headers = self.request(method, path)
                    self.assertEqual(code, 405)
                    self.assertEqual(headers.get('Allow'), 'POST')
                    if method == 'HEAD':
                        self.assertEqual(body, b'')
        self.assertEqual(self.actions, [])

    def test_look_only_permission_covers_every_radio_action(self):
        token = self.token()
        self.web.set_phone_control('off')
        with patch.object(_Handler, '_local_client', return_value=False):
            for path in RADIO_MUTATIONS:
                with self.subTest(path=path):
                    self.assertEqual(self.request('POST', path, {'X-POUNCer-Token': token})[0], 403)
        self.assertEqual(self.actions, [])

    def test_supported_local_phone_and_all_policies(self):
        token = self.token()
        for local, policy in ((True, 'off'), (False, 'phone'), (False, 'all')):
            self.web.set_phone_control(policy)
            with patch.object(_Handler, '_local_client', return_value=local):
                for path in RADIO_MUTATIONS:
                    with self.subTest(local=local, policy=policy, path=path):
                        self.assertEqual(self.request('POST', path, {'X-POUNCer-Token': token})[0], 200)
        self.assertEqual(len(self.actions), len(RADIO_MUTATIONS) * 3)

    def test_all_mutations_require_token_and_same_origin_intent(self):
        token = self.token()
        invalid_headers = (
            {}, {'X-POUNCer-Token': 'incorrect'},
            {'X-POUNCer-Token': token, 'Origin': 'http://evil.example'},
            {'X-POUNCer-Token': token, 'Origin': 'null'},
            {'X-POUNCer-Token': token, 'Sec-Fetch-Site': 'cross-site'},
            {'X-POUNCer-Token': token, 'Sec-Fetch-Site': 'same-site'},
            {'X-POUNCer-Token': token, 'Host': f'evil.example:{self.web.port}'},
        )
        for path in MUTATIONS:
            for headers in invalid_headers:
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request('POST', path, headers)[0], 403)
        self.assertEqual(self.actions, [])
        for path in MUTATIONS:
            code, _, _ = self.request('POST', path, {
                'X-POUNCer-Token': token, 'Origin': f'http://{self.host}',
                'Sec-Fetch-Site': 'same-origin',
            })
            self.assertEqual(code, 200, path)
        self.assertEqual(len(self.actions), len(MUTATIONS))

    def test_foreign_host_cannot_read_token_or_settings(self):
        for path in ('/', '/api/session', '/api/settings', '/api/runhist'):
            self.assertEqual(self.request('GET', path, {'Host': f'evil.example:{self.web.port}'})[0], 403)
        for headers in ({'Origin': 'http://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('GET', '/api/session', headers)[0], 403)

    def test_loopback_hostname_and_intentless_clients_work(self):
        code, body, _ = self.request('GET', '/api/session', {'Host': f'localhost:{self.web.port}'})
        self.assertEqual(code, 200)
        token = json.loads(body)['token']
        self.assertEqual(self.request('POST', '/api/key?key=F1', {
            'Host': f'localhost:{self.web.port}', 'Origin': f'http://localhost:{self.web.port}',
            'X-POUNCer-Token': token,
        })[0], 200)
        for hostname in (socket.gethostname(), socket.gethostname().split('.')[0] + '.local'):
            self.assertEqual(self.request('GET', '/api/session', {
                'Host': f'{hostname}:{self.web.port}',
            })[0], 200)

    def test_malformed_host_origin_and_token_headers_are_rejected(self):
        token = self.token()
        for host in ('evil.example', self.host + '@evil.example', self.host + '/path',
                     self.host + '?query', self.host + '#fragment', '127.0.0.1:1'):
            self.assertEqual(self.request('POST', '/api/key?key=F1', {
                'Host': host, 'X-POUNCer-Token': token,
            })[0], 403, host)
        self.assertEqual(self.request('POST', '/api/key?key=F1', {'X-POUNCer-Token': '\u00e9'})[0], 403)
        self.assertEqual(self.actions, [])

    def test_duplicate_security_headers_are_rejected(self):
        token = self.token()
        for duplicate in ('Host', 'Origin', 'Sec-Fetch-Site', 'X-POUNCer-Token'):
            conn = http.client.HTTPConnection('127.0.0.1', self.web.port, timeout=3)
            conn.putrequest('POST', '/api/key?key=F1', skip_host=True)
            headers = {'Host': self.host, 'Origin': f'http://{self.host}',
                       'Sec-Fetch-Site': 'same-origin', 'X-POUNCer-Token': token}
            for name, value in headers.items():
                conn.putheader(name, value)
            conn.putheader(duplicate, headers[duplicate])
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 403, duplicate)
            response.read(); conn.close()
        self.assertEqual(self.actions, [])

    def test_served_main_and_settings_pages_use_shared_post_helper(self):
        for path in ('/', '/settings'):
            code, body, headers = self.request('GET', path)
            self.assertEqual(code, 200)
            page = body.decode()
            self.assertIn('async function mutationFetch(url)', page)
            self.assertIn("method: 'POST'", page)
            self.assertEqual(headers['X-Frame-Options'], 'DENY')
            for route in ('key', 'qsy', 'cw', 'tune', 'minsnr', 'settings/set', 'restart', 'clear', 'reconnect'):
                self.assertIsNone(re.search(r"(?<!\w)fetch\('/api/" + route + r"[?' ]", page), route)

    def test_post_body_and_settings_policy(self):
        token = self.token()
        headers = {'X-POUNCer-Token': token, 'Content-Type': 'application/x-www-form-urlencoded'}
        self.assertEqual(self.request('POST', '/api/key', headers, 'key=F2')[0], 200)
        self.assertEqual(self.actions[-1][0], ('F2',))
        with patch.object(_Handler, '_local_client', return_value=False):
            self.assertEqual(self.request('POST', MUTATIONS[-2], headers)[0], 403)
            self.web._httpd.remote_settings = True
            self.assertEqual(self.request('POST', MUTATIONS[-2], headers)[0], 200)
        before = len(self.actions)
        self.assertEqual(self.request('POST', '/api/settings/unknown?key=x&value=y', headers)[0], 404)
        self.assertEqual(len(self.actions), before)

    def test_read_only_head_is_empty_and_mutation_body_is_bounded(self):
        for path in ('/', '/help', '/settings', '/api/spots'):
            code, body, _ = self.request('HEAD', path)
            self.assertEqual(code, 200)
            self.assertEqual(body, b'')
        token = self.token()
        self.assertEqual(self.request('POST', '/api/key', {
            'X-POUNCer-Token': token, 'Content-Type': 'application/x-www-form-urlencoded',
        }, 'key=' + 'x' * 9000)[0], 413)
        self.assertEqual(self.actions, [])


class BrowserMutationClientTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required for production JS helper')
    def test_post_handshake_and_restart_recovery_without_duplicate_controls(self):
        result = subprocess.run([shutil.which('node'), 'tests/browser_mutations.cjs'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
