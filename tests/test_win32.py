"""Tests for the N1MM process check (process list is faked)."""

import unittest
from unittest import mock

from pouncer import win32


class N1MMRunningTests(unittest.TestCase):
    def _check(self, names):
        with mock.patch.object(win32, "running_process_names", return_value=names):
            return win32.is_n1mm_running()

    def test_real_exe_name_matches(self) -> None:
        # The real N1MM Logger+ exe has no space in its name.
        self.assertTrue(self._check({"explorer.exe", "n1mmlogger.net.exe"}))

    def test_spaced_name_also_matches(self) -> None:
        self.assertTrue(self._check({"n1mm logger.net.exe"}))

    def test_not_running(self) -> None:
        self.assertFalse(self._check({"explorer.exe", "chrome.exe"}))

    def test_unknown_when_scan_fails_or_is_empty(self) -> None:
        self.assertIsNone(self._check(None))
        self.assertIsNone(self._check(set()))


class FilterToggleTests(unittest.TestCase):
    def test_no_op_off_windows(self) -> None:
        if win32._WIN:
            self.skipTest("would press Alt+' on this machine")
        self.assertFalse(win32.send_filter_toggle())
        self.assertEqual(win32._VK_OEM_7, 0xDE)


if __name__ == "__main__":
    unittest.main()
