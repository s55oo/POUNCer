"""Contest summary: taps -> QSOs from POUNCer, mults from spots, rates."""

import calendar
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.request

from pouncer import summary
from pouncer.summary import TapLog, build, read_qsos


def t(hhmm: str) -> float:
    return float(calendar.timegm(time.strptime("2026-10-13 " + hhmm, "%Y-%m-%d %H:%M")))


def q(hhmm, call, band, zn=None, cty=None):
    return {"t": t(hhmm), "call": call, "band": band,
            "mults": {"ZN": zn, "CountryPrefix": cty}}


class BuildTests(unittest.TestCase):
    def test_counts_and_mults(self) -> None:
        qsos = [
            q("06:00", "K1ABC", "20m", 5, "K"),       # tapped -> POUNCer, 2 mults
            q("06:05", "DL1AA", "20m", 14, "DL"),     # not tapped, 2 mults
            q("06:10", "W1AW", "20m", 5, "K"),        # tapped too late (> 15 min)
            q("06:20", "S5/JA1XYZ/P", "40m", 25, "JA"),  # tapped as JA1XYZ, 40 m
            q("07:10", "OK1RR", "20m", 15, "OK"),
        ]
        taps = [
            (t("05:59"), "K1ABC", "20m"),
            (t("05:50"), "W1AW", "20m") ,             # 20 min before its QSO
            (t("06:19"), "JA1XYZ", "40m"),
            (t("06:30"), "VK2XX", "15m"),             # tapped, never worked
        ]
        d = build(qsos, taps, ("ZN", "CountryPrefix"))
        self.assertEqual(d["qsos"], 5)
        self.assertEqual(d["pouncer"], 2)
        self.assertEqual(d["pouncer_pct"], 40)
        self.assertEqual(d["mults"], 8)                # 2 + 2 + 0 + 2 + 2
        self.assertEqual(d["pouncer_mults"], 4)        # K1ABC 2 + JA 2
        kinds = {(m["call"], m["kind"], m["value"]) for m in d["mults_from_spots"]}
        self.assertIn(("K1ABC", "Zone", "5"), kinds)
        self.assertIn(("S5/JA1XYZ/P", "Country", "JA"), kinds)
        b20 = next(b for b in d["bands"] if b["band"] == "20m")
        self.assertEqual((b20["qsos"], b20["pouncer"], b20["mults"]), (4, 1, 6))
        self.assertEqual([b["band"] for b in d["bands"]], ["40m", "20m"])
        self.assertEqual([h["qsos"] for h in d["hours"]], [4, 1])
        self.assertEqual(d["best60"], 4)
        self.assertEqual(d["best60_at"], "06:00")
        self.assertEqual(d["tapped_stations"], 4)
        self.assertEqual(d["tapped_worked"], 2)
        self.assertEqual(d["on_hours"], 0.3)           # 06:00-06:20; 50 min gap = off

    def test_rate_line(self) -> None:
        now = 1_800_000_000.0
        qsos = [{"t": now - 60 * i, "call": f"K{i}AA", "band": "20m", "mode": "CW",
                 "mults": {"CountryPrefix": f"P{i % 3}"}} for i in range(12)]
        qsos.append({"t": now - 7200, "call": "DL1AA", "band": "40m", "mode": "CW",
                     "mults": {"CountryPrefix": "DL"}})
        s = build(qsos, [], ["CountryPrefix"], now=now)
        self.assertEqual((s["q10"], s["q60"], s["m60"]), (10, 12, 3))
        text = summary.rate_line(s)
        self.assertIn("Rate 60/h (10 min)", text)
        self.assertIn("12 last hour", text)
        self.assertIn("13 QSOs 4 mults (+3 this hour)", text)
        self.assertIn("40m 1 Q 1 M", text)
        self.assertIn("20m 12 Q 3 M (+3)", text)
        self.assertIn("no QSOs", summary.rate_line({"ok": True, "qsos": 0}))

    def test_empty_log(self) -> None:
        d = build([], [], ("ZN",))
        self.assertTrue(d["ok"])
        self.assertEqual((d["qsos"], d["pouncer"], d["rate"]), (0, 0, None))


class FileTests(unittest.TestCase):
    def test_taps_persist(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            a = TapLog(d)
            a.record("k1abc", 14025.0)
            a.record("X1X", 99999.0)                   # no band: ignored
            b = TapLog(d)
            self.assertEqual([(c, band) for _, c, band in b.snapshot()], [("K1ABC", "20m")])
            # old taps are dropped
            with open(os.path.join(d, "taps.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.time() - 30 * 86400, "c": "OLD", "b": "20m"}) + "\n")
            self.assertEqual(len(TapLog(d).snapshot()), 1)

    def test_read_qsos_from_n1mm_log(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = os.path.join(d, "test.s3db")
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE DXLOG (TS TEXT, Call TEXT, Band REAL, Mode TEXT,"
                        " ContestNR INTEGER, ZN INTEGER, CountryPrefix TEXT)")
            con.executemany("INSERT INTO DXLOG VALUES (?,?,?,?,?,?,?)", [
                ("2026-10-13 06:00:12", "K1ABC", 14.0, "CW", 8, 5, "K"),
                ("2026-10-13 06:01:00", "DL1AA", 7.0, "CW", 8, 14, "DL"),
                ("2026-10-13 06:02:00", "OTHER", 7.0, "CW", 3, 14, "DL"),   # other contest
            ])
            con.commit()
            con.close()
            got = read_qsos(path, 8, ("ZN", "CountryPrefix", "WPXPrefix"))
        self.assertEqual([(x["call"], x["band"]) for x in got], [("K1ABC", "20m"), ("DL1AA", "40m")])
        self.assertEqual(got[0]["t"], t("06:00") + 12)
        self.assertEqual(got[0]["mults"], {"ZN": 5, "CountryPrefix": "K"})


class WebTests(unittest.TestCase):
    def test_summary_page_and_api(self) -> None:
        from pouncer.web import INDEX_HTML, WebServer
        n = lambda *a, **k: None
        web = WebServer("127.0.0.1", 0, lambda: {"spots": []}, n, n, n, n, n, n)
        web.set_summary_provider(lambda: {"ok": True, "qsos": 3})
        web.start()
        base = f"http://127.0.0.1:{web.port}"
        try:
            with urllib.request.urlopen(base + "/api/summary", timeout=5) as r:
                self.assertEqual(json.load(r)["qsos"], 3)
            with urllib.request.urlopen(base + "/summary", timeout=5) as r:
                self.assertIn("Contest summary", r.read().decode())
        finally:
            web.stop()
        self.assertIn('href="/summary"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
