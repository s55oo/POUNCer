"""People's spots on a telnet connection: taken only when the client has a
people filter (the app gives Telnet one since v0.7.212). v0.7.211: before,
'DX de UT4VL: 7074.0 YO2CK 0724Z' was read as a skimmer spot with mode '0724Z'
and went to port 10000 whatever the setting."""

import queue
import unittest

from pouncer.rbn import (PeopleFilter, RBNClient, SpotterFilter, parse_cluster_spot, parse_spot,
                         unmarked_ft)

PERSON = "DX de UT4VL:      7074.0  YO2CK                                       0724Z\n"
POTA = "DX de YO2CK:       7074.0  IU8RFW       [-POTA-]                       0716Z\n"
MODEWORD = "DX de UT4VL:      7074.0  YO2CK        FT8 -10dB                      0724Z\n"
SKIM = "DX de DL1ABC-#:   7025.0  K1ABC          CW    24 dB  28 WPM  CQ      0724Z\n"


class PeopleOnTelnetTests(unittest.TestCase):
    def client(self, people: bool):
        q = queue.Queue()
        raw = []
        sf = SpotterFilter("")
        sf.configure("ww")
        c = RBNClient("h", 1, "S55OO", sf, q, name="telnet", on_raw=raw.append)
        c.people = PeopleFilter(sf) if people else None
        return c, q, raw

    def drained(self, q):
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out

    def test_cluster_off_no_people_spots(self) -> None:
        c, q, raw = self.client(people=False)
        c._consume(PERSON + POTA + MODEWORD + SKIM, "")
        got = self.drained(q)
        self.assertEqual([s.dxcall for s in got], ["K1ABC"])     # only the skimmer
        self.assertEqual({s.dxcall for s in raw} >= {"YO2CK", "IU8RFW", "K1ABC"}, True)

    def test_cluster_on_people_spots_with_their_real_mode(self) -> None:
        c, q, raw = self.client(people=True)
        c._consume(PERSON + POTA, "")
        got = {s.dxcall: s.mode for s in self.drained(q)}
        self.assertEqual(got["YO2CK"], "SSB")          # band plan: 7074 = SSB -> PSK check
        self.assertEqual(got["IU8RFW"], "SSB")
        self.assertNotIn("0724Z", got.values())

    def test_a_time_is_no_mode(self) -> None:
        self.assertIsNone(parse_spot(PERSON.strip()))
        self.assertIsNone(parse_spot(POTA.strip()))
        self.assertEqual(parse_spot(SKIM.strip()).mode, "CW")


class UnmarkedFtTests(unittest.TestCase):
    """Unmarked people's spots on 7074 - 7077 / 1840 - 1843: FT8 outside an SSB
    contest (in contests those windows hold FT8 stations, S55OO)."""

    def test_unmarked_is_ft8_in_a_cw_contest(self) -> None:
        sp = parse_cluster_spot(PERSON.strip())
        self.assertEqual(sp.mode, "SSB")                      # the band plan
        self.assertEqual(unmarked_ft(sp, phone_contest=False), "FT8")
        self.assertEqual(unmarked_ft(sp, phone_contest=True), "")     # SSB contest: PSK decides
        on160 = parse_cluster_spot("DX de UT4VL:      1841.0  YO2CK        0724Z")
        self.assertEqual(unmarked_ft(on160, phone_contest=False), "FT8")

    def test_marked_or_skimmer_or_elsewhere_untouched(self) -> None:
        ssb = parse_cluster_spot("DX de UT4VL:      7074.0  YO2CK        SSB 59  0724Z")
        self.assertEqual(unmarked_ft(ssb, phone_contest=False), "")   # he said SSB
        cw = parse_cluster_spot("DX de UT4VL:      7024.0  YO2CK        0724Z")
        self.assertEqual(unmarked_ft(cw, phone_contest=False), "")    # not an FT8 window
        ph = parse_cluster_spot("DX de UT4VL:      7120.0  YO2CK        0724Z")
        self.assertEqual(unmarked_ft(ph, phone_contest=False), "")


if __name__ == "__main__":
    unittest.main()
