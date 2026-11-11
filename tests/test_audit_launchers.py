"""Audit regressions for real runner cancellation and POSIX helper arguments."""
import os
import tempfile
import unittest
from unittest.mock import patch

from pouncer import autostart, autostarter


class CancellationTests(unittest.TestCase):
    def runner(self, folder, apps, **kwargs):
        paths = [os.path.join(folder, name) for name, _delay, _wait in apps]
        for path in paths:
            open(path, 'w').close()
        settings = {**autostarter.DEFAULTS, 'wait_n1mm': False,
                    'apps': [(path, delay, wait) for path, (_name, delay, wait) in zip(paths, apps)]}
        return autostarter.Runner(settings, say=lambda _: None, running=lambda: set(),
                                  n1mm_is_running=lambda: False, **kwargs)

    def test_stop_during_delegated_delay_launches_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            launched = []
            runner = self.runner(folder, [('first.exe', 5, False), ('second.exe', 5, False)],
                                 launch=lambda *args: launched.append(args))
            runner._sleep = lambda _: runner.stop()
            runner.run()
            self.assertEqual(launched, [])
            self.assertTrue(runner.done.is_set())

    def test_stop_during_delegated_readiness_exits_without_spinning(self):
        with tempfile.TemporaryDirectory() as folder:
            launched, sleeps = [], []
            now = [100.0]
            runner = self.runner(folder, [('first.exe', 0, True), ('second.exe', 0, False)],
                                 launch=lambda *args: launched.append(args), clock=lambda: now[0])
            def sleep(seconds):
                sleeps.append(seconds)
                now[0] += seconds
                runner.stop()
            runner._sleep = sleep
            with patch.object(runner.beacon, 'start'):
                runner.run()
            self.assertEqual(launched, [])
            self.assertEqual(sleeps, [1])

    def test_stop_between_launches_preserves_only_started_program(self):
        with tempfile.TemporaryDirectory() as folder:
            launched = []
            def launch(path, args):
                launched.append(os.path.basename(path))
                runner.stop()
            runner = self.runner(folder, [('first.exe', 0, False), ('second.exe', 0, False)], launch=launch)
            runner.run()
            self.assertEqual(launched, ['first.exe'])

    def test_stop_before_runner_starts_prevents_all_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            launched = []
            runner = self.runner(folder, [('first.exe', 0, False)], launch=lambda *a: launched.append(a))
            runner.stop()
            runner.run()
            self.assertEqual(launched, [])


class PosixArgumentsTests(unittest.TestCase):
    def test_quoted_spaces_empty_and_escaped_arguments_are_preserved(self):
        with patch.object(autostart.sys, 'platform', 'darwin'), patch.object(autostart.subprocess, 'Popen') as popen:
            autostart._launch('/tmp/My Tools/helper', '--profile "Contest station" "" escaped\\ value')
            popen.assert_called_once_with(['/tmp/My Tools/helper', '--profile', 'Contest station', '', 'escaped value'],
                                          cwd='/tmp/My Tools')

    def test_bad_quotes_fail_one_program_without_launching_it(self):
        with tempfile.TemporaryDirectory() as folder:
            bad, good = [os.path.join(folder, name) for name in ('bad', 'good')]
            for path in (bad, good):
                open(path, 'w').close()
            items = autostart.plan([(f'"{bad}" --profile "unfinished', 0), (good, 0)])
            with patch.object(autostart.sys, 'platform', 'darwin'), patch.object(autostart.subprocess, 'Popen') as popen:
                runner = autostart.Autostart(items)
                runner.run()
            self.assertTrue(runner.results[0][1].startswith('failed:'))
            self.assertEqual(runner.results[1][1], 'started')
            popen.assert_called_once_with([good], cwd=folder)
