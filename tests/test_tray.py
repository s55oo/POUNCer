"""N1MM Autostarter: minimized = an icon in the system tray (v0.7.242)."""
import os
import sys
import tempfile
import time
import unittest

from pouncer import tray


class TrayIconTests(unittest.TestCase):
    def test_off_windows_or_failure_is_harmless(self):
        t = tray.TrayIcon("", "tip", lambda what: None)
        if not tray.AVAILABLE:
            self.assertFalse(t.show())
            t.hide()                      # never raises

    @unittest.skipUnless(tray.AVAILABLE, "Windows only")
    def test_show_and_hide_a_real_icon(self):
        from pouncer import theme
        t = tray.TrayIcon(os.path.join(theme.icon_dir(), "autostarter.ico"), "POUNCer test",
                          lambda what: None)
        if not t.show():
            self.skipTest("no notification area here (no Explorer session)")
        self.assertTrue(t.visible())
        self.assertTrue(t.show())         # twice = still one icon
        t.hide()
        self.assertFalse(t.visible())
        self.assertTrue(t.show())         # and back again
        t.hide()
        self.assertFalse(t.visible())

    @unittest.skipUnless(tray.AVAILABLE, "Windows only")
    def test_left_click_message_sends_show(self):
        got = []
        t = tray.TrayIcon("", "POUNCer test", got.append)
        if not t.show():
            self.skipTest("no notification area here")
        try:
            for ev in (tray.WM_LBUTTONUP, tray.WM_LBUTTONDBLCLK):
                tray._user32.PostMessageW(t._hwnd, tray.CALLBACK, 1, ev)
            end = time.time() + 3
            while len(got) < 2 and time.time() < end:
                time.sleep(0.05)
            self.assertEqual(got, ["show", "show"])
        finally:
            t.hide()

    @unittest.skipUnless(tray.AVAILABLE, "Windows only")
    def test_missing_icon_file_falls_back(self):
        t = tray.TrayIcon(r"C:\nowhere\none.ico", "POUNCer test", lambda what: None)
        if not t.show():
            self.skipTest("no notification area here")
        t.hide()


@unittest.skipUnless(tray.AVAILABLE, "Windows only")
class AutostarterTrayTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            tk.Tk().destroy()
        except Exception:  # noqa: BLE001
            self.skipTest("no display")
        from pouncer.autostarter import App
        self.dir = tempfile.TemporaryDirectory()
        self.app = App(os.path.join(self.dir.name, "N1MMautostarter.ini"), setup=True)
        if self.app.tray is None or not self.app.tray.show():
            self.app.root.destroy()
            self.skipTest("no notification area here")
        self.app.tray.hide()

    def tearDown(self):
        try:
            self.app._close()
        except Exception:  # noqa: BLE001
            pass
        self.dir.cleanup()

    def pump(self, seconds=1.0):
        end = time.time() + seconds
        while time.time() < end:
            self.app.root.update()
            time.sleep(0.02)

    def test_minimize_goes_to_tray_and_a_click_brings_it_back(self):
        root = self.app.root
        self.pump(0.3)
        root.iconify()
        self.pump(1.0)
        self.assertEqual(root.state(), "withdrawn")     # not on the taskbar
        self.assertTrue(self.app.tray.visible())
        self.app._tray_q.put("show")                     # what a click on the icon sends
        self.pump(1.0)
        self.assertEqual(root.state(), "normal")
        self.assertFalse(self.app.tray.visible())

    def test_tray_exit_closes(self):
        root = self.app.root
        self.pump(0.3)
        root.iconify()
        self.pump(1.0)
        closed = []
        real = self.app._close
        self.app._close = lambda: (closed.append(1), real())
        self.app._tray_q.put("exit")
        try:
            self.pump(1.0)
        except Exception:  # noqa: BLE001 - the root is gone
            pass
        self.assertEqual(closed, [1])


if __name__ == "__main__":
    unittest.main()
