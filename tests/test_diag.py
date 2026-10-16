"""Log file and diagnostics zip (passwords never in it)."""

import logging
import os
import tempfile
import unittest
import zipfile

from pouncer import diag


class RedactTests(unittest.TestCase):
    def test_secrets_replaced_rest_kept(self) -> None:
        text = ("[rbn]\ncallsign = S50XX\npassword = hunter2\n[web]\napi_key: abc\n"
                "token=xyz\nfkeys = F2:RPRT F3:TU\n; password = old\nsecret =\n")
        out = diag.redact_ini(text)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("abc", out)
        self.assertNotIn("xyz", out)
        self.assertIn("password = ***", out)
        self.assertIn("callsign = S50XX", out)
        self.assertIn("fkeys = F2:RPRT F3:TU", out)
        self.assertIn("secret =", out)          # empty stays empty


class ZipTests(unittest.TestCase):
    def test_zip_has_log_facts_and_clean_config(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            os.makedirs(cache)
            cfg = os.path.join(d, "config.ini")
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write("[team]\npassword = topsecret\n")
            with open(os.path.join(cache, diag.LOG_NAME), "w", encoding="utf-8") as fh:
                fh.write("tap OK1BB: QSY 7012.0\n")
            with open(os.path.join(cache, "cwspeed.txt"), "w", encoding="utf-8") as fh:
                fh.write("speed\n")
            out = diag.build_zip(os.path.join(d, diag.zip_name()), cfg, cache,
                                 {"version": "0.7.67", "radios": {1: {"band": "20m"}}})
            with zipfile.ZipFile(out) as z:
                names = set(z.namelist())
                self.assertEqual(names, {"facts.txt", "config.ini", diag.LOG_NAME, "cwspeed.txt"})
                self.assertNotIn(b"topsecret", z.read("config.ini"))
                self.assertIn(b"version: 0.7.67", z.read("facts.txt"))
                self.assertIn(b"OK1BB", z.read(diag.LOG_NAME))

    def test_file_log(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            old = diag._handler
            diag._handler = None
            try:
                path = diag.setup_file_log(os.path.join(d, "cache"))
                self.assertEqual(diag.setup_file_log(os.path.join(d, "other")), path)  # once
                logging.getLogger("pouncer.test").info("hello log")
                diag._handler.flush()
                with open(path, encoding="utf-8") as fh:
                    self.assertIn("hello log", fh.read())
            finally:
                if diag._handler is not None:
                    logging.getLogger().removeHandler(diag._handler)
                    diag._handler.close()
                diag._handler = old


if __name__ == "__main__":
    unittest.main()
