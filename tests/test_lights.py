"""Tests for the main window's connection lights."""

import unittest

from pouncer import lights as L


class LightsTests(unittest.TestCase):
    def test_rbn(self) -> None:
        self.assertEqual(L.rbn_light("logged in as S55OO", None, "solo")[0], L.OK)
        self.assertEqual(L.rbn_light("connecting to x:7000", None, "off")[0], L.WAIT)
        self.assertEqual(L.rbn_light("disconnected, reconnecting", None, "off")[0], L.BAD)
        self.assertEqual(L.rbn_light("disconnected, reconnecting", 30, "off")[0], L.OK)
        self.assertEqual(L.rbn_light("starting", None, "slave"), (L.OK, "RBN via master"))

    def test_n1mm_log(self) -> None:
        self.assertEqual(L.n1mm_light(None)[0], L.BAD)
        self.assertEqual(L.n1mm_light(3)[0], L.OK)
        self.assertEqual(L.n1mm_light(600)[0], L.WAIT)
        self.assertEqual(L.log_light(True, "ok")[0], L.OK)
        self.assertEqual(L.log_light(False, "unknown")[0], L.WAIT)
        self.assertEqual(L.log_light(False, "no_log")[0], L.BAD)

    def test_team_web_warm(self) -> None:
        self.assertEqual(L.team_light("master", 3), (L.OK, "Team master (3)"))
        self.assertEqual(L.team_light("slave", 2)[0], L.OK)
        self.assertEqual(L.team_light("solo", 1)[0], L.WAIT)
        self.assertEqual(L.team_light("off", 1)[0], L.OFF)
        # team POUNCers on different versions: amber, says who
        state, text = L.team_light("master", 2, [("192.168.1.20", "0.7.9")])
        self.assertEqual(state, L.WAIT)
        self.assertIn("192.168.1.20 on 0.7.9", text)
        self.assertEqual(L.team_light("master", 2, [])[0], L.OK)
        self.assertEqual(L.web_light(True, True)[0], L.OK)
        self.assertEqual(L.web_light(True, False)[0], L.BAD)
        self.assertEqual(L.web_light(False, False)[0], L.OFF)
        self.assertEqual(L.warm_light(True, "")[0], L.WAIT)
        self.assertEqual(L.warm_light(True, "46044 stations known in advance (downloaded x)")[0], L.OK)
        self.assertEqual(L.warm_light(True, "warm-up download failed: offline")[0], L.BAD)
        self.assertEqual(L.warm_light(True, "CW 46044 stations known in advance (downloaded x);"
                                            " SSB list: warm-up download failed: 404")[0], L.OK)
        self.assertEqual(L.warm_light(False, "")[0], L.OFF)
        for state in (L.OK, L.WAIT, L.BAD, L.OFF):
            self.assertTrue(L.COLORS[state].startswith("#"))


if __name__ == "__main__":
    unittest.main()
