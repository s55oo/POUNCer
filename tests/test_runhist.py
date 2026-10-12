"""Tests for run/S&P history from the contest-log archive (no network)."""

import os
import sqlite3
import tempfile
import time
import unittest
import zlib

from pouncer.runhist import RunHistory, archive_target, pick_logs, run_share, shard_name


def cabrillo(freqs):
    return "\n".join(
        f"QSO: {f:.1f} CW 2025-11-29 1200 S55OO 599 15 K1ABC 599 05" for f in freqs
    )


class HelperTests(unittest.TestCase):
    def test_shard_name(self) -> None:
        self.assertEqual(shard_name("k3lr"), f"logs_{zlib.crc32(b'K3LR') & 0xff:02x}.sqlite")
        self.assertEqual(shard_name("K3LR"), "logs_38.sqlite")

    def test_archive_target(self) -> None:
        self.assertEqual(archive_target("CQWWCW", "CW"), ("CQWW", "CW"))
        self.assertEqual(archive_target("CQWPXSSB", "USB"), ("CQWPX", "PH"))
        self.assertEqual(archive_target("CQWWRTTY", "RTTY"), ("CQWWRTTY", "RTTY"))
        self.assertEqual(archive_target("", "CW"), (None, "CW"))

    def test_pick_logs_prefers_contest_then_mode(self) -> None:
        rows = [
            ("CQWW/cw/2023/k1abc.log", "CQWW", 2023, "CW"),
            ("CQWW/cw/2025/k1abc.log", "CQWW", 2025, "CW"),
            ("CQWW/cw/2024/k1abc.log", "CQWW", 2024, "CW"),
            ("CQWPX/cw/2025/k1abc.log", "CQWPX", 2025, "CW"),
            ("RECONSTRUCTED_LOGS/CQWW/k1abc.log", "CQWW", 2026, "CW"),
        ]
        self.assertEqual(pick_logs(rows, "CQWW", "CW"), ["CQWW/cw/2025/k1abc.log"])
        self.assertEqual(pick_logs(rows, "WAE", "CW")[0], "CQWW/cw/2025/k1abc.log")
        self.assertEqual(pick_logs(rows, "CQWW", "PH"), [])

    def test_run_share(self) -> None:
        self.assertEqual(run_share(cabrillo([14025.0] * 10)), (9, 10))           # runner
        self.assertEqual(run_share(cabrillo([7001 + i for i in range(10)])), (0, 10))  # S&P
        # two stations interleaved on 20 m, one running: still counted
        mixed = [14025.0, 14031.0, 14025.0, 14036.0, 14025.0, 14040.0]
        self.assertEqual(run_share(cabrillo(mixed))[0], 2)


class RunHistoryTests(unittest.TestCase):
    def test_lookup_in_background_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            shard = os.path.join(d, "src.sqlite")
            con = sqlite3.connect(shard)
            con.execute("CREATE TABLE logs (path TEXT, callsign TEXT, contest TEXT,"
                        " year INTEGER, mode TEXT, season TEXT, subcontest TEXT, detail TEXT)")
            con.execute("INSERT INTO logs VALUES ('CQWW/cw/2025/oh2bh.log','OH2BH','CQWW',"
                        "2025,'CW',NULL,NULL,NULL)")
            con.commit()
            con.close()
            with open(shard, "rb") as fh:
                shard_bytes = fh.read()
            urls = []

            def fetch(url):
                urls.append(url)
                if url.endswith(shard_name("OH2BH")):
                    return shard_bytes
                return cabrillo([7001 + i for i in range(40)]).encode()  # all S&P

            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch, base_url="X/")
            self.assertIsNone(rh.get("OH2BH", "CQWWCW", "CW"))  # queued
            for _ in range(100):
                if rh.get("OH2BH", "CQWWCW", "CW") is not None:
                    break
                time.sleep(0.05)
            self.assertEqual(rh.get("OH2BH", "CQWWCW", "CW"), 0)
            self.assertEqual(urls, ["X/SH6/" + shard_name("OH2BH"), "X/CQWW/cw/2025/oh2bh.log"])
            # a new instance reads the disk cache: no downloads
            urls.clear()
            again = RunHistory(os.path.join(d, "cache"), fetch=fetch, base_url="X/")
            self.assertEqual(again.get("OH2BH", "CQWWCW", "CW"), 0)
            self.assertEqual(urls, [])

    def test_parallel_lookups_share_one_shard_download(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            shard = os.path.join(d, "src.sqlite")
            con = sqlite3.connect(shard)
            con.execute("CREATE TABLE logs (path TEXT, callsign TEXT, contest TEXT,"
                        " year INTEGER, mode TEXT, season TEXT, subcontest TEXT, detail TEXT)")
            con.commit()
            con.close()
            with open(shard, "rb") as fh:
                shard_bytes = fh.read()
            calls = []
            def fetch(url):
                calls.append(url)
                time.sleep(0.3)  # slow network
                return shard_bytes
            # four calls that live in the same shard
            same = [c for c in (f"K{i}AA" for i in range(4000))
                    if shard_name(c) == shard_name("K0AA")][:4]
            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch, base_url="X/")
            t0 = time.time()
            for c in same:
                rh.get(c, "CQWWCW", "CW")
            for _ in range(100):
                with rh._lock:
                    if len(rh._results) == 4:
                        break
                time.sleep(0.05)
            self.assertEqual(len(rh._results), 4)
            self.assertEqual(len(calls), 1)  # shard fetched once
            self.assertLess(time.time() - t0, 2)


if __name__ == "__main__":
    unittest.main()
