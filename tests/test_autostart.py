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


if __name__ == "__main__":
    unittest.main()
