"""N1MM Autostarter: settings, N1MM-up rules, the port, a whole run."""

import os
import socket
import tempfile
import threading
import time
import unittest

from pouncer import autostarter as A


class SettingsTests(unittest.TestCase):
    def test_round_trip_and_defaults(self) -> None:
        d = tempfile.mkdtemp()
        p = os.path.join(d, A.INI_NAME)
        s = A.load(p)                               # no file: defaults
        self.assertEqual(len(s["apps"]), 8)
        self.assertEqual((s["n1mm_port"], s["n1mm_settle"], s["wait_n1mm"]), (12060, 30, True))
        s["apps"][0] = (r'"C:\HAM\N1MM Logger+\N1MMLogger.net.exe"', 0, False)
        s["apps"][1] = ("C:\\HAM\\rotor.exe\nfolder = evil", 10, True)   # a line break can't sneak in
        s["n1mm_port"], s["close_when_done"] = 12061, True
        A.save(p, s)
        t = A.load(p)
        self.assertEqual(t["apps"][0][0], r'"C:\HAM\N1MM Logger+\N1MMLogger.net.exe"')
        self.assertEqual(t["apps"][1], ("C:\\HAM\\rotor.exe folder = evil", 10, True))
        self.assertEqual(t["folder"], "")
        self.assertEqual((t["n1mm_port"], t["close_when_done"]), (12061, True))

    def test_reads_pouncer_style_keys(self) -> None:
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.ini")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[autostart]\napp1 = C:\\a.exe\napp1_delay = 900\nwait_n1mm = false\n")
        s = A.load(p)
        self.assertEqual(s["apps"][0], ("C:\\a.exe", 600, False))   # delay capped
        self.assertFalse(s["wait_n1mm"])


class UpTests(unittest.TestCase):
    def test_broadcast_or_running_long_enough(self) -> None:
        now = [100.0]
        run = [False]
        b = A.N1mmBeacon(0)
        up = A.N1mmUp(b, settle_s=30, running=lambda: run[0], clock=lambda: now[0])
        self.assertFalse(up())
        run[0] = True
        self.assertFalse(up())                       # just started
        now[0] += 29
        self.assertFalse(up())
        now[0] += 1
        self.assertTrue(up())                        # ran 30 s
        b.heard = 1.0
        run[0] = False
        self.assertTrue(up())                        # heard its broadcast

    def test_packet(self) -> None:
        self.assertTrue(A.is_n1mm_packet(b'<?xml version="1.0"?><RadioInfo><Freq>1</Freq>'))
        self.assertTrue(A.is_n1mm_packet(b"<AppInfo><app>N1MM</app></AppInfo>"))
        self.assertFalse(A.is_n1mm_packet(b"<contactinfo/>"))


class BeaconTests(unittest.TestCase):
    def _free_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_hears_n1mm_then_frees_the_port(self) -> None:
        port = self._free_port()
        b = A.N1mmBeacon(port, ip="127.0.0.1")
        b.start()
        self.assertTrue(b.listening)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(b"<RadioInfo><Freq>1402500</Freq></RadioInfo>", ("127.0.0.1", port))
        end = time.time() + 3
        while not b.heard and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(b.heard)
        time.sleep(0.6)
        again = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        again.bind(("127.0.0.1", port))            # free again
        again.close()
        tx.close()

    def test_busy_port_is_left_alone(self) -> None:
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        other.bind(("127.0.0.1", 0))
        b = A.N1mmBeacon(other.getsockname()[1], ip="127.0.0.1")
        b.start()
        self.assertFalse(b.listening)
        self.assertIn("busy", b.error)
        other.close()


class RunTests(unittest.TestCase):
    def _settings(self, d):
        n1mm = os.path.join(d, "N1MMLogger.net.exe")
        tool = os.path.join(d, "rotor.exe")
        for p in (n1mm, tool):
            open(p, "w").close()
        s = A.load(os.path.join(d, "none.ini"))
        s["apps"][0] = (n1mm, 0, False)
        s["apps"][1] = (tool, 0, False)
        s["apps"][2] = (os.path.join(d, "missing.exe"), 0, False)
        s["n1mm_port"], s["n1mm_settle"] = 0, 5
        return s, n1mm, tool

    def test_n1mm_first_then_the_rest(self) -> None:
        d = tempfile.mkdtemp()
        s, n1mm, tool = self._settings(d)
        now = [1000.0]
        started, said = [], []
        running = {"v": False}

        def launch(path, args):
            started.append((os.path.basename(path), now[0]))
            if path == n1mm:
                running["v"] = True

        def sleep(t):
            now[0] += t
        r = A.Runner(s, said.append, launch=launch, running=lambda: set(),
                     n1mm_is_running=lambda: running["v"], sleep=sleep, clock=lambda: now[0])
        r.run()
        self.assertEqual([n for n, _ in started], ["N1MMLogger.net.exe", "rotor.exe"])
        self.assertGreaterEqual(started[1][1] - started[0][1], 5)     # waited for N1MM
        self.assertIn(("" + os.path.join(d, "missing.exe"), "not found"), r.results)
        self.assertTrue(any("not started" in t for t in said))
        self.assertTrue(r.done.is_set())
        # not every second: a 5-8 s wait gives a few lines, not one per second
        self.assertLess(sum(1 for t in said if t.startswith("waiting for N1MM")), 3)

    def test_already_running_is_not_started_again(self) -> None:
        d = tempfile.mkdtemp()
        s, n1mm, tool = self._settings(d)
        started = []
        r = A.Runner(s, lambda t: None, launch=lambda p, a: started.append(os.path.basename(p)),
                     running=lambda: {"n1mmlogger.net.exe", "rotor.exe"},
                     n1mm_is_running=lambda: True, sleep=lambda t: None)
        r.run()
        self.assertEqual(started, [])

    def test_stop_during_the_wait(self) -> None:
        d = tempfile.mkdtemp()
        s, n1mm, tool = self._settings(d)
        s["n1mm_settle"] = 300
        started = []
        r = A.Runner(s, lambda t: None, launch=lambda p, a: started.append(os.path.basename(p)),
                     running=lambda: set(), n1mm_is_running=lambda: bool(started))
        threading.Timer(0.3, r.stop).start()
        r.run()
        self.assertEqual(started, ["N1MMLogger.net.exe"])         # the rest never started


class WindowTests(unittest.TestCase):
    def test_window_builds_and_saves(self) -> None:
        try:
            import tkinter
            tkinter.Tk().destroy()
        except Exception:  # noqa: BLE001
            self.skipTest("no Tk display")
        d = tempfile.mkdtemp()
        p = os.path.join(d, A.INI_NAME)
        app = A.App(p, setup=True)
        try:
            app.v_app[0].set(r"C:\HAM\N1MM Logger+\N1MMLogger.net.exe")
            app.v_delay[0].set("0")
            app.v_port.set("12061")
            self.assertTrue(app._save())
            s = A.load(p)
            self.assertEqual(s["apps"][0][0], r"C:\HAM\N1MM Logger+\N1MMLogger.net.exe")
            self.assertEqual(s["n1mm_port"], 12061)
            app.root.update()
        finally:
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
