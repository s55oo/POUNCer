"""Contest multipliers: slots from N1MM's Contest table, learnt from the
IsMultiplier flags, exchange values from the log and N1MM's call history."""

import os
import sqlite3
import tempfile
import unittest

from pouncer import mults
from pouncer.worked import WorkedLog


def row(call, band, value_field, value, f1=0, f2=0, mode="CW", **more):
    vals = {value_field: mults.normalize(value_field, value)}
    vals.update({k: mults.normalize(k, v) for k, v in more.items()})
    return {"call": call, "band": band, "mode": mode, "values": vals, "flags": (bool(f1), bool(f2), False)}


class SlotTests(unittest.TestCase):
    def test_named_slot_and_default_scope(self) -> None:
        s = mults.resolve_slots(["ZN", "CountryPrefix", "N/A"], {"ZN", "CountryPrefix"}, [])
        self.assertEqual([(x["field"], x["scope"]) for x in s], [("ZN", "band"), ("CountryPrefix", "band")])
        self.assertEqual(mults.describe(s), "CQ zone per band + country per band")

    def test_scope_learnt_once_per_contest(self) -> None:
        rows = []
        for i in range(12):                       # each country on 20 m, then again on 40 m
            rows.append(row(f"A{i}", "20m", "CountryPrefix", f"C{i}", f1=1))
            rows.append(row(f"A{i}", "40m", "CountryPrefix", f"C{i}", f1=0))   # N1MM: not new
        s = mults.resolve_slots(["CountryPrefix", "N/A", "N/A"], {"CountryPrefix"}, rows)
        self.assertEqual(s[0]["scope"], "contest")
        self.assertIn("learnt", s[0]["how"])

    def test_unnamed_slot_learnt_from_flags(self) -> None:
        # SPDX-like: the table names only the country; N1MM flags mult 2 on new provinces
        rows = []
        for i, prov in enumerate("ABCDEFGHIJKLAB"):
            rows.append(row(f"SP{i}X", "40m", "CountryPrefix", "SP", f1=(i == 0),
                            f2=(i < 12), Sect=prov))
        s = mults.resolve_slots(["CountryPrefix", "N/A", "N/A"], {"CountryPrefix", "Sect"}, rows)
        self.assertEqual(s[1]["field"], "Sect")
        self.assertEqual(s[1]["how"], "learnt from the log")

    def test_named_slot_that_contradicts_the_flags_is_not_used(self) -> None:
        rows = [row(f"K{i}", "20m", "WPXPrefix", f"K{i}", f1=(i == 0)) for i in range(20)]
        s = mults.resolve_slots(["WPXPrefix", "N/A", "N/A"], {"WPXPrefix"}, rows)
        self.assertIsNone(s[0]["field"])
        self.assertIn("not known", mults.describe(s))


class WorkedExchangeTests(unittest.TestCase):
    """ARRL DX from Europe: the mult is the US state / VE province (Sect) - a
    spot has no exchange, so the value comes from the log or the call history."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = os.path.join(self.tmp.name, "Databases")
        os.makedirs(d)
        self.db = os.path.join(d, "log.s3db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE DXLOG (TS TEXT, Call TEXT, Band REAL, Mode TEXT, ContestNR INTEGER, "
                    "CountryPrefix TEXT, ZN INTEGER, WPXPrefix TEXT, Sect TEXT, "
                    "IsMultiplier1 INTEGER, IsMultiplier2 INTEGER, isMultiplier3 INTEGER)")
        con.executemany("INSERT INTO DXLOG VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("2026-02-21 00:01:00", "K3LR", 14.0, "CW", 2, "K", 5, "K3", "PA", 1, 0, 0),
            ("2026-02-21 00:02:00", "N2MF", 14.0, "CW", 2, "K", 5, "N2", "NY", 1, 0, 0),
        ])
        con.execute("CREATE TABLE Contest (Name TEXT, Multiplier1Name TEXT, Multiplier2Name TEXT, "
                    "Multiplier3Name TEXT)")
        con.execute("INSERT INTO Contest VALUES ('ARRLDXCW', 'Sect', 'N/A', 'N/A')")
        con.commit()
        con.close()
        a = sqlite3.connect(os.path.join(d, "N1MM Admin.s3db"))
        a.execute("CREATE TABLE CallHist (Call TEXT, Sect TEXT, Exchange1 TEXT)")
        a.execute("INSERT INTO CallHist VALUES ('W1AW', 'CT', '')")
        a.commit()
        a.close()
        self.wl = WorkedLog(self.db)
        self.wl._load(self.db, ("x",), 2, "ARRLDXCW")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_value_from_log_from_call_history_or_unknown(self) -> None:
        wl = self.wl
        self.assertEqual(wl.mult_summary(), "section per band")
        # K3LR worked on 20 m (PA): on 40 m PA is new, on 20 m it is not
        self.assertEqual(wl.new_mults(7010, "K", 5, "K3", call="K3LR"), 1)
        self.assertEqual(wl.new_mults(14010, "K", 5, "K3", call="K3LR"), 0)
        d = wl.mult_detail(7010, "K", 5, "K3", call="K3LR")[0]
        self.assertEqual((d["value"], d["source"], d["new"]), ("PA", "log", True))
        # W1AW never worked: N1MM's call history says CT
        d = wl.mult_detail(14010, "K", 5, "W1", call="W1AW")[0]
        self.assertEqual((d["value"], d["source"], d["new"]), ("CT", "call history", True))
        # unknown station: no exchange in a spot - not counted
        self.assertEqual(wl.new_mults(14010, "K", 5, "K9", call="K9XYZ"), 0)
        self.assertEqual(wl.mult_detail(14010, "K", 5, "K9", call="K9XYZ")[0]["source"], "unknown")


if __name__ == "__main__":
    unittest.main()
