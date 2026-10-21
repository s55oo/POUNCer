"""RBN closed while every radio is on SSB; back at once on CW."""

import types
import unittest

from pouncer import checks, lights


def _app():
    from tests.test_so2r import _import_app
    return _import_app()


class PauseTests(unittest.TestCase):
    def _fake(self, modes, peers=(), contest=""):
        app = _app()
        st = {"modes": dict(modes)}
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(rbn_pause_on_phone=True),
            state=types.SimpleNamespace(radio_numbers=lambda: sorted(st["modes"]),
                                        snapshot=lambda nr: {"mode": st["modes"].get(nr)}),
            team=types.SimpleNamespace(
                alive_peers=lambda: list(peers),
                peers_need_rbn=lambda: any(p.get("cw", True) for p in peers),
            ) if peers is not None else None,
            worked=types.SimpleNamespace(contest_name=contest),
            RBN_PAUSE_S=app.PouncerApp.RBN_PAUSE_S, _phone_since=None)
        for name in ("_phone_radio", "_rbn_paused", "_rbn_needed_here", "_phone_contest"):
            setattr(f, name, getattr(app.PouncerApp, name).__get__(f))
        return app, f, st

    def test_ssb_for_a_minute_pauses_cw_resumes(self) -> None:
        app, f, st = self._fake({1: "USB"})
        self.assertFalse(f._rbn_paused())              # just switched: not yet
        f._phone_since -= 61
        self.assertTrue(f._rbn_paused())
        st["modes"][1] = "CW"
        self.assertFalse(f._rbn_paused())              # back on CW: at once

    def test_so2r_one_radio_on_cw_keeps_it(self) -> None:
        _, f, _ = self._fake({1: "LSB", 2: "CW"})
        f._phone_since = 0
        self.assertFalse(f._rbn_paused())

    def test_team_peers_keep_it(self) -> None:
        _, f, _ = self._fake({1: "USB"}, peers=[{"ip": "10.0.0.2"}])
        f._phone_since = 0
        self.assertFalse(f._rbn_paused())

    def test_team_all_on_ssb_pauses_one_on_cw_keeps_it(self) -> None:
        peers = [{"ip": "10.0.0.2", "cw": False}, {"ip": "10.0.0.3", "cw": False}]
        _, f, _ = self._fake({1: "USB"}, peers=peers)
        f._phone_since = 0
        self.assertTrue(f._rbn_paused())              # nobody needs RBN
        peers[1]["cw"] = True                         # one goes to CW
        self.assertFalse(f._rbn_paused())
        _, f, _ = self._fake({1: "USB"}, peers=[{"ip": "10.0.0.9"}])   # older: says nothing
        f._phone_since = 0
        self.assertFalse(f._rbn_paused())

    def test_ssb_contest_needs_no_rbn_from_the_start(self) -> None:
        _, f, _ = self._fake({}, contest="CQWWSSB")           # no radio info yet
        self.assertFalse(f._rbn_needed_here())
        self.assertTrue(f._rbn_paused())
        _, f, _ = self._fake({1: "USB"}, contest="CQWWSSB")   # no minute to wait
        self.assertTrue(f._rbn_paused())
        _, f, st = self._fake({1: "CW"}, contest="CQWWSSB")   # a radio on CW: needed
        self.assertTrue(f._rbn_needed_here())
        _, f, _ = self._fake({}, contest="CQWWCW")
        self.assertTrue(f._rbn_needed_here())

    def test_phone_contest_names(self) -> None:
        from pouncer.rbn import is_phone_contest
        for name in ("CQWWSSB", "CQWPXSSB", "ARRLDXSSB", "NAQPSSB", "OKOMSSB", "UKEI-PH"):
            self.assertTrue(is_phone_contest(name), name)
        for name in ("CQWWCW", "CQWWRTTY", "IARU-HF", "WAG", "SSB-CW-MIX", "EUHFC", ""):
            self.assertFalse(is_phone_contest(name), name)

    def test_team_hello_says_who_needs_rbn(self) -> None:
        from pouncer.team import Team
        t = Team.__new__(Team)
        import threading
        t._lock = threading.Lock()
        t.peers = {}
        t.id = "me"
        t.started = 0.0
        t.handle({"t": "hi", "id": "a", "started": 1, "cw": False, "paused": True}, "10.0.0.2")
        t.handle({"t": "hi", "id": "b", "started": 2}, "10.0.0.3")      # older version
        self.assertTrue(t.peers_need_rbn())
        del t.peers["b"]
        self.assertFalse(t.peers_need_rbn())
        t.started = 5.0                                       # "a" started first: master
        self.assertTrue(t.master_paused())

    def test_no_radio_info_or_option_off(self) -> None:
        _, f, _ = self._fake({})
        self.assertFalse(f._rbn_paused())
        _, f, _ = self._fake({1: "USB"})
        f.cfg.rbn_pause_on_phone = False
        f._phone_since = 0
        self.assertFalse(f._rbn_paused())

    def test_light_and_check(self) -> None:
        self.assertEqual(lights.rbn_light("paused", None, "solo"), (lights.OFF, "RBN paused"))
        self.assertEqual(lights.rbn_light("paused", None, "slave"), (lights.OFF, "RBN paused"))
        from tests.test_checks import GOOD
        rbn = [i for i in checks.build(dict(GOOD, rbn="paused")) if i["name"] == "RBN"][0]
        self.assertEqual((rbn["state"], rbn["text"]), ("ok", "paused - SSB, not needed"))

if __name__ == "__main__":
    unittest.main()
