"""Traffic console hover: each source says what it is and its settings."""

import types
import unittest

from tests.test_so2r import _import_app

_import_app()                       # stub tkinter where there is none

from pouncer.config import load_config  # noqa: E402
from pouncer.console import GROUPS, TrafficConsole  # noqa: E402


class TipTests(unittest.TestCase):
    def _app(self):
        from tests.test_so2r import _import_app
        app = _import_app()
        cfg = load_config("")
        cfg.team_key = "00ff" * 8
        f = types.SimpleNamespace(cfg=cfg, team=None, _dxc=None, own_call=lambda: "S55OO",
                                  _self_feed_login=lambda: "S55OO-77")
        f._cluster_merged = lambda: app.PouncerApp._cluster_merged(f)
        return app, f

    def test_every_group_has_a_text(self) -> None:
        app, f = self._app()
        for g in GROUPS:
            text = app.PouncerApp._console_info(f, g)
            self.assertTrue(text.startswith(g), (g, text[:40]))
            self.assertIn("\n", text)

    def test_settings_and_no_secret(self) -> None:
        app, f = self._app()
        rbn = app.PouncerApp._console_info(f, "RBN")
        self.assertIn("telnet.reversebeacon.net:7000", rbn)
        self.assertIn("S55OO", rbn)
        team = app.PouncerApp._console_info(f, "TEAM")
        self.assertIn("Password: set", team)
        self.assertNotIn("00ff", team)
        self.assertIn("Relay: on", app.PouncerApp._console_info(f, "SERVER"))
        self.assertIn("spots of people", app.PouncerApp._console_info(f, "SSB"))

    def test_tip_text_adds_why_and_counts(self) -> None:
        con = TrafficConsole(None, notes=lambda: {"HTTP": "backup off - RBN telnet works"},
                             info=lambda g: f"{g} - test\nline")
        con.counts["HTTP"] = 3
        t = con.tip_text("HTTP")
        self.assertIn("Now: backup off", t)
        self.assertIn("3 lines", t)
        bad = TrafficConsole(None, notes=lambda: 1 / 0, info=lambda g: 1 / 0)
        self.assertIn("lines", bad.tip_text("RBN"))      # never raises


if __name__ == "__main__":
    unittest.main()
