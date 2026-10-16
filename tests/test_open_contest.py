"""The log counts only the contest open in N1MM - never the whole database."""

import os
import sqlite3
import tempfile
import unittest

from pouncer.n1mm import RadioState, parse_app_info
from pouncer.worked import WorkedLog

APP = b"""<?xml version="1.0" encoding="utf-8"?>
<AppInfo><app>N1MM</app><dbname>S55OO.s3db</dbname><contestnr>2</contestnr>
<contestname>CQWWCW</contestname><StationName>PC</StationName><mycall>S55OO</mycall></AppInfo>"""
CONTACT = b"""<?xml version="1.0" encoding="utf-8"?>
<contactinfo><app>N1MM</app><contestname>CQWPXCW</contestname><contestnr>3</contestnr>
<call>K1ABC</call></contactinfo>"""


class ParseTests(unittest.TestCase):
    def test_appinfo_and_contact(self) -> None:
        self.assertEqual(parse_app_info(APP), {"kind": "AppInfo", "contest_nr": 2,
                                               "contest_name": "CQWWCW", "dbname": "S55OO.s3db"})
        self.assertEqual(parse_app_info(CONTACT)["contest_nr"], 3)
        self.assertIsNone(parse_app_info(b"<RadioInfo><Freq>1</Freq></RadioInfo>"))
        self.assertIsNone(parse_app_info(b"not xml"))

    def test_contact_keeps_the_database(self) -> None:
        st = RadioState()
        st.set_app(parse_app_info(APP))
        st.set_app(parse_app_info(CONTACT))
        app = st.app()
        self.assertEqual((app["dbname"], app["contest_nr"], app["contest_name"]),
                         ("S55OO.s3db", 3, "CQWPXCW"))


class OpenContestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        os.makedirs(os.path.join(base, "Databases"))
        self.db = os.path.join(base, "Databases", "S55OO.s3db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE DXLOG (Call TEXT, Band REAL, Mode TEXT, ContestNR INTEGER, TS TEXT)")
        rows = [(f"K{i}AA", 7.0, "CW", 1, "2025-11-29 10:00:00") for i in range(1000)]   # old CQWW
        rows += [("W1AW", 14.0, "CW", 2, "2026-10-16 06:00:00"), ("DL1AA", 7.0, "CW", 2, "2026-10-16 06:01:00")]
        con.executemany("INSERT INTO DXLOG VALUES (?,?,?,?,?)", rows)
        con.commit()
        con.close()
        self.ini = os.path.join(base, "N1MM Logger.ini")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ini(self, nr):
        with open(self.ini, "w") as fh:
            fh.write("[Settings]\nDXLog Name=S55OO.s3db\n" + (f"Contest NR={nr}\n" if nr is not None else ""))

    def test_stale_ini_but_n1mm_says_contest_2(self) -> None:
        self._ini(1)                                   # N1MM hasn't saved its ini yet
        st = RadioState()
        wl = WorkedLog("", self.ini)
        wl.app_info = st.app
        wl.maybe_reload()
        self.assertEqual(dict(wl.band_qsos())["40m"], 1000)   # the ini's (old) contest
        self.assertEqual(wl.contest_source, "ini")
        st.set_app(parse_app_info(APP))                 # N1MM: contest 2 is open
        self.assertTrue(wl.maybe_reload())              # at once, no 10 s wait
        self.assertEqual(wl.contest_nr, 2)
        self.assertEqual(wl.contest_source, "N1MM")
        self.assertEqual(dict(wl.band_qsos()), {"40m": 1, "20m": 1})
        self.assertEqual(wl.qso_count, 2)

    def test_no_contest_number_never_counts_the_whole_database(self) -> None:
        self._ini(None)
        wl = WorkedLog("", self.ini)
        wl.maybe_reload()
        self.assertEqual(wl.contest_nr, 2)                    # the newest QSO's contest
        self.assertEqual(wl.contest_source, "latest QSO")
        self.assertEqual(wl.qso_count, 2)


if __name__ == "__main__":
    unittest.main()
