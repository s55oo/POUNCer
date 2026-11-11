"""Country-file failures must remain retryable without killing the worker."""
import os
import tempfile
import unittest
from unittest.mock import patch

from pouncer.ctyupdate import CtyUpdater
from tests.test_ctyupdate import fake_cty


class RecoveryTests(unittest.TestCase):
    def test_invalid_or_empty_success_response_retries_in_an_hour(self):
        for response in (b'<html>temporary error</html>', b''):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                now = [1_800_000_000.0]
                fetch_result = [(200, response)]
                u = CtyUpdater(folder, clock=lambda: now[0], fetch=lambda *_: fetch_result[0])
                self.assertFalse(u.check())
                now[0] += 3600
                self.assertTrue(u.due())
                fetch_result[0] = (200, fake_cty())
                self.assertTrue(u.check())
                self.assertFalse(u.due())

    def test_cache_write_failure_preserves_previous_file_and_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1_800_000_000.0]
            u = CtyUpdater(folder, clock=lambda: now[0], fetch=lambda *_: (200, fake_cty()))
            with open(u.path, 'wb') as fh:
                fh.write(b'previous file')
            with patch.object(u, '_write', side_effect=OSError('temporary disk error')):
                self.assertFalse(u.check())
            with open(u.path, 'rb') as fh:
                self.assertEqual(fh.read(), b'previous file')
            now[0] += 3600
            self.assertTrue(u.due())
            self.assertTrue(u.check())
            self.assertFalse(u.due())

    def test_actual_worker_retries_failed_install_then_accepts_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            now, calls, waits = [1_800_000_000.0], [], []
            def fetch(*_):
                calls.append(now[0])
                return 200, fake_cty()
            u = CtyUpdater(folder, clock=lambda: now[0], fetch=fetch,
                           on_new=lambda _: u.stop())
            original_write = u._write
            def write(path, data):
                if len(calls) == 1:
                    raise OSError('first install failed')
                original_write(path, data)
            def wait(seconds):
                waits.append(seconds)
                now[0] += seconds
                if len(waits) > 2:
                    u.stop()
            with patch.object(u, '_write', side_effect=write), patch.object(u._stop, 'wait', side_effect=wait), \
                    patch('pouncer.ctyupdate.threading.Thread') as thread:
                u.start()
                thread.call_args.kwargs['target']()
            self.assertEqual(calls, [1_800_000_000.0, 1_800_003_600.0])
            self.assertTrue(os.path.exists(u.path))
            self.assertFalse(u.due())


class N1mmCopyRecoveryTests(unittest.TestCase):
    def test_failed_n1mm_copy_retries_from_cache_after_restart_and_304(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [1_800_000_000.0]
            destination = os.path.join(folder, 'n1mm', 'wl_cty.dat')
            os.makedirs(os.path.dirname(destination))
            with open(destination, 'wb') as fh:
                fh.write(b'old N1MM file')
            os.utime(destination, (1, 1))
            installed = []
            cache = os.path.join(folder, 'cache')
            u = CtyUpdater(cache, n1mm_path=destination, update_n1mm=True, url="https://isolated.invalid/wl_cty.dat",
                           clock=lambda: now[0], fetch=lambda *_: (200, fake_cty()), on_new=installed.append)
            os.makedirs(cache, exist_ok=True)
            # A different source filename can be missing even though this
            # week's shared check marker is recent. A failed copy must still retry.
            with open(u.check_path, 'w') as fh:
                fh.write(str(now[0] - 3600))
            self.assertTrue(u.due())
            original = u._write
            def write(path, data):
                if path == destination:
                    raise OSError('N1MM temporarily locked')
                original(path, data)
            with patch.object(u, '_write', side_effect=write):
                self.assertTrue(u.check())  # the main app still gets its valid new cache
            self.assertEqual(installed, [u.path])
            now[0] += 3600
            self.assertTrue(u.due())
            restarted = CtyUpdater(cache, n1mm_path=destination, update_n1mm=True, url="https://isolated.invalid/wl_cty.dat",
                                  clock=lambda: now[0], fetch=lambda *_: (304, b''))
            self.assertTrue(restarted.due())
            self.assertFalse(restarted.check())  # no newer server file; copy still recovers
            with open(destination, 'rb') as fh:
                self.assertEqual(fh.read(), fake_cty())
            self.assertFalse(restarted.due())

    def test_304_does_not_replace_a_newer_local_n1mm_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = os.path.join(folder, 'wl_cty.dat')
            u = CtyUpdater(os.path.join(folder, 'cache'), n1mm_path=destination,
                           update_n1mm=True, url="https://isolated.invalid/wl_cty.dat", fetch=lambda *_: (304, b''))
            os.makedirs(os.path.dirname(u.path))
            with open(u.path, 'wb') as fh:
                fh.write(fake_cty())
            with open(destination, 'wb') as fh:
                fh.write(fake_cty(321))
            os.utime(u.path, (100, 100))
            os.utime(destination, (200, 200))
            self.assertFalse(u.check())
            with open(destination, 'rb') as fh:
                self.assertEqual(fh.read(), fake_cty(321))
