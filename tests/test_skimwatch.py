"""Own skimmer offline / its connection down: S53WW at home, TK0C away."""

import unittest

from pouncer import checks
from pouncer.skimwatch import SkimmerWatch


class WatchTests(unittest.TestCase):
    def test_quiet_skimmer_while_the_feed_works(self) -> None:
        now = [0.0]
        w = SkimmerWatch(("TK0C", "S53WW"), quiet_s=180, clock=lambda: now[0])
        now[0] = 100
        self.assertEqual(w.quiet(), [])                     # start: time to speak
        w.seen("TK0C-1-#")
        w.seen("S53WW-#")
        w.seen("DL1ABC-#")                                  # not ours: ignored
        now[0] = 200
        w.seen("TK0C-2-#")                                  # any receiver keeps TK0C alive
        now[0] = 290                                        # S53WW silent 190 s
        self.assertEqual([n for n, _ in w.quiet()], ["S53WW"])
        self.assertEqual(w.text(w.quiet()), "S53WW sends nothing (3 min)")
        self.assertEqual(w.quiet(feed_alive=False), [])     # RBN down: not the skimmer
        now[0] = 500
        self.assertEqual([n for n, _ in w.quiet()], ["TK0C", "S53WW"])

    def test_never_spotted_and_off(self) -> None:
        now = [0.0]
        w = SkimmerWatch(("S53WW",), quiet_s=180, clock=lambda: now[0])
        now[0] = 181
        self.assertEqual([n for n, _ in w.quiet()], ["S53WW"])
        self.assertEqual(SkimmerWatch(()).quiet(), [])      # spotter empty = all: off

    def test_check_line(self) -> None:
        from tests.test_checks import GOOD
        items = {i["name"]: i for i in checks.build(dict(
            GOOD, skimmers={"names": "TK0C", "quiet": "TK0C sends nothing (4 min)"}))}
        self.assertEqual(items["Skimmer"]["state"], "warn")
        self.assertIn("offline or its connection down", items["Skimmer"]["text"])
        self.assertEqual(items["RBN"]["state"], "ok")
        items = {i["name"]: i for i in checks.build(dict(GOOD, skimmers={"names": "S53WW", "quiet": ""}))}
        self.assertEqual((items["Skimmer"]["state"], items["Skimmer"]["text"]), ("ok", "S53WW"))
        self.assertNotIn("Skimmer", {i["name"] for i in checks.build(GOOD)})


if __name__ == "__main__":
    unittest.main()
