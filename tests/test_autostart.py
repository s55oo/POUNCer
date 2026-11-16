"""Autostart (used by N1MMautostarter.exe): programs in order, after their delays."""

import os
import tempfile
import unittest

from pouncer import autostart as a


class SplitTests(unittest.TestCase):
    def test_paths_and_arguments(self) -> None:
        self.assertEqual(a.split_command(r'"C:\My Apps\tool.exe" --port 9000'),
                         (r"C:\My Apps\tool.exe", "--port 9000"))
        self.assertEqual(a.split_command(r"C:\My Apps\tool.exe"), (r"C:\My Apps\tool.exe", ""))
        self.assertEqual(a.split_command(r'"C:\x.bat"'), (r"C:\x.bat", ""))
        self.assertEqual(a.split_command("  "), ("", ""))


class PlanRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.exe = os.path.join(d, "rotor.exe")
        self.bat = os.path.join(d, "cw tool.bat")
        for p in (self.exe, self.bat):
            open(p, "w").close()
        self.folder = os.path.join(d, "autostart")
        os.makedirs(self.folder)
        for n in ("2 second.bat", "1 first.lnk", "README.txt"):
            open(os.path.join(self.folder, n), "w").close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_order_delays_and_folder(self) -> None:
        items = a.plan([(self.exe, 5), ("", 5), (f'"{self.bat}" -x', 10), ("", 1)], self.folder, 3)
        self.assertEqual([os.path.basename(i["path"]) for i in items],
                         ["rotor.exe", "cw tool.bat", "1 first.lnk", "2 second.bat"])
        self.assertEqual([i["delay"] for i in items], [5, 10, 3, 3])
        self.assertEqual(items[1]["args"], "-x")

    def test_run_skips_running_and_missing(self) -> None:
        slept, launched, said = [], [], []
        items = a.plan([(self.exe, 2), (os.path.join(self.tmp.name, "gone.exe"), 1), (self.bat, 4)])
        r = a.Autostart(items, running=lambda: {"rotor.exe"}, launch=lambda p, x: launched.append(p),
                        sleep=slept.append, on_status=said.append)
        r.run()
        self.assertEqual(slept, [2, 1, 4])                    # each after the one before
        self.assertEqual(launched, [self.bat])                # rotor already runs, gone missing
        self.assertEqual([res for _, res in r.results], ["running", "not found", "started"])
        self.assertIn("started cw tool.bat (3/3)", said[-1])

    def test_same_program_four_times(self) -> None:
        launched = []
        items = a.plan([(self.exe, 0)] * 4)
        a.Autostart(items, running=lambda: set(), launch=lambda p, x: launched.append(p),
                    sleep=lambda s: None).run()
        self.assertEqual(launched, [self.exe] * 4)          # 4 copies
        launched.clear()
        a.Autostart(items, running=lambda: {"rotor.exe"}, launch=lambda p, x: launched.append(p),
                    sleep=lambda s: None).run()
        self.assertEqual(launched, [])                      # already running: none again

    def test_one_failure_does_not_stop_the_rest(self) -> None:
        def launch(p, x):
            if p == self.exe:
                raise OSError("access denied")
        r = a.Autostart(a.plan([(self.exe, 0), (self.bat, 0)]), launch=launch, sleep=lambda s: None)
        r.run()
        self.assertEqual([res for _, res in r.results], ["failed: access denied", "started"])


class PerItemWaitTests(unittest.TestCase):
    def test_waits_for_n1mm_then_starts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "router.exe")
            open(exe, "w").close()
            items = a.plan([(exe, 0, True), (exe, 2)])
            self.assertEqual([i["wait"] for i in items], [True, False])
            now = [0.0]
            up = {"v": False}
            started, slept = [], []

            def sleep(s):
                slept.append(s)
                now[0] += s
                if now[0] >= 5:
                    up["v"] = True          # N1MM broadcasts after 5 s
            run = a.Autostart(items, launch=lambda p, x: started.append(now[0]), sleep=sleep,
                              n1mm_up=lambda: up["v"], wait_s=60, clock=lambda: now[0])
            run.run()
            self.assertEqual(started, [5.0, 7.0])

    def test_gives_up_after_wait(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "x.exe")
            open(exe, "w").close()
            now = [0.0]
            started = []
            run = a.Autostart(a.plan([(exe, 0, True)]), launch=lambda p, x: started.append(now[0]),
                              sleep=lambda s: now.__setitem__(0, now[0] + s),
                              n1mm_up=lambda: False, wait_s=10, clock=lambda: now[0])
            run.run()
            self.assertEqual(started, [10.0])
            self.assertIn("started", run.status)

class N1mmFirstTests(unittest.TestCase):
    def test_is_n1mm_anywhere(self) -> None:
        self.assertTrue(a.is_n1mm(r"C:\HAM\N1MM Logger+\N1MMLogger.net.exe"))
        self.assertTrue(a.is_n1mm(r"D:\Radio\N1MM\n1mmlogger.net.exe"))
        self.assertFalse(a.is_n1mm(r"C:\HAM\N1MM Logger+\N1MM Logger+.lnk"))
        self.assertFalse(a.is_n1mm(r"C:\tools\router.exe"))
        items = [{"path": r"C:\X\N1MMLogger.net.exe"}, {"path": r"C:\r.exe"}]
        self.assertTrue(a.n1mm_first(items))
        self.assertFalse(a.n1mm_first(items[::-1]))
        self.assertFalse(a.n1mm_first([]))

    def test_ready_after_first_broadcast_and_settle(self) -> None:
        w = a.N1mmWait(started=100.0, timeout_s=180, settle_s=3)
        self.assertFalse(w.check(110, radio_t=50, running=True)[0])   # old packet: not ours
        done, text = w.check(130, radio_t=129, running=True)
        self.assertFalse(done)
        self.assertIn("up", text)
        self.assertFalse(w.check(132)[0])
        done, text = w.check(133.5)
        self.assertTrue(done)
        self.assertIn("N1MM is up", text)

    def test_app_info_counts_too(self) -> None:
        w = a.N1mmWait(started=0.0, settle_s=0)
        self.assertTrue(w.check(20, app_t=19)[0])

    def test_gives_up(self) -> None:
        w = a.N1mmWait(started=0.0, timeout_s=60, no_process_s=45)
        self.assertFalse(w.check(30, running=False)[0])
        done, text = w.check(46, running=False)
        self.assertTrue(done)
        self.assertIn("didn't start", text)
        w = a.N1mmWait(started=0.0, timeout_s=60)
        self.assertFalse(w.check(59, running=True)[0])
        done, text = w.check(60, running=True)
        self.assertTrue(done)
        self.assertIn("Broadcast Data", text)

if __name__ == "__main__":
    unittest.main()
