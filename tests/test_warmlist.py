"""server/warmlist.py: CW and SSB contest sets, measured from log folders."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from server import warmlist  # noqa: E402


def log(call, freqs, mode="PH"):
    head = f"START-OF-LOG: 3.0\nCALLSIGN: {call}\n"
    qsos = "\n".join(f"QSO: {f} {mode} 2025-10-25 {1200 + i:04d} {call} 59 15 K1ABC 59 05"
                      for i, f in enumerate(freqs))
    return head + qsos + "\nEND-OF-LOG:\n"


class WarmListTests(unittest.TestCase):
    def test_ssb_set_uses_phone_folders(self) -> None:
        folders = {f for _c, _m, f in warmlist.SETS["ssb"]}
        self.assertEqual(folders, {"CQWW/ph", "CQWPX/ph", "ARRL/arrl_international_dx_phone_contest"})
        self.assertEqual({m for _c, m, _f in warmlist.SETS["ssb"]}, {"PH"})
        self.assertEqual(warmlist.OUT, {"cw": "warm.sqlite", "ssb": "warm-ssb.sqlite"})
        self.assertEqual(warmlist.CONTESTS, warmlist.SETS["cw"])

    def test_measure_ssb_logs(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            y = os.path.join(repo, "CQWW", "ph", "2025")
            os.makedirs(y)
            with open(os.path.join(y, "s51a.log"), "w") as fh:
                fh.write(log("S51A", [14250.0] * 40))                  # runs
            with open(os.path.join(y, "ja7owd.log"), "w") as fh:
                fh.write(log("JA7OWD", [28000] * 40))                  # no CAT
            found = warmlist.measure(repo, [2025], warmlist.SETS["ssb"])
            rows = {r[0]: r for r in warmlist.aggregate(found)}
            self.assertEqual(rows["S51A"][1:4], ("CQWW", "PH", 98))
            self.assertEqual(json.loads(rows["S51A"][8]), {"20m": 98})
            # band-only log (no CAT): no run %, but its hours on the air stay
            self.assertIsNone(rows["JA7OWD"][3])
            self.assertIsNotNone(rows["JA7OWD"][4])
            self.assertEqual(json.loads(rows["JA7OWD"][8]), {})

    def test_run_per_band(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            y = os.path.join(repo, "CQWW", "ph", "2025")
            os.makedirs(y)
            # runs on 20 m, S&P on 80 m
            freqs = [14250.0] * 40 + [3700.0 + i for i in range(30)]
            with open(os.path.join(y, "s50a.log"), "w") as fh:
                fh.write(log("S50A", freqs))
            rows = {r[0]: r for r in warmlist.aggregate(
                warmlist.measure(repo, [2025], warmlist.SETS["ssb"]))}
            self.assertEqual(json.loads(rows["S50A"][8]), {"20m": 98, "80m": 0})


if __name__ == "__main__":
    unittest.main()
