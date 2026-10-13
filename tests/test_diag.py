"""Diagnostic log: traces, file, /diag page."""

import json
import os
import tempfile
import unittest
import urllib.request

from pouncer.diag import DiagLog, MAX_LINES


class DiagTests(unittest.TestCase):
    def test_trace_line_and_file(self) -> None:
        t = [1000.0]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "diag.log")
            log = DiagLog(path, clock=lambda: t[0])
            tr = log.trace("TAP", "K1ABC 14025.0 radio B")
            t[0] += 0.012
            tr.step("focus", "ok")
            t[0] += 0.5
            tr.step("n1mm", "14025.0")
            tr.done()
            tr.done()                                    # only once
            log.event("RBN", "logged in as S55OO")
            lines = log.text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].endswith("TAP K1ABC 14025.0 radio B | focus ok +12 | n1mm 14025.0 +512"))
            self.assertIn("RBN logged in as S55OO", lines[1])
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read().splitlines(), lines)

    def test_memory_is_bounded(self) -> None:
        log = DiagLog("")
        for n in range(MAX_LINES + 50):
            log.event("X", str(n))
        lines = log.text().splitlines()
        self.assertEqual(len(lines), MAX_LINES)
        self.assertTrue(lines[-1].endswith(f"X {MAX_LINES + 49}"))

    def test_diag_page_and_api(self) -> None:
        from pouncer.web import WebServer
        n = lambda *a, **k: None
        web = WebServer("127.0.0.1", 0, lambda: {"spots": []}, n, n, n, n, n, n)
        web.set_diag_provider(lambda: "12:00:00.000 TAP K1ABC")
        web.start()
        base = f"http://127.0.0.1:{web.port}"
        try:
            with urllib.request.urlopen(base + "/api/diag", timeout=5) as r:
                self.assertEqual(r.read().decode(), "12:00:00.000 TAP K1ABC")
            with urllib.request.urlopen(base + "/diag", timeout=5) as r:
                self.assertIn("Copy all", r.read().decode())
        finally:
            web.stop()


if __name__ == "__main__":
    unittest.main()
