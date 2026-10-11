"""Tests for the CTY country-file reader (EU / DX marking)."""

import os
import tempfile
import unittest

from pouncer.cty import CtyDB, load_cty, wpx_prefix

# Shaped like N1MM's wl_cty.dat, including its multi-line "#" comments that
# contain ";" (they used to split records in the wrong place).
SAMPLE = """Slovenia:                 15:  28:  EU:   46.00:   -14.00:    -1.0:  S5:
    S5;
United States:            05:  08:  NA:   37.60:    91.87:     5.0:  K:
#   K: K0(4)[7],K5(4)[7],K6(3)[6],
        K7(3)[6],K8(4),K9(4);
    AA,K,N,W,=VP2V/W1AW{EU};
European Russia:          16:  29:  EU:   53.65:   -41.37:    -4.0:  UA:
#   UA: UA1I(17)[20],UA1N[19],
        UA9X(17)[20];
    R,U,=R9XY;
Asiatic Russia:           17:  30:  AS:   55.88:   -84.08:    -7.0:  UA9:
    R0,R8,R9,U0,U8,U9,UA0,UA8,UA9;
Canary Islands:           33:  36:  AF:   28.32:    15.85:     0.0:  EA8:
    AM8,AN8,AO8,EA8,EB8,EC8,ED8,EE8,EF8,EG8,EH8;
Fed. Rep. of Germany:     14:  28:  EU:   51.00:   -10.00:    -1.0:  DL:
    DA,DB,DC,DD,DE,DF,DG,DH,DI,DJ,DK,DL,DM,DN,DO,DP,DQ,DR;
"""


class CtyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = CtyDB.from_text(SAMPLE)

    def cont(self, call):
        return self.db.continent(call)

    def test_basic_prefixes(self) -> None:
        self.assertEqual(self.cont("S55OO"), "EU")
        self.assertEqual(self.cont("K1ABC"), "NA")
        self.assertEqual(self.cont("W1AW"), "NA")

    def test_comment_blocks_do_not_break_records(self) -> None:
        self.assertEqual(self.cont("RV3DEP"), "EU")
        self.assertEqual(self.cont("UA9CDX"), "AS")
        self.assertEqual(self.cont("R9AA"), "AS")

    def test_longest_prefix_wins(self) -> None:
        self.assertEqual(self.cont("UA3AA"), "EU")
        self.assertEqual(self.cont("UA0AA"), "AS")

    def test_exact_call_and_continent_override(self) -> None:
        self.assertEqual(self.cont("R9XY"), "EU")          # =R9XY in EU Russia
        self.assertEqual(self.cont("VP2V/W1AW"), "EU")     # {EU} override

    def test_portable_calls(self) -> None:
        self.assertEqual(self.cont("EA8/DL1ABC"), "AF")
        self.assertEqual(self.cont("DL1ABC/EA8"), "AF")
        self.assertEqual(self.cont("DL1ABC/P"), "EU")
        self.assertEqual(self.cont("K1ABC/4"), "NA")
        self.assertIsNone(self.cont("DL1ABC/MM"))

    def test_zones_with_call_area_hints(self) -> None:
        self.assertEqual(self.db.lookup("S55OO").cq, 15)
        self.assertEqual(self.db.lookup("K1ABC").cq, 5)   # no hint for area 1
        self.assertEqual(self.db.lookup("W6ABC").cq, 3)   # "#  K: K6(3)[6]"
        self.assertEqual(self.db.lookup("W6ABC").itu, 6)
        self.assertEqual(self.db.lookup("N0AA").cq, 4)
        self.assertEqual(self.db.lookup("UA9CDX").cq, 17)

    def test_wpx_prefix(self) -> None:
        for call, prefix in [
            ("S55OO", "S55"), ("3Z75HSC", "3Z75"), ("K1ABC", "K1"),
            ("EA8/DL1ABC", "EA8"), ("DL1ABC/EA8", "EA8"), ("DL1ABC/P", "DL1"),
            ("K1ABC/4", "K4"), ("RAEM", "RA0"), ("PA/DL1ABC", "PA0"),
        ]:
            self.assertEqual(wpx_prefix(call), prefix, call)

    def test_unknown(self) -> None:
        self.assertIsNone(self.cont("QQ1QQ"))
        self.assertIsNone(self.cont(""))

    def test_load_cty_from_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wl_cty.dat")
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(SAMPLE)
            db = load_cty(path)
            self.assertIsNotNone(db)
            self.assertEqual(db.continent("K1ABC"), "NA")


if __name__ == "__main__":
    unittest.main()
