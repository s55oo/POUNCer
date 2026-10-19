"""RBN closed while every radio is on SSB; back at once on CW."""

import types
import unittest

from pouncer import checks, lights


def _app():
    from tests.test_so2r import _import_app
    return _import_app()


class PauseTests(unittest.TestCase):
    def _fake(self, modes, peers=()):
        app = _app()
        st = {"modes": dict(modes)}
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(rbn_pause_on_phone=True),
            state=types.SimpleNamespace(radio_numbers=lambda: sorted(st["modes"]),
                                        snapshot=lambda nr: {"mode": st["modes"].get(nr)}),
            team=types.SimpleNamespace(alive_peers=lambda: list(peers)) if peers is not None else None,
            RBN_PAUSE_S=app.PouncerApp.RBN_PAUSE_S, _phone_since=None)
        f._phone_radio = app.PouncerApp._phone_radio.__get__(f)
        f._rbn_paused = app.PouncerApp._rbn_paused.__get__(f)
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

    def test_no_radio_info_or_option_off(self) -> None:
        _, f, _ = self._fake({})
        self.assertFalse(f._rbn_paused())
        _, f, _ = self._fake({1: "USB"})
        f.cfg.rbn_pause_on_phone = False
        f._phone_since = 0
        self.assertFalse(f._rbn_paused())

    def test_light_and_check(self) -> None:
        self.assertEqual(lights.rbn_light("paused", None, "solo"), (lights.OFF, "RBN paused"))
        from tests.test_checks import GOOD
        rbn = [i for i in checks.build(dict(GOOD, rbn="paused")) if i["name"] == "RBN"][0]
        self.assertEqual((rbn["state"], rbn["text"]), ("ok", "paused - radio on SSB"))

if __name__ == "__main__":
    unittest.main()
