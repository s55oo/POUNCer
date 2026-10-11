"""Tests for the N1MM worked-log reader."""

import os
import sqlite3
import tempfile
import unittest

from pouncer.worked import (
    LOG_MISSING, LOG_N1MM_NOT_RUNNING, LOG_NO_INI, LOG_NONE_OPEN, LOG_OK,
    WorkedLog, band_label, find_active_db, inspect_active, normalize_mode,
)


def _make_db(path: str, rows: list[tuple[str, float, str]]) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("DROP TABLE IF EXISTS DXLOG")
        con.execute("CREATE TABLE DXLOG (Call TEXT, Band REAL, Mode TEXT)")
        con.executemany(
            "INSERT INTO DXLOG (Call, Band, Mode) VALUES (?, ?, ?)", rows
        )
        con.commit()
    finally:
        con.close()


class AuxTests(unittest.TestCase):
    def test_band_label_numeric_and_text(self) -> None:
        self.assertEqual(band_label(14.0), "20m")
        self.assertEqual(band_label(3.5), "80m")
        self.assertEqual(band_label("20M"), "20m")
        self.assertIsNone(band_label(None))

    def test_normalize_mode(self) -> None:
        self.assertEqual(normalize_mode("USB"), "PHONE")
        self.assertEqual(normalize_mode("lsb"), "PHONE")
        self.assertEqual(normalize_mode("CW"), "CW")
        self.assertEqual(normalize_mode(""), "UNKNOWN")


class WorkedLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        os.makedirs(os.path.join(base, "Databases"))
        self.db = os.path.join(base, "Databases", "test.s3db")
        _make_db(self.db, [
            ("K1ABC", 14.0, "CW"),
            ("K1ABC", 21.0, "CW"),
            ("W9XYZ", 14.0, "USB"),
        ])
        self.ini = os.path.join(base, "N1MM Logger.ini")
        with open(self.ini, "w", encoding="utf-8") as fh:
            fh.write("[Layout]\nDXLog Name=test.s3db\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_find_active_db(self) -> None:
        self.assertEqual(find_active_db(self.ini), self.db)

    def test_is_worked_same_band_and_mode(self) -> None:
        wl = WorkedLog("", self.ini, refresh_seconds=1)
        self.assertTrue(wl.maybe_reload())
        self.assertTrue(wl.is_worked("K1ABC", 14015.0, "CW"))
        self.assertTrue(wl.is_worked("k1abc", 14015.0, "CW"))
        self.assertTrue(wl.is_worked("K1ABC", 21020.0, "CW"))
        self.assertFalse(wl.is_worked("K1ABC", 14015.0, "RTTY"))
        self.assertFalse(wl.is_worked("N0CALL", 14015.0, "CW"))

    def test_band_qso_counts(self) -> None:
        _make_db(self.db, [
            ("K1ABC", 14.0, "CW"), ("K1ABC", 21.0, "CW"),
            ("W9XYZ", 14.0, "CW"), ("DL1AA", 7.0, "CW"),
            ("K1ABC", 14.0, "CW"),  # dupe still counts as a logged QSO
        ])
        wl = WorkedLog("", self.ini, refresh_seconds=1)
        wl.maybe_reload()
        self.assertEqual(wl.qso_count, 5)
        self.assertEqual(wl.band_qsos(), [("40m", 1), ("20m", 3), ("15m", 1)])

    def test_phone_modes_group(self) -> None:
        wl = WorkedLog("", self.ini, refresh_seconds=1)
        wl.maybe_reload()
        self.assertTrue(wl.is_worked("W9XYZ", 14250.0, "LSB"))

    def test_only_open_contest_is_used(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE IF EXISTS DXLOG")
        con.execute("CREATE TABLE DXLOG (Call TEXT, Band REAL, Mode TEXT, ContestNR INTEGER)")
        con.executemany("INSERT INTO DXLOG VALUES (?, ?, ?, ?)", [
            ("OLD1", 14.0, "CW", 1),
            ("OLD2", 14.0, "CW", 1),
            ("NEW1", 14.0, "CW", 2),
        ])
        con.commit()
        con.close()
        with open(self.ini, "w", encoding="utf-8") as fh:
            fh.write(
                "[Configurer]\nContest NR=2\nContest Type=CQWWCW\n"
                "DXLog Name=test.s3db\n"
            )
        wl = WorkedLog("", self.ini, refresh_seconds=1)
        self.assertTrue(wl.maybe_reload())
        self.assertEqual(wl.contest_nr, 2)
        self.assertTrue(wl.is_worked("NEW1", 14010.0, "CW"))
        self.assertFalse(wl.is_worked("OLD1", 14010.0, "CW"))

    def test_reload_after_change(self) -> None:
        wl = WorkedLog("", self.ini, refresh_seconds=0)
        wl.maybe_reload()
        self.assertFalse(wl.is_worked("DL1ABC", 14015.0, "CW"))
        _make_db(self.db, [
            ("K1ABC", 14.0, "CW"),
            ("W9XYZ", 14.0, "USB"),
            ("DL1ABC", 14.0, "CW"),
        ])
        os.utime(self.db, None)
        wl._last_check = 0.0
        wl.maybe_reload()
        self.assertTrue(wl.is_worked("DL1ABC", 14015.0, "CW"))

    def test_reload_is_not_throttled(self) -> None:
        # A newly logged QSO must be noticed on the very next call, with no
        # waiting for the refresh timer.
        wl = WorkedLog("", self.ini, refresh_seconds=10)
        self.assertTrue(wl.maybe_reload())
        self.assertFalse(wl.is_worked("DL1ABC", 14015.0, "CW"))
        _make_db(self.db, [
            ("K1ABC", 14.0, "CW"),
            ("DL1ABC", 14.0, "CW"),
        ])
        os.utime(self.db, None)
        self.assertTrue(wl.maybe_reload())
        self.assertTrue(wl.is_worked("DL1ABC", 14015.0, "CW"))

    def test_wal_sidecar_is_part_of_signature(self) -> None:
        _make_db(self.db, [("K1ABC", 14.0, "CW")])
        before = WorkedLog._db_signature(self.db, 8)
        wal = self.db + "-wal"
        with open(wal, "wb") as fh:
            fh.write(b"x" * 64)
        self.assertNotEqual(WorkedLog._db_signature(self.db, 8), before)

    def test_reload_sees_wal_only_insert(self) -> None:
        # N1MM (and SQLite in WAL mode) writes new QSOs to the -wal sidecar, so
        # the main file's size/mtime can stay put. The reload must still notice.
        _make_db(self.db, [("K1ABC", 14.0, "CW")])
        writer = sqlite3.connect(self.db)
        try:
            self.assertEqual(
                writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal"
            )
            writer.execute("INSERT INTO DXLOG VALUES ('K1ABC', 14.0, 'CW')")
            writer.commit()
            self.assertTrue(os.path.exists(self.db + "-wal"))
            wl = WorkedLog("", self.ini, refresh_seconds=0)
            self.assertTrue(wl.maybe_reload())
            self.assertFalse(wl.is_worked("R9MM", 14015.0, "CW"))
            writer.execute("INSERT INTO DXLOG VALUES ('R9MM', 14.0, 'CW')")
            writer.commit()
            wl._last_check = 0.0
            self.assertTrue(wl.maybe_reload())
            self.assertTrue(wl.is_worked("R9MM", 14015.0, "CW"))
        finally:
            writer.close()


class LogOpenStateTests(unittest.TestCase):
    """Detecting whether N1MM has a log open, and reconnecting."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        os.makedirs(os.path.join(base, "Databases"))
        self.db = os.path.join(base, "Databases", "test.s3db")
        _make_db(self.db, [("K1ABC", 14.0, "CW")])
        self.ini = os.path.join(base, "N1MM Logger.ini")
        self._write_ini("DXLog Name=test.s3db\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_ini(self, body: str) -> None:
        with open(self.ini, "w", encoding="utf-8") as fh:
            fh.write("[Configurer]\n" + body)

    def test_open_log_is_reported_ok(self) -> None:
        wl = WorkedLog("", self.ini, refresh_seconds=10, n1mm_running=lambda: True)
        self.assertTrue(wl.maybe_reload())
        self.assertTrue(wl.log_open)
        self.assertEqual(wl.log_state, LOG_OK)
        self.assertEqual(wl.log_message, "")

    def test_missing_ini(self) -> None:
        wl = WorkedLog("", os.path.join(self.tmp.name, "nope", "N1MM Logger.ini"))
        wl.maybe_reload()
        self.assertFalse(wl.log_open)
        self.assertEqual(wl.log_state, LOG_NO_INI)
        self.assertIn("not found", wl.log_message)

    def test_ini_without_log(self) -> None:
        self._write_ini("DXLog Name=\nContest NR=3\n")
        info = inspect_active(self.ini)
        self.assertEqual(info["ini"], self.ini)
        self.assertIsNone(info["path"])
        wl = WorkedLog("", self.ini)
        wl.maybe_reload()
        self.assertFalse(wl.log_open)
        self.assertEqual(wl.log_state, LOG_NONE_OPEN)
        self.assertIn("open", wl.log_message.lower())

    def test_log_file_missing(self) -> None:
        self._write_ini("DXLog Name=gone.s3db\n")
        wl = WorkedLog("", self.ini)
        wl.maybe_reload()
        self.assertEqual(wl.log_state, LOG_MISSING)
        self.assertIn("gone.s3db", wl.log_message)

    def test_n1mm_not_running_closes_log(self) -> None:
        running = {"v": True}
        wl = WorkedLog("", self.ini, refresh_seconds=10,
                       n1mm_running=lambda: running["v"])
        wl.maybe_reload()
        self.assertTrue(wl.is_worked("K1ABC", 14010.0, "CW"))
        running["v"] = False
        wl.request_recheck()
        self.assertTrue(wl.maybe_reload())  # state change -> refresh UI
        self.assertFalse(wl.log_open)
        self.assertEqual(wl.log_state, LOG_N1MM_NOT_RUNNING)
        self.assertFalse(wl.is_worked("K1ABC", 14010.0, "CW"))
        self.assertFalse(wl.maybe_reload())  # nothing new while still closed

    def test_unknown_running_state_is_ignored(self) -> None:
        wl = WorkedLog("", self.ini, n1mm_running=lambda: None)
        wl.maybe_reload()
        self.assertTrue(wl.log_open)

    def test_reconnect_picks_up_newly_opened_log(self) -> None:
        self._write_ini("DXLog Name=\n")
        wl = WorkedLog("", self.ini, refresh_seconds=10)
        wl.maybe_reload()
        self.assertFalse(wl.log_open)
        self._write_ini("DXLog Name=test.s3db\n")
        self.assertFalse(wl.maybe_reload())  # throttled until retry/reconnect
        wl.request_recheck()
        self.assertTrue(wl.maybe_reload())
        self.assertTrue(wl.log_open)
        self.assertTrue(wl.is_worked("K1ABC", 14010.0, "CW"))

    def test_reconnect_rereads_unchanged_log(self) -> None:
        wl = WorkedLog("", self.ini, refresh_seconds=10)
        self.assertTrue(wl.maybe_reload())
        self.assertFalse(wl.maybe_reload())
        wl.request_recheck()
        self.assertTrue(wl.maybe_reload())


class MultiplierTests(unittest.TestCase):
    """New-multiplier counting for the open contest (CQWW: zone + country)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        os.makedirs(os.path.join(base, "Databases"))
        self.db = os.path.join(base, "Databases", "log.s3db")
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE DXLOG (Call TEXT, Band REAL, Mode TEXT, ContestNR INTEGER, "
            "CountryPrefix TEXT, ZN INTEGER, WPXPrefix TEXT)"
        )
        con.executemany("INSERT INTO DXLOG VALUES (?,?,?,?,?,?,?)", [
            ("S51A", 14.0, "CW", 8, "S5", 15, "S51"),
            ("RL3O", 14.0, "CW", 8, "UA", 16, "RL3"),
            ("R9MM", 14.0, "CW", 8, "UA9", 17, "R9"),
            ("K1ABC", 21.0, "CW", 8, "K", 5, "K1"),
            ("JA1ABC", 14.0, "CW", 7, "JA", 25, "JA1"),  # other contest
        ])
        con.execute(
            "CREATE TABLE Contest (Name TEXT, Multiplier1Name TEXT, "
            "Multiplier2Name TEXT, Multiplier3Name TEXT)"
        )
        con.executemany("INSERT INTO Contest VALUES (?,?,?,?)", [
            ("CQWWCW", "ZN", "CountryPrefix", "N/A"),
            ("CQWPXCW", "WPXPrefix", "N/A", "N/A"),
        ])
        con.commit()
        con.close()
        self.ini = os.path.join(base, "N1MM Logger.ini")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _open(self, contest: str, nr: int = 8) -> WorkedLog:
        with open(self.ini, "w", encoding="utf-8") as fh:
            fh.write(
                f"[Configurer]\nContest NR={nr}\nContest Type={contest}\n"
                "DXLog Name=log.s3db\n"
            )
        wl = WorkedLog("", self.ini, refresh_seconds=10)
        wl.maybe_reload()
        return wl

    def test_cqww_single_and_double(self) -> None:
        wl = self._open("CQWWCW")
        self.assertEqual(wl.mult_types, ("ZN", "CountryPrefix"))
        self.assertEqual(wl.new_mults(14010, "K", 5, "K1"), 2)    # new zone + country
        self.assertEqual(wl.new_mults(14010, "OH", 15, "OH2"), 1)  # zone 15 worked
        self.assertEqual(wl.new_mults(14010, "UA9", 18, "RA9"), 1)  # UA9 worked
        self.assertEqual(wl.new_mults(14010, "UA", 16, "RV3"), 0)
        # per band: K / zone 5 are worked on 15m only
        self.assertEqual(wl.new_mults(21010, "K", 5, "K1"), 0)
        # QSOs of another contest in the same file don't count
        self.assertEqual(wl.new_mults(14010, "JA", 25, "JA1"), 2)

    def test_wpx_counts_once_per_contest(self) -> None:
        wl = self._open("CQWPXCW")
        self.assertEqual(wl.mult_types, ("WPXPrefix",))
        self.assertEqual(wl.new_mults(7010, "S5", 15, "S51"), 0)  # worked on 20m
        self.assertEqual(wl.new_mults(7010, "S5", 15, "S52"), 1)

    def test_unknown_contest_has_no_mults(self) -> None:
        wl = self._open("SOMETHING")
        self.assertEqual(wl.mult_types, ())
        self.assertEqual(wl.new_mults(14010, "K", 5, "K1"), 0)


class StationCallTests(unittest.TestCase):
    def _call(self, text: str) -> str:
        from pouncer.worked import n1mm_station_call
        with tempfile.TemporaryDirectory() as d:
            ini = os.path.join(d, "N1MM Logger.ini")
            with open(ini, "w", encoding="utf-8") as fh:
                fh.write(text)
            return n1mm_station_call(ini)

    def test_packet_logon_callsign_first(self) -> None:
        text = "[EntryWindow]\nOperator=S50XX\n[Packet]\nLogon Callsign=s55oo\n"
        self.assertEqual(self._call(text), "S55OO")

    def test_operator_when_logon_is_default(self) -> None:
        text = "[EntryWindow]\nOperator=S50XX\n[Packet]\nLogon Callsign=DEFAULT\n"
        self.assertEqual(self._call(text), "S50XX")

    def test_nothing_usable(self) -> None:
        self.assertEqual(self._call("[Packet]\nLogon Callsign=DEFAULT\n"), "")
        self.assertEqual(self._call("[Other]\nOperator=S55OO\n"), "")


if __name__ == "__main__":
    unittest.main()
