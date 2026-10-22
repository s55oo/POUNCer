"""Settings page: config.ini edited in place, live values, local-only edits."""

import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

from pouncer import settings
from pouncer.config import load_config

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class IniTests(unittest.TestCase):
    def setUp(self) -> None:
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "config.ini")
        shutil.copy(os.path.join(HERE, "config.ini"), self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.d, ignore_errors=True)

    def test_save_keeps_comments_and_reloads(self) -> None:
        before = open(self.path, encoding="utf-8").read()
        cfg = load_config(self.path)
        self.assertEqual(cfg.config_path, os.path.abspath(self.path))
        r = settings.save(cfg, self.path, "n1mm.qsy_settle_ms", "500")
        self.assertEqual(r, {"ok": True, "key": "n1mm.qsy_settle_ms", "value": 500, "live": True})
        self.assertEqual(cfg.qsy_settle_ms, 500)                  # in use at once
        settings.save(cfg, self.path, "n1mm.type_mode", "KEYS")
        settings.save(cfg, self.path, "rbn.backup_commands", "set/skimmer, sh/dx")
        settings.save(cfg, self.path, "n1mm.default_radio", "b")
        settings.save(cfg, self.path, "n1mm.return_focus", "false")
        after = open(self.path, encoding="utf-8").read()
        # only those lines changed; every comment is still there
        changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))
        self.assertEqual(len(changed), 5)
        again = load_config(self.path)
        self.assertEqual((again.qsy_settle_ms, again.type_mode, again.default_radio, again.return_focus),
                         (500, "keys", 2, False))
        self.assertEqual(again.rbn_backup_commands, ("set/skimmer", "sh/dx"))

    def test_bad_values_are_refused_and_not_written(self) -> None:
        cfg = load_config(self.path)
        before = open(self.path, encoding="utf-8").read()
        for key, val in (("n1mm.qsy_wait_ms", "abc"), ("n1mm.qsy_wait_ms", "99999"),
                         ("n1mm.type_mode", "fast"), ("rbn.spotter", "S5[0]"),
                         ("n1mm.focus_n1mm", "maybe"), ("web.port", "1")):
            self.assertFalse(settings.save(cfg, self.path, key, val)["ok"], (key, val))
        self.assertEqual(open(self.path, encoding="utf-8").read(), before)

    def test_missing_key_and_section_are_added(self) -> None:
        p = os.path.join(self.d, "small.ini")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("; my notes\n[n1mm]\nradionr = 1\n\n[rbn]\nport = 7000\n")
        settings.set_ini_value(p, "n1mm", "qsy_wait_ms", "2000")
        settings.set_ini_value(p, "ui", "auto_update", "false")
        text = open(p, encoding="utf-8").read()
        self.assertIn("[n1mm]\nradionr = 1\nqsy_wait_ms = 2000\n\n[rbn]", text)
        self.assertTrue(text.startswith("; my notes\n"))
        self.assertTrue(text.endswith("[ui]\nauto_update = false\n"))
        self.assertEqual(load_config(p).qsy_wait_ms, 2000)


class PageTests(unittest.TestCase):
    def _web(self, remote):
        from pouncer.web import WebServer
        n = lambda *a, **k: None
        web = WebServer("127.0.0.1", 0, lambda: {"spots": []}, n, n, n, n, n, n)
        saved = []
        web.set_settings_provider(lambda: {"items": [{"key": "n1mm.qsy_wait_ms"}], "path": "x"},
                                  lambda k, v: saved.append((k, v)) or {"ok": True},
                                  remote=remote)
        web.start()
        return web, saved

    def test_local_browser_can_edit(self) -> None:
        web, saved = self._web(remote=False)
        base = f"http://127.0.0.1:{web.port}"
        try:
            with urllib.request.urlopen(base + "/api/settings", timeout=5) as r:
                d = json.load(r)
            self.assertTrue(d["can_edit"])
            self.assertFalse(d["can_restart"])
            with urllib.request.urlopen(base + "/api/settings/set?key=n1mm.qsy_wait_ms&value=900",
                                        timeout=5) as r:
                self.assertTrue(json.load(r)["ok"])
            with urllib.request.urlopen(base + "/settings", timeout=5) as r:
                self.assertIn("Settings", r.read().decode())
        finally:
            web.stop()
        self.assertEqual(saved, [("n1mm.qsy_wait_ms", "900")])

    def test_autostart_never_from_the_web(self) -> None:
        web, saved = self._web(remote=True)          # even with remote_settings on
        base = f"http://127.0.0.1:{web.port}"
        try:
            for key, value in (("autostart.folder", r"C:\\Users\\x\\Downloads"),
                               ("autostart.app1", r"C:\\evil.exe"), ("Autostart.enabled", "true")):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(base + "/api/settings/set?" + urllib.parse.urlencode(
                        {"key": key, "value": value}), timeout=5)
                self.assertEqual(cm.exception.code, 403)
        finally:
            web.stop()
        self.assertEqual(saved, [])
        from pouncer import settings
        self.assertTrue(settings.app_only("autostart.app3_delay"))
        self.assertFalse(settings.app_only("ui.look"))
        items = {i["key"]: i for i in settings.current(load_config(""))}
        self.assertTrue(items["autostart.folder"]["app_only"])
        self.assertFalse(items["n1mm.qsy_wait_ms"]["app_only"])

    def test_other_devices_read_only_unless_allowed(self) -> None:
        from pouncer.web import _Handler
        h = _Handler.__new__(_Handler)
        h.client_address = ("192.168.1.20", 5000)
        self.assertFalse(h._local_client())
        h.client_address = ("127.0.0.1", 5000)
        self.assertTrue(h._local_client())


if __name__ == "__main__":
    unittest.main()
