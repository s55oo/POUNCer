"""cache/nonPublishedSpots.log: wanted spots that never went out on port 10000."""

import os
import tempfile
import unittest

from pouncer.rbn import parse_spot
from pouncer.unpublished import FILE, WAIT_S, UnpublishedLog

A = "DX de G4ZFE-#:    7022.0  SQ6OEP       CW  14dB Q:9* Z:14,15          0948Z"
B = "DX de OE9GHV-#:   7022.0  SQ6OEP       CW  26dB Q:9* Z:14,15          0948Z"
C = "DX de S53A-#:    14026.0  ET5DX        CW  19dB Q:7*+ Z:14,15,16,20   0921Z"
D = "DX de W3LPL-#:   14030.0  K1ABC        CW  20dB Q:5 Z:5               0921Z"
BUST = "DX de G4ZFE-#:    7022.1  SQ6OFP       CW  14dB Q:2  Z:14,15          0948Z"


def spot(line, source="telnet"):
    s = parse_spot(line)
    s.source = source
    s.raw_line = line
    return s


class LogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.now = [1000.0]
        self.u = UnpublishedLog(self.dir, clock=lambda: self.now[0])

    def text(self) -> str:
        p = os.path.join(self.dir, FILE)
        return open(p, encoding="utf-8").read() if os.path.exists(p) else ""

    def test_only_wanted_unsent_spots_are_written(self) -> None:
        a, b, c, d = spot(A), spot(B, "telnet2"), spot(C), spot(D)
        for s in (a, b, c, d):
            self.u.seen(s)
        self.now[0] += 1
        self.u.published(a)                               # SQ6OEP went out (b merged)
        self.u.note(c, "collector: held; input")
        self.now[0] += WAIT_S + 1
        n = self.u.flush(lambda s: s.dxcall != "K1ABC")   # K1ABC: your filters keep it out
        self.assertEqual(n, 1)
        t = self.text()
        self.assertIn("ET5DX\t14026.0\tCW\tS53A-#\tcollector: held; input\tDX de S53A-#", t)
        self.assertNotIn("SQ6OEP", t)
        self.assertNotIn("K1ABC", t)
        self.assertTrue(t.startswith("# POUNCer"))

    def test_corrected_call_counts_as_sent(self) -> None:
        self.u.seen(spot(BUST))
        self.now[0] += 2
        self.u.published(spot(A))                         # same skimmer, corrected call
        self.now[0] += WAIT_S
        self.assertEqual(self.u.flush(lambda s: True), 0)

    def test_nothing_while_relay_is_off_and_no_reason(self) -> None:
        self.u.seen(spot(C))
        self.now[0] += WAIT_S + 1
        self.assertEqual(self.u.flush(lambda s: True, active=False), 0)
        self.u.seen(spot(C))
        self.now[0] += WAIT_S + 1
        self.assertEqual(self.u.flush(lambda s: True), 1)
        self.assertIn("no reason noted", self.text())

    def test_not_judged_too_early_and_history_ignored(self) -> None:
        old = spot(C)
        old.history = True
        self.u.seen(old)
        self.u.seen(spot(C))
        self.now[0] += WAIT_S - 5
        self.assertEqual(self.u.flush(lambda s: True), 0)   # still waiting
        self.now[0] += 10
        self.assertEqual(self.u.flush(lambda s: True), 1)   # history one not counted


if __name__ == "__main__":
    unittest.main()
