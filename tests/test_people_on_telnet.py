"""People's spots on the Telnet connection: taken only with the Cluster source on
(v0.7.211 - before, 'DX de UT4VL: 7074.0 YO2CK 0724Z' was read as a skimmer spot
with mode '0724Z' and went to port 10000)."""

import queue
import unittest

from pouncer.rbn import PeopleFilter, RBNClient, SpotterFilter, parse_spot

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


if __name__ == "__main__":
    unittest.main()
