"""Team settings log: every POUNCer's settings, collected on the master."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from pouncer import team as teammod
from pouncer import teamsettings as ts
from pouncer.config import load_config
from pouncer.team import Team


def wait_for(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ReportTests(unittest.TestCase):
    def test_every_setting_private_ones_masked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.ini")
            Path(path).write_text(
                "[autostart]\napp1 = C:\\Secret\\Path\\prog.exe\n"
                "[ssb]\nhost = cluster.example.org\nlogin = DL1ABC\n"
                "[ui]\nmin_snr = 12\n")
            r = ts.report(load_config(path))
        from pouncer import settings
        self.assertEqual(set(r), {it["key"] for it in settings.SETTINGS})
        self.assertEqual(r["autostart.app1"], "(set)")
        self.assertEqual(r["autostart.app2"], "")
        self.assertEqual(r["ssb.host"], "(set)")
        self.assertEqual(r["ssb.login"], "(set)")
        self.assertEqual(r["ui.min_snr"], "12")
        text = json.dumps(r)
        for secret in ("Secret", "prog.exe", "example.org", "DL1ABC"):
            self.assertNotIn(secret, text)
        self.assertTrue(ts.valid({"v": "0.7.194", "call": "S5", "why": "start", "s": r}))

    def test_bad_reports_refused(self):
        for bad in (None, [], {"s": "x"}, {"s": {"a": 1}}, {"s": {"a" * 70: "x"}},
                    {"s": {"a": "x" * 300}}, {"s": {}, "call": "x" * 100},
                    {"s": {str(i): "" for i in range(500)}}):
            self.assertFalse(ts.valid(bad), bad)


class LogTests(unittest.TestCase):
    def test_full_set_first_then_only_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            lg = ts.SettingsLog(folder)
            self.assertTrue(lg.add("S55OO", "", "0.7.194", {"a": "1", "b": "x"}, "start"))
            self.assertFalse(lg.add("S55OO", "", "0.7.194", {"a": "1", "b": "x"}, "timer"))
            self.assertTrue(lg.add("S55OO", "", "0.7.194", {"a": "2", "b": "x"}, "change"))
            self.assertTrue(lg.add("S55OO", "10.66.66.5", "0.7.194", {"a": "1"}, "start"))
            lines = [json.loads(x) for x in Path(lg.path).read_text().splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]["pc"], "S55OO@this PC")
        self.assertEqual(lines[0]["settings"], {"a": "1", "b": "x"})
        self.assertEqual(lines[1]["changed"], {"a": ["1", "2"]})
        self.assertEqual(lines[1]["why"], "change")
        self.assertEqual(lines[2]["pc"], "S55OO@10.66.66.5")
        self.assertIn("settings", lines[2])

    def test_file_stays_small(self):
        with tempfile.TemporaryDirectory() as folder:
            old = ts.MAX_BYTES
            ts.MAX_BYTES = 2000
            try:
                lg = ts.SettingsLog(folder)
                for i in range(200):
                    lg.add("S5", "", "v", {"a": str(i)}, "change")
                self.assertLess(os.path.getsize(lg.path), 4000)
            finally:
                ts.MAX_BYTES = old

    def test_in_the_diagnostics_zip(self):
        from pouncer import diag
        self.assertIn(ts.FILE, diag.EXTRA_FILES)


class TeamTests(unittest.TestCase):
    def test_slave_sends_its_settings_to_the_master_only(self):
        old = teammod.ANNOUNCE_S
        teammod.ANNOUNCE_S = 0.2
        got_a, got_b = [], []
        a = Team(0, port=18141, send_to=[("127.0.0.1", 18142)],
                 on_cfg=lambda pid, ip, d: got_a.append(d))
        a.started -= 100                      # a = master
        b = Team(0, port=18142, send_to=[("127.0.0.1", 18141)],
                 on_cfg=lambda pid, ip, d: got_b.append(d))
        try:
            a.start()
            b.start()
            self.assertTrue(wait_for(lambda: b.master_id() == a.id and a.alive_peers(), 4))
            rep = {"v": "0.7.194", "call": "S55OO", "why": "start", "s": {"ui.min_snr": "12"}}
            b.send_cfg(rep)
            self.assertTrue(wait_for(lambda: got_a == [rep], 3))
            a.send_cfg(rep)                   # the master never sends its own
            b._send({"t": "cfg", "d": {"s": {"x": 1}}}, broadcast=False)   # junk: dropped
            time.sleep(0.6)
            self.assertEqual(got_b, [])
            self.assertEqual(got_a, [rep])
        finally:
            a.stop()
            b.stop()
            teammod.ANNOUNCE_S = old


class AppTests(unittest.TestCase):
    def test_master_logs_its_own_slave_sends_after_a_save(self):
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.ini")
            Path(path).write_text("[ui]\nmin_snr = 9\n")
            cfg = load_config(path)
            cfg.run_history_dir = folder
            sent = []
            import types
            team = types.SimpleNamespace(is_master=lambda: True, send_cfg=sent.append)
            f = types.SimpleNamespace(cfg=cfg, team=team, own_call=lambda: "S55OO")
            for name in ("_settings_report", "_send_settings_report", "_note_team_settings",
                         "_send_settings_report_soon", "_maybe_send_settings_report"):
                setattr(f, name, getattr(P, name).__get__(f))
            f._send_settings_report("start")            # master: into its own file
            line = json.loads(Path(folder, ts.FILE).read_text().splitlines()[0])
            self.assertEqual(line["pc"], "S55OO@this PC")
            self.assertEqual(line["settings"]["ui.min_snr"], "9")
            team.is_master = lambda: False              # a slave: to the master
            f._send_settings_report_soon()
            f._maybe_send_settings_report()
            self.assertEqual(sent, [])                  # not yet: a few seconds later
            f._settings_report_at = time.time() - 1
            f._maybe_send_settings_report()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["why"], "change")
            self.assertEqual(sent[0]["call"], "S55OO")


if __name__ == "__main__":
    unittest.main()
