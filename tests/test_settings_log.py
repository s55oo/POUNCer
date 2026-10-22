"""Every saved setting is logged: label, key, old -> new."""

import os
import tempfile
import types
import unittest

from pouncer.config import load_config


class SettingsLogTests(unittest.TestCase):
    def test_change_is_logged(self) -> None:
        from tests.test_so2r import _import_app
        P = _import_app().PouncerApp
        d = tempfile.mkdtemp()
        ini = os.path.join(d, "config.ini")
        with open(ini, "w") as fh:
            fh.write("[rbn]\nspotter = S53WW\n[n1mm]\nqsy_wait_ms = 2500\n")
        cfg = load_config(ini)
        f = types.SimpleNamespace(cfg=cfg)
        with self.assertLogs("pouncer.app", level="INFO") as got:
            res = P._save_setting(f, "n1mm.qsy_wait_ms", "3000")
            P._save_setting(f, "n1mm.qsy_wait_ms", "abc")
        self.assertTrue(res["ok"])
        text = "\n".join(got.output)
        self.assertIn("setting changed:", text)
        self.assertIn("(n1mm.qsy_wait_ms): 2500 -> 3000", text)
        self.assertIn("setting NOT saved: n1mm.qsy_wait_ms", text)
        with open(ini) as fh:
            self.assertIn("3000", fh.read())


if __name__ == "__main__":
    unittest.main()
