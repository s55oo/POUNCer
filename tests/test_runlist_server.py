"""Tests for the run list server (server/runlist.py): header parsing + HTTP."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import runlist  # noqa: E402


class RunListServerTests(unittest.TestCase):
    def test_log_call_from_header(self) -> None:
        self.assertEqual(runlist.log_call("START-OF-LOG: 3.0\nCALLSIGN: ea6/dl6tk\n"), "EA6/DL6TK")
        self.assertEqual(runlist.log_call("QSO: 7000 CW"), "")

    def test_serve_newest_year(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            con = sqlite3.connect(os.path.join(d, "runlist.sqlite"))
            con.execute("CREATE TABLE runs (call TEXT, contest TEXT, mode TEXT, year INTEGER,"
                        " run INTEGER, qsos INTEGER)")
            con.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?)", [
                ("S53CC", "CQWW", "CW", 2024, 70, 900), ("S53CC", "CQWW", "CW", 2025, 77, 1200),
                ("OH2BH", "CQWW", "CW", 2025, 8, 314), ("S53CC", "CQWW", "PH", 2025, 50, 500),
            ])
            con.commit()
            con.close()
            port = 18077
            threading.Thread(target=runlist.serve, args=(d, port), daemon=True).start()
            time.sleep(0.5)
            get = lambda p: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{p}").read())
            self.assertEqual(get("/runs?contest=CQWW&mode=CW"), {"S53CC": 77, "OH2BH": 8})
            self.assertEqual(get("/run?call=s53cc&contest=CQWW&mode=CW")["year"], 2025)
            self.assertEqual(len(get("/status")), 3)  # CW 2024, CW 2025, PH 2025


if __name__ == "__main__":
    unittest.main()
