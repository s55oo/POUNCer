"""CPU / memory / disk monitor: a CSV row a minute, POUNCer's own counters added."""

import csv
import os
import sys
import tempfile
import time
import unittest

from pouncer import perf


class PerfTests(unittest.TestCase):
    def test_row_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "taps.jsonl"), "w") as fh:
                fh.write("x" * 2048)
            m = perf.PerfMonitor(d, stats=lambda: {"spots": 42, "tick_avg_ms": 3.5})
            sum(i * i for i in range(200000))            # burn a little CPU
            row = m.sample()
            for k in ("time", "cpu_proc_core_pct", "mem_ws_mb", "disk_free_gb", "cache_mb", "threads"):
                self.assertIn(k, row)
            self.assertGreaterEqual(row["cpu_proc_core_pct"], 0)
            self.assertEqual(row["spots"], 42)
            if sys.platform == "win32":                  # the Windows API numbers are there
                for k in ("mem_ws_mb", "mem_private_mb", "pc_mem_pct", "handles", "disk_write_kb"):
                    self.assertNotEqual(row[k], "", k)
                self.assertGreater(row["mem_ws_mb"], 1)
            m.write(row)
            time.sleep(0.3)                               # Windows counts CPU time in ~16 ms steps
            row2 = m.sample()
            if sys.platform == "win32":
                self.assertNotEqual(row2["cpu_pc_pct"], "", "cpu_pc_pct")    # needs two samples
            m.write(row2)
            with open(m.path, newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["spots"], "42")
            self.assertIn("tick_avg_ms", rows[0])

    def test_stats_error_never_stops_it(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            def bad():
                raise RuntimeError("boom")
            row = perf.PerfMonitor(d, stats=bad).sample()
            self.assertIn("boom", row["stats_error"])

    def test_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            m = perf.PerfMonitor(d)
            with open(m.path, "w") as fh:
                fh.write("old header\n" + "x" * 100 + "\n")
            m.write(m.sample())                          # other columns: old file moved aside
            self.assertTrue(os.path.exists(m.path + ".1"))
            with open(m.path) as fh:
                self.assertTrue(fh.readline().startswith("time,uptime_min,"))


@unittest.skipUnless(__import__("sys").platform == "win32", "Windows API")
class WindowsProcessTests(unittest.TestCase):
    def test_processes_n1mm_and_busiest(self) -> None:
        import subprocess
        import sys
        import time
        w = perf.ProcessWatch(n1mm_prefix="python")    # this test's python stands in for N1MM
        busy = subprocess.Popen([sys.executable, "-c", "import time\nt=time.time()\nwhile time.time()-t<3: pass"])
        try:
            w.sample(0)
            time.sleep(1.5)
            row = w.sample(0)
        finally:
            busy.wait()
        self.assertGreater(row["pc_processes"], 10)
        self.assertEqual(row["n1mm_running"], 1)
        self.assertNotEqual(row.get("n1mm_mem_ws_mb", ""), "")
        self.assertIn("python", row["pc_top_cpu"].lower())

    def test_monitor_row_has_the_pc_columns(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            m = perf.PerfMonitor(d)
            row = m.sample()
            self.assertNotIn("procs_error", row)
            self.assertIn("pc_top_cpu", row)
            self.assertIn("n1mm_running", row)


if __name__ == "__main__":
    unittest.main()
