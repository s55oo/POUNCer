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
        self.assertFalse(win32.clear_focused_edit(12345))
        self.assertFalse(win32.clear_focused_edit(0))
        self.assertEqual(win32._VK_OEM_7, 0xDE)



@unittest.skipUnless(win32._WIN, "needs Windows")
class ClearEditOnWindowsTests(unittest.TestCase):
    def test_clears_a_real_focused_edit_box(self) -> None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE = 0x00CF0000, 0x40000000, 0x10000000
        top = u.CreateWindowExW(0, "STATIC", "pouncer test", WS_OVERLAPPEDWINDOW,
                                0, 0, 200, 100, None, None, None, None)
        if not top:
            self.skipTest("cannot create a window here")
        try:
            edit = u.CreateWindowExW(0, "EDIT", "S50ROH2BH", WS_CHILD | WS_VISIBLE,
                                     0, 0, 150, 20, top, None, None, None)
            self.assertTrue(edit)
            u.SetFocus.argtypes = (wintypes.HWND,)
            u.SetFocus(edit)
            self.assertTrue(win32.clear_focused_edit(top))
            buf = ctypes.create_unicode_buffer(64)
            u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
            u.GetWindowTextW(edit, buf, 64)
            self.assertEqual(buf.value, "")
        finally:
            u.DestroyWindow.argtypes = (wintypes.HWND,)
            u.DestroyWindow(top)

    def test_restore_focus_puts_cursor_back(self) -> None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE = 0x00CF0000, 0x40000000, 0x10000000
        top = u.CreateWindowExW(0, "STATIC", "pouncer test", WS_OVERLAPPEDWINDOW,
                                0, 0, 200, 100, None, None, None, None)
        if not top:
            self.skipTest("cannot create a window here")
        try:
            call = u.CreateWindowExW(0, "EDIT", "K1ABC", WS_CHILD | WS_VISIBLE,
                                     0, 0, 150, 20, top, None, None, None)
            exch = u.CreateWindowExW(0, "EDIT", "599 15", WS_CHILD | WS_VISIBLE,
                                     0, 30, 150, 20, top, None, None, None)
            u.SetFocus.argtypes = (wintypes.HWND,)
            u.GetFocus.restype = wintypes.HWND
            u.SetFocus(call)
            win32.restore_focus({"fg": top, "focus": exch, "sel": (2, 4), "pid": 0})
            self.assertEqual(u.GetFocus(), exch)            # back in the exchange box
            start, end = wintypes.DWORD(0), wintypes.DWORD(0)
            u.SendMessageW(exch, 0x00B0, ctypes.addressof(start), ctypes.addressof(end))
            self.assertEqual((start.value, end.value), (2, 4))   # same cursor / selection
            # a call put in one go: text and cursor at its end
            u.SetFocus(call)
            self.assertTrue(win32.set_focused_edit_text(top, "HB9CBR/P"))
            self.assertEqual(win32.focused_edit_text(top), "HB9CBR/P")
            u.SendMessageW(call, 0x00B0, ctypes.addressof(start), ctypes.addressof(end))
            self.assertEqual((start.value, end.value), (8, 8))
        finally:
            u.DestroyWindow.argtypes = (wintypes.HWND,)
            u.DestroyWindow(top)

if __name__ == "__main__":
    unittest.main()
