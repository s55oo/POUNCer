"""Tests for the main window's connection lights."""

import unittest

from pouncer import lights as L


class LightsTests(unittest.TestCase):
    def test_ssb_pause_with_a_working_telnet_is_green(self) -> None:
        # dual mode: RBN paused on SSB, Telnet #1 - #4 still deliver (v0.7.244)
        self.assertEqual(L.rbn_light("paused", 5, "solo", working="telnet"),
                         (L.OK, "SPOTS via Telnet #1"))
        self.assertEqual(L.rbn_light("paused", 5, "off", working="telnet4"),
                         (L.OK, "SPOTS via Telnet #4"))
        # nothing else delivers: really on hold
        self.assertEqual(L.rbn_light("paused", None, "solo", working=""),
                         (L.HOLD, "SPOTS on hold (SSB)"))
        # a team slave with the master paused stays on hold
        self.assertEqual(L.rbn_light("paused", None, "slave", working="telnet")[0], L.HOLD)

    def test_rbn(self) -> None:
        self.assertEqual(L.rbn_light("logged in as S55OO", None, "solo")[0], L.OK)
        self.assertEqual(L.rbn_light("connecting to x:7000", None, "off")[0], L.WAIT)
        self.assertEqual(L.rbn_light("disconnected, reconnecting", None, "off")[0], L.BAD)
        self.assertEqual(L.rbn_light("disconnected, reconnecting", 30, "off")[0], L.OK)
        self.assertEqual(L.rbn_light("starting", None, "slave"), (L.OK, "SPOTS via master"))
        # RBN not logged in, but Telnet brings spots: green, named after it
        self.assertEqual(L.rbn_light("disconnected, reconnecting", None, "solo", working="telnet"),
                         (L.OK, "SPOTS via Telnet #1"))
        self.assertEqual(L.rbn_light("logged in as S55OO", None, "solo", working="ok"),
                         (L.OK, "SPOTS via RBN"))
        # RBN paused for SSB while a Telnet delivers: green since v0.7.244 (was on hold)
        self.assertEqual(L.rbn_light("paused", None, "solo", working="telnet")[0], L.OK)

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
        # HamFiles: cty.dat + MASTER.DTA
        self.assertEqual(L.hamfiles_light(True, True, True), (L.OK, "HamFiles"))
        self.assertEqual(L.hamfiles_light(True, False, False), (L.OK, "HamFiles"))   # SCP off
        self.assertEqual(L.hamfiles_light(True, True, False), (L.WAIT, "HamFiles: no MASTER.DTA"))
        self.assertEqual(L.hamfiles_light(False, True, True), (L.WAIT, "HamFiles: no cty.dat"))
        self.assertEqual(L.hamfiles_light(False, False, False), (L.WAIT, "HamFiles: no cty.dat"))
        self.assertEqual(L.hamfiles_light(False, True, False), (L.BAD, "HamFiles NOK"))
        self.assertEqual(L.warm_light(True, "")[0], L.WAIT)
        self.assertEqual(L.warm_light(True, "46044 stations known in advance (downloaded x)")[0], L.OK)
        self.assertEqual(L.warm_light(True, "warm-up download failed: offline")[0], L.BAD)
        self.assertEqual(L.warm_light(True, "CW 46044 stations known in advance (downloaded x);"
                                            " SSB list: warm-up download failed: 404")[0], L.OK)
        self.assertEqual(L.warm_light(False, "")[0], L.OFF)
        for state in (L.OK, L.WAIT, L.BAD, L.OFF):
            self.assertTrue(L.COLORS[state].startswith("#"))


class SharedAndStandbyTests(unittest.TestCase):
    def test_via_n1mm_and_standby(self) -> None:
        self.assertEqual(L.rbn_light("via N1MM's connection to azure.s53m.com:8000", None, "solo"),
                         (L.OK, "SPOTS via N1MM's login"))
        self.assertEqual(L.rbn_light("standby - the HTTP feed delivers", None, "solo", http=True),
                         (L.OK, "SPOTS via HTTP"))
        self.assertEqual(L.rbn_light("standby - the HTTP feed delivers", None, "solo"),
                         (L.HOLD, "SPOTS standby (HTTP)"))


if __name__ == "__main__":
    unittest.main()
