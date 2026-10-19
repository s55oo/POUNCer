"""Tests for run/S&P history from the contest-log archive (no network)."""

import os
import sqlite3
import tempfile
import time
import unittest
import zlib

from pouncer.runhist import (
    RunHistory, archive_target, on_air_hours, pick_logs, run_share, shard_name,
)


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
        self.assertEqual(pick_logs(rows, "CQWW", "CW", 2026),
                         (["CQWW/cw/2025/k1abc.log", "CQWW/cw/2024/k1abc.log",
                           "CQWW/cw/2023/k1abc.log"], False))
        # no WAE log: its other CW contests of the last 6 years, newest first
        self.assertEqual(pick_logs(rows, "WAE", "CW", 2026)[0][:2],
                         ["CQWW/cw/2025/k1abc.log", "CQWPX/cw/2025/k1abc.log"])
        self.assertEqual(pick_logs(rows, "CQWW", "PH", 2026), ([], False))

    def test_pick_logs_only_last_6_years(self) -> None:
        rows = [(f"CQWW/cw/{y}/k1abc.log", "CQWW", y, "CW") for y in range(2010, 2026)]
        paths, _ = pick_logs(rows, "CQWW", "CW", 2026)
        self.assertEqual(paths[0], "CQWW/cw/2025/k1abc.log")
        self.assertEqual(paths[-1], "CQWW/cw/2020/k1abc.log")  # 2026 - 6
        self.assertEqual(len(paths), 6)
        self.assertEqual(pick_logs([("CQWW/cw/2015/x.log", "CQWW", 2015, "CW")],
                                   "CQWW", "CW", 2026), ([], False))

    def test_reconstructed_only_when_no_real_log(self) -> None:
        rows = [("RECONSTRUCTED_LOGS/CQWW/cw/2025/M0KTZ.log", "CQWW", 2025, "CW"),
                ("RECONSTRUCTED_LOGS/CQWPX/cw/2025/M0KTZ.log", "CQWPX", 2025, "CW")]
        self.assertEqual(pick_logs(rows, "CQWW", "CW", 2026),
                         (["RECONSTRUCTED_LOGS/CQWW/cw/2025/M0KTZ.log"], True))

    def test_on_air_hours(self) -> None:
        log = "\n".join([
            "QSO: 7001 CW 2025-11-29 1200 S55OO 599 15 K1ABC 599 05",
            "QSO: 7002 CW 2025-11-29 1210 S55OO 599 15 K1ABD 599 05",
            "QSO: 7003 CW 2025-11-29 1230 S55OO 599 15 K1ABE 599 05",   # +20 min
            "QSO: 7004 CW 2025-11-29 1400 S55OO 599 15 K1ABF 599 05",   # 90 min off
            "QSO: 7005 CW 2025-11-29 1430 S55OO 599 15 K1ABG 599 05",   # +30 min
        ])
        self.assertEqual(on_air_hours(log), 1.0)  # 10 + 20 + 30 minutes

    def test_run_share(self) -> None:
        self.assertEqual(run_share(cabrillo([14025.0] * 10)), (9, 10))           # runner
        self.assertEqual(run_share(cabrillo([7001 + i for i in range(10)])), (0, 10))  # S&P
        # two stations interleaved on 20 m, one running: still counted
        mixed = [14025.0, 14031.0, 14025.0, 14036.0, 14025.0, 14040.0]
        self.assertEqual(run_share(cabrillo(mixed))[0], 2)

    def test_band_only_frequencies_are_not_a_run(self) -> None:
        # logs without CAT: every QSO on "28000" - not 100% running (common on SSB)
        self.assertEqual(run_share(cabrillo([28000.0] * 50)), (0, 0))
        self.assertEqual(run_share(cabrillo([14000.0] * 5 + [14250.0] * 5)), (4, 5))


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
            self.assertEqual(rh.get("OH2BH", "CQWWCW", "CW")["run"], 0)
            self.assertEqual(urls, ["X/SH6/" + shard_name("OH2BH"), "X/CQWW/cw/2025/oh2bh.log"])
            # a new instance reads the disk cache: no downloads
            urls.clear()
            again = RunHistory(os.path.join(d, "cache"), fetch=fetch, base_url="X/")
            self.assertEqual(again.get("OH2BH", "CQWWCW", "CW")["run"], 0)
            self.assertEqual(urls, [])

    def test_parallel_lookups_share_one_shard_download(self) -> None:
        # the lookups run in background threads that may still write to the
        # cache folder while it is removed (macOS: "Directory not empty")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
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


    def test_server_list_makes_calls_known_without_github(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            urls = []

            def fetch(url):
                urls.append(url)
                if url.startswith("http://srv/runs"):
                    return b'{"S53CC": 77, "OH2BH": 8}'
                raise OSError("no github in this test")

            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch, base_url="X/",
                            server="http://srv/")
            rh.get("S53CC", "CQWWCW", "CW")
            for _ in range(100):
                if rh.get("S53CC", "CQWWCW", "CW") is not None:
                    break
                time.sleep(0.05)
            self.assertEqual(rh.get("S53CC", "CQWWCW", "CW"), {"run": 77})
            self.assertEqual(rh.get("OH2BH", "CQWWCW", "CW"), {"run": 8})  # never asked, known
            self.assertEqual(urls[0], "http://srv/runs?contest=CQWW&mode=CW")
            self.assertFalse(any(u.startswith("X/") for u in urls))  # no GitHub

    def test_local_run_list_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "runlist.sqlite")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE runs (call TEXT, contest TEXT, mode TEXT, year INTEGER,"
                        " run INTEGER, qsos INTEGER)")
            con.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?)", [
                ("S53CC", "CQWW", "CW", 2024, 70, 900), ("S53CC", "CQWW", "CW", 2025, 77, 1200),
            ])
            con.commit()
            con.close()
            def fetch(url):
                raise OSError("no network")
            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch, run_list=db)
            self.assertEqual(rh.get("S53CC", "CQWWCW", "CW"), {"run": 77})  # instant, newest
            self.assertIsNone(RunHistory(os.path.join(d, "c2"), fetch=fetch,
                                         run_list=os.path.join(d, "missing.sqlite"))
                              .get("X1X", "CQWWCW", "CW"))


    def test_merge_from_team_and_learn_hook(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            learned = []

            def fetch(url):
                raise OSError("no network")

            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch)
            rh.on_learn = lambda k, v: learned.append(k)
            self.assertEqual(rh.merge({"M0KTZ|CQWW|CW": {"run": 6}, "bad": 1}), 1)
            self.assertEqual(rh.get("M0KTZ", "CQWWCW", "CW"), {"run": 6})
            self.assertEqual(rh.merge({"M0KTZ|CQWW|CW": {"run": 99}}), 0)  # never overwrites
            self.assertIn("M0KTZ|CQWW|CW", rh.snapshot())
            self.assertEqual(learned, [])  # merged results aren't re-broadcast


    def _warm_db(self, path, bands=True):
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INTEGER,"
                    " hours REAL, rec INTEGER, logs INTEGER, newest INTEGER"
                    + (", bands TEXT)" if bands else ")"))
        rows = [("M0KTZ", "CQWW", "CW", 6, 3.5, 1, 2, 2025),
                ("S50R", "ARRL", "CW", 80, 20.0, 0, 5, 2026)]
        if bands:
            rows = [r + ("{}",) for r in rows]
        con.executemany(f"INSERT INTO warm VALUES ({','.join('?' * len(rows[0]))})", rows)
        con.commit()
        con.close()

    def test_warm_up_downloads_once_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.sqlite")
            self._warm_db(src)
            with open(src, "rb") as fh:
                blob = fh.read()
            urls = []

            def fetch(url):
                urls.append(url)
                return blob

            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch)
            rh.warm_up("http://x/warm.sqlite")
            for _ in range(100):
                if rh.get("M0KTZ", "CQWWCW", "CW") is not None:
                    break
                time.sleep(0.05)
            self.assertEqual(rh.get("M0KTZ", "CQWWCW", "CW"),
                             {"run": 6, "hours": 3.5, "rec": True})
            self.assertEqual(rh.get("S50R", "ARRLDXCW", "CW")["run"], 80)
            self.assertIn("2 stations", rh.warm_status)
            txt = os.path.join(d, "cache", "warm-up.txt")
            for _ in range(100):
                if "downloaded" in rh.warm_status and os.path.exists(txt):
                    break
                time.sleep(0.05)
            self.assertIn("downloaded", rh.warm_status)
            with open(txt, encoding="utf-8") as fh:
                info = fh.read()
            self.assertIn("Stations:    2", info)
            self.assertIn("http://x/warm.sqlite", info)
            # fresh copy in the cache: a new start loads it without downloading
            urls.clear()
            rh2 = RunHistory(os.path.join(d, "cache"), fetch=fetch)
            rh2.warm_up("http://x/warm.sqlite")
            for _ in range(100):
                if rh2.get("M0KTZ", "CQWWCW", "CW") is not None:
                    break
                time.sleep(0.05)
            self.assertEqual(rh2.get("M0KTZ", "CQWWCW", "CW")["hours"], 3.5)
            self.assertEqual(urls, [])

    def _blob(self, d, rows, name):
        path = os.path.join(d, name)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INTEGER,"
                    " hours REAL, rec INTEGER, logs INTEGER, newest INTEGER)")
        con.executemany("INSERT INTO warm VALUES (?,?,?,?,?,?,?,?)", rows)
        con.execute("CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO info VALUES ('contests', 'CQWW SSB')")
        con.commit()
        con.close()
        with open(path, "rb") as fh:
            return fh.read()

    def test_separate_ssb_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cw = self._blob(d, [("M0KTZ", "CQWW", "CW", 6, 3.5, 0, 2, 2025),
                                ("S50R", "CQWW", "CW", 80, 20.0, 0, 5, 2025)], "cw.sqlite")
            ssb = self._blob(d, [("M0KTZ", "CQWW", "PH", 70, 30.0, 0, 3, 2025)], "ssb.sqlite")
            rh = RunHistory(os.path.join(d, "cache"),
                            fetch=lambda url: ssb if "ssb" in url else cw)
            rh.warm_up("http://x/warm.sqlite", ssb_url="http://x/warm-ssb.sqlite")
            for _ in range(100):
                if "SSB" in rh.warm_status and "CW" in rh.warm_status:
                    break
                time.sleep(0.05)
            # same station, different activity on CW and on SSB
            self.assertEqual(rh.get("M0KTZ", "CQWWCW", "CW")["run"], 6)
            self.assertEqual(rh.get("M0KTZ", "CQWWSSB", "SSB")["run"], 70)
            self.assertEqual(rh.get("M0KTZ", "CQWWSSB", "USB")["hours"], 30.0)
            self.assertIn("CW 2", rh.warm_status)
            self.assertIn("SSB 1", rh.warm_status)
            self.assertIn("known in advance", rh.warm_status)
            for _ in range(100):
                if os.path.exists(os.path.join(d, "cache", "warm-up-ssb.txt")):
                    break
                time.sleep(0.05)
            with open(os.path.join(d, "cache", "warm-up-ssb.txt"), encoding="utf-8") as fh:
                self.assertIn("(SSB)", fh.read())
            self.assertTrue(os.path.exists(os.path.join(d, "cache", "warm-ssb.sqlite")))

    def test_ssb_list_missing_keeps_cw(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cw = self._blob(d, [("S50R", "CQWW", "CW", 80, 20.0, 0, 5, 2025)], "cw.sqlite")

            def fetch(url):
                if "ssb" in url:
                    raise OSError("HTTP 404")
                return cw
            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch)
            rh.warm_up("http://x/warm.sqlite", ssb_url="http://x/warm-ssb.sqlite")
            for _ in range(100):
                if "CW 1" in rh.warm_status and "SSB list" in rh.warm_status:
                    break
                time.sleep(0.05)
            self.assertIn("CW 1 stations known in advance", rh.warm_status)
            self.assertIn("SSB list: warm-up download failed", rh.warm_status)

    def test_run_per_band_from_warm_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "warm.sqlite")
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INTEGER,"
                        " hours REAL, rec INTEGER, logs INTEGER, newest INTEGER, bands TEXT)")
            con.executemany("INSERT INTO warm VALUES (?,?,?,?,?,?,?,?,?)", [
                ("S50A", "CQWW", "CW", 60, 30.0, 0, 4, 2025, '{"20m":95,"80m":5}'),
                ("JA7OWD", "CQWW", "PH", None, 20.1, 0, 1, 2024, "{}"),
            ])
            con.commit()
            con.close()
            rh = RunHistory(os.path.join(d, "cache"), fetch=lambda u: b"")
            self.assertEqual(rh.load_run_list(path), 2)
            # a CQ on 80 m from a 20 m runner: its 80 m habit counts
            self.assertEqual(rh.get("S50A", "CQWWCW", "CW", band="80m")["run"], 5)
            self.assertEqual(rh.get("S50A", "CQWWCW", "CW", band="80m")["band"], "80m")
            self.assertEqual(rh.get("S50A", "CQWWCW", "CW", band="20m")["run"], 95)
            # band without enough QSOs: the overall run %
            self.assertEqual(rh.get("S50A", "CQWWCW", "CW", band="40m")["run"], 60)
            self.assertNotIn("band", rh.get("S50A", "CQWWCW", "CW", band="40m"))
            # logs without frequencies: hours only
            info = rh.get("JA7OWD", "CQWWSSB", "SSB", band="10m")
            self.assertIsNone(info["run"])
            self.assertEqual(info["hours"], 20.1)

    def test_old_warm_list_without_bands_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            os.makedirs(cache)
            self._warm_db(os.path.join(cache, "warm.sqlite"), bands=False)   # fresh, no bands column
            new = os.path.join(d, "new.sqlite")
            con = sqlite3.connect(new)
            con.execute("CREATE TABLE warm (call TEXT, contest TEXT, mode TEXT, run INTEGER,"
                        " hours REAL, rec INTEGER, logs INTEGER, newest INTEGER, bands TEXT)")
            con.execute("INSERT INTO warm VALUES ('S50A','CQWW','CW',60,30.0,0,4,2025,'{\"80m\":5}')")
            con.commit()
            con.close()
            with open(new, "rb") as fh:
                blob = fh.read()
            urls = []
            rh = RunHistory(cache, fetch=lambda u: urls.append(u) or blob)
            rh.warm_up("http://x/warm.sqlite", ssb_url=None)
            for _ in range(100):
                if rh.get("S50A", "CQWWCW", "CW", band="80m"):
                    break
                time.sleep(0.05)
            self.assertEqual([u for u in urls if "warm" in u], ["http://x/warm.sqlite"])
            self.assertEqual(rh.get("S50A", "CQWWCW", "CW", band="80m")["run"], 5)

    def test_summarize(self) -> None:
        from pouncer.runhist import run_share_by_band, summarize
        self.assertEqual(run_share_by_band(cabrillo([14025.0] * 5 + [3505.0 + i for i in range(3)])),
                         {"20m": (4, 5), "80m": (0, 3)})
        # (run, measurable, hours, all qsos, bands)
        self.assertIsNone(summarize([(0, 0, 2.0, 10, {})]))           # too few QSOs
        out = summarize([(0, 0, 20.0, 400, {})])                      # no CAT: hours only
        self.assertEqual((out["run"], out["hours"], out["bands"]), (None, 20.0, {}))
        out = summarize([(90, 100, 30.0, 100, {"20m": (90, 100)}),
                         (10, 50, 10.0, 50, {"80m": (10, 50)})])
        self.assertEqual(out["run"], 67)
        self.assertEqual(out["bands"], {"20m": 90, "80m": 20})
        self.assertEqual(out["hours"], 20.0)

    def test_warm_up_offline_is_harmless(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            def fetch(url):
                raise OSError("offline")
            rh = RunHistory(os.path.join(d, "cache"), fetch=fetch)
            rh.warm_up("http://x/warm.sqlite")
            for _ in range(100):
                if rh.warm_status:
                    break
                time.sleep(0.05)
            self.assertIn("failed", rh.warm_status)


if __name__ == "__main__":
    unittest.main()


class SaveThrottleTests(unittest.TestCase):
    """The run history file (MBs) is written at most every SAVE_EVERY_S - the
    contest start learns dozens of calls a minute (100+ MB written before)."""

    def test_many_learnt_calls_one_write_then_flush(self) -> None:
        import json as _json
        import tempfile
        from unittest import mock
        from pouncer import runhist as rhmod
        with tempfile.TemporaryDirectory() as d:
            rh = rhmod.RunHistory(os.path.join(d, "cache"), fetch=lambda u: b"", base_url="X/")
            rh.SAVE_EVERY_S = 3600
            writes = []
            real = rhmod.os.replace
            with mock.patch.object(rhmod.os, "replace",
                                   side_effect=lambda a, b: (writes.append(b), real(a, b))):
                for i in range(50):
                    with rh._lock:
                        rh._results[f"K{i}AA|CQWWCW|CW"] = {"run": i}
                    rh._save()
                self.assertEqual(len(writes), 1)          # the first one; the rest wait
                rh.flush()                                # exit: the rest goes in
                self.assertEqual(len(writes), 2)
            with open(rh._file, encoding="utf-8") as fh:
                self.assertEqual(len(_json.load(fh)), 50)
            self.assertIsNone(rh._save_timer)
