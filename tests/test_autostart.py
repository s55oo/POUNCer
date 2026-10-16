"""Autostart: your programs start with POUNCer, in order, after their delays."""

import os
import tempfile
import unittest

from pouncer import autostart as a
from pouncer import settings
from pouncer.config import load_config


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


class ConfigTests(unittest.TestCase):
    def test_ini_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[autostart]\nenabled = true\napp1 = C:\\HAM\\100% tools\\x.exe\napp1_delay = 8\n")
            cfg = load_config(p)
            self.assertEqual(cfg.autostart_app1, "C:\\HAM\\100% tools\\x.exe")   # % is fine
            self.assertEqual(cfg.autostart_delay1, 8)
            res = settings.save(cfg, p, "autostart.app2", '"C:\\HAM\\cw.bat" --fast')
            self.assertTrue(res["ok"])
            self.assertEqual(load_config(p).autostart_app2, '"C:\\HAM\\cw.bat" --fast')
            self.assertFalse(settings.save(cfg, p, "autostart.app2_delay", "9999")["ok"])


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

    def test_eight_programs_in_ini_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[autostart]\napp8 = C:\\t\\eight.exe\napp8_wait_n1mm = true\n")
            cfg = load_config(p)
            self.assertEqual(cfg.autostart_app8, "C:\\t\\eight.exe")
            self.assertTrue(cfg.autostart_wait8)
            self.assertFalse(cfg.autostart_wait1)
            self.assertTrue(settings.save(cfg, p, "autostart.app6_wait_n1mm", "yes")["ok"])
            self.assertTrue(load_config(p).autostart_wait6)


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

    def test_ini_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[autostart]\nwait_n1mm = false\nn1mm_timeout = 5\n")
            cfg = load_config(p)
            self.assertFalse(cfg.autostart_wait_n1mm)
            self.assertEqual(cfg.autostart_n1mm_timeout, 10)       # at least 10 s
            self.assertTrue(load_config(os.path.join(d, "none.ini")).autostart_wait_n1mm)
            self.assertTrue(settings.save(cfg, p, "autostart.n1mm_timeout", "240")["ok"])
            self.assertEqual(load_config(p).autostart_n1mm_timeout, 240)


class AppWaitTests(unittest.TestCase):
    """PouncerApp: N1MM first -> start it, the rest of POUNCer only when it is up."""

    def _fake(self, items, running):
        import types
        from tests.test_so2r import _import_app
        PouncerApp = _import_app().PouncerApp
        from pouncer.n1mm import RadioState
        calls = {"launch": [], "services": 0, "rest": None, "after": []}

        class Label:
            text = ""

            def configure(self, text=""):
                Label.text = text

        class Root:
            def after(self, ms, fn):
                calls["after"].append(fn)

        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(autostart_wait_n1mm=True, autostart_n1mm_timeout=180,
                                      autostart_folder_delay=3,
                                      **{f"autostart_app{n}": "" for n in range(1, 5)},
                                      **{f"autostart_delay{n}": 0 for n in range(1, 5)}),
            state=RadioState(), status_label=Label(), root=Root(),
            _autostart_folder=lambda: "", _n1mm_running=lambda: running)
        for n, (path, delay) in enumerate(items, 1):
            setattr(f.cfg, f"autostart_app{n}", path)
            setattr(f.cfg, f"autostart_delay{n}", delay)
        f._start_services = lambda: calls.__setitem__("services", calls["services"] + 1)
        f._run_autostart = lambda rest: calls.__setitem__("rest", rest)
        for name in ("_start_autostart", "_launch_n1mm", "_n1mm_wait_tick",
                     "_n1mm_wait_done", "_stop_waiting_n1mm"):
            setattr(f, name, types.MethodType(getattr(PouncerApp, name), f))
        return f, calls, Label

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.n1mm = os.path.join(self.tmp.name, "N1MMLogger.net.exe")
        self.router = os.path.join(self.tmp.name, "router.exe")
        for p in (self.n1mm, self.router):
            open(p, "w").close()
        self._orig = a._launch

    def tearDown(self) -> None:
        a._launch = self._orig
        self.tmp.cleanup()

    def test_waits_for_n1mm_then_starts_the_rest(self) -> None:
        f, calls, label = self._fake([(self.n1mm, 0), (self.router, 5)], running=False)
        a._launch = lambda path, args: calls["launch"].append(path)
        self.assertTrue(f._start_autostart(defer_pouncer=True))
        self.assertEqual(calls["services"], 0)
        calls["after"].pop(0)()                         # app1 delay over: N1MM starts
        self.assertEqual(calls["launch"], [self.n1mm])
        self.assertIn("waiting for N1MM", label.text)
        self.assertEqual(calls["services"], 0)          # POUNCer still waits
        f.state.update({"radio_nr": 1, "freq": 1400000})  # N1MM's first RadioInfo
        f._n1mm_wait.heard = 1.0                        # (settle time over)
        f._n1mm_wait_tick()
        self.assertEqual(calls["services"], 1)
        self.assertEqual([i["path"] for i in calls["rest"]], [self.router])

    def test_no_wait_when_n1mm_runs_or_not_first(self) -> None:
        f, calls, _ = self._fake([(self.n1mm, 0), (self.router, 5)], running=True)
        self.assertFalse(f._start_autostart(defer_pouncer=True))
        self.assertEqual(len(calls["rest"]), 2)         # normal list (N1MM skipped there)
        f, calls, _ = self._fake([(self.router, 0), (self.n1mm, 5)], running=False)
        self.assertFalse(f._start_autostart(defer_pouncer=True))
        f, calls, _ = self._fake([(self.n1mm, 0)], running=False)
        f.cfg.autostart_wait_n1mm = False
        self.assertFalse(f._start_autostart(defer_pouncer=True))

    def test_dont_wait_menu(self) -> None:
        f, calls, _ = self._fake([(self.n1mm, 0), (self.router, 5)], running=False)
        a._launch = lambda path, args: None
        f._start_autostart(defer_pouncer=True)
        calls["after"].pop(0)()
        f._stop_waiting_n1mm()
        self.assertEqual(calls["services"], 1)
        f._stop_waiting_n1mm()                          # only once
        f._n1mm_wait_tick()
        self.assertEqual(calls["services"], 1)


if __name__ == "__main__":
    unittest.main()
