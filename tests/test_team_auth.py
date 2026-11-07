"""Team password (signed messages), the Team check item, the firewall rules."""

import threading
import time
import types
import unittest

from pouncer import checks, team as teammod
from pouncer.winfw import rules_command


class SignTests(unittest.TestCase):
    key = teammod.derive_key("tk0c-2026")

    def test_round_trip_and_rejects(self) -> None:
        msg = b'{"t":"hi","id":"a"}'
        now = 1_800_000_000
        sealed = teammod.seal(msg, now=now, key=self.key)
        self.assertEqual(teammod.unseal(sealed, now=now + 10, key=self.key), (msg, "ok"))
        self.assertEqual(teammod.unseal(sealed, now=now + 400, key=self.key)[1], "old")
        self.assertEqual(teammod.unseal(sealed, now=now, key=teammod.derive_key("x"))[1], "bad")
        changed = sealed.replace(b'"a"', b'"b"')
        self.assertEqual(teammod.unseal(changed, now=now, key=self.key)[1], "bad")
        self.assertEqual(teammod.unseal(sealed, now=now, key=b"")[1], "foreign")
        self.assertEqual(teammod.unseal(msg, now=now, key=self.key), (msg, "none"))

    def test_no_password_changes_nothing(self) -> None:
        self.assertEqual(teammod.seal(b"x", key=b""), b"x")
        self.assertTrue(teammod.accepted("none", b""))
        self.assertFalse(teammod.accepted("foreign", b""))
        self.assertTrue(teammod.accepted("ok", self.key))
        self.assertFalse(teammod.accepted("none", self.key))

    def test_key_only_from_password(self) -> None:
        self.assertEqual(len(self.key), 32)
        self.assertEqual(teammod.derive_key(""), b"")
        self.assertNotIn(b"tk0c", self.key)


class AuthProblemTests(unittest.TestCase):
    def _team(self, key):
        t = teammod.Team.__new__(teammod.Team)
        t._lock = threading.Lock()
        t.key, t.auth_seen = key, {}
        return t

    def test_texts(self) -> None:
        t = self._team(b"k" * 32)
        self.assertEqual(t.auth_problem(), "")
        t.auth_seen["bad"] = (time.time(), "10.0.0.3")
        self.assertIn("10.0.0.3: wrong team password", t.auth_problem())
        t2 = self._team(b"")
        t2.auth_seen["foreign"] = (time.time(), "10.0.0.2")
        self.assertIn("use a team password", t2.auth_problem())
        t2.auth_seen["foreign"] = (time.time() - 300, "10.0.0.2")
        self.assertEqual(t2.auth_problem(), "")


class TeamNoteTests(unittest.TestCase):
    def _fake(self, peers=(), seen=False, fw=None, age=60):
        from tests.test_so2r import _import_app
        app = _import_app()
        f = types.SimpleNamespace(team=types.SimpleNamespace(auth_problem=lambda: ""),
                                  cfg=types.SimpleNamespace(team_peers=peers),
                                  _app_started=time.time() - age, _fw_ok=fw,
                                  TEAM_SILENT_S=30)
        if seen:
            f._team_seen = True
        return app, f

    def test_silent_team(self) -> None:
        app, f = self._fake()
        self.assertIsNone(app.PouncerApp._team_note(f, "solo"))      # alone is normal
        app, f = self._fake(peers=("10.0.0.3",), fw=False)
        self.assertIn("firewall?", app.PouncerApp._team_note(f, "solo"))
        app, f = self._fake(seen=True, fw=True)
        self.assertIn("check that PC", app.PouncerApp._team_note(f, "solo"))
        app, f = self._fake(peers=("10.0.0.3",), age=5)
        self.assertIsNone(app.PouncerApp._team_note(f, "solo"))      # just started
        app, f = self._fake(peers=("10.0.0.3",))
        self.assertIsNone(app.PouncerApp._team_note(f, "master"))

    def test_check_item(self) -> None:
        items = checks.build({"team_note": "no other POUNCer heard - firewall?"})
        team = [i for i in items if i["name"] == "Team"]
        self.assertEqual(team[0]["state"], "warn")
        self.assertFalse([i for i in checks.build({}) if i["name"] == "Team"])


class FirewallTests(unittest.TestCase):
    def test_command(self) -> None:
        cmd = rules_command([("POUNCer phone page TCP", "TCP", 8080), ("POUNCer team UDP", "udp", 12079)])
        self.assertIn('delete rule name="POUNCer phone page TCP"', cmd)
        self.assertIn("protocol=TCP localport=8080 profile=private,domain", cmd)
        self.assertIn("protocol=UDP localport=12079 profile=private,domain", cmd)
        self.assertEqual(cmd.count(" & "), 3)
        self.assertNotIn("public", cmd)


if __name__ == "__main__":
    unittest.main()
