"""Tests for configuration loading."""

import os
import tempfile
import unittest

from pouncer.config import load_config, parse_fkeys, parse_modes


class ParseModesTests(unittest.TestCase):
    def test_default_when_empty_or_invalid(self):
        self.assertEqual(parse_modes(""), ("CW",))
        self.assertEqual(parse_modes("nonsense"), ("CW",))

    def test_valid_and_deduplicated(self):
        self.assertEqual(parse_modes("rtty, cw"), ("RTTY", "CW"))
        self.assertEqual(parse_modes("CW CW RTTY"), ("CW", "RTTY"))


class ParseFkeysTests(unittest.TestCase):
    def test_default_when_blank(self):
        self.assertEqual(parse_fkeys(""), ())

    def test_key_with_label_and_sorted(self):
        self.assertEqual(
            parse_fkeys("F1:CQ F4:MyCall"), (("F1", "CQ"), ("F4", "MyCall"))
        )

    def test_label_defaults_to_key_and_bad_keys_dropped(self):
        self.assertEqual(parse_fkeys("F2 F99 foo F4:x"), (("F2", "F2"), ("F4", "x")))

    def test_enter_aliases_and_dedupe(self) -> None:
        self.assertEqual(parse_fkeys("Enter RETURN cr"), (("Enter", "Enter"),))
        self.assertEqual(parse_fkeys("Enter:Log"), (("Enter", "Log"),))
        self.assertEqual(parse_fkeys("Return"), (("Enter", "Enter"),))


class LoadConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = load_config("")
        self.assertEqual(cfg.follow_modes, ("CW",))
        self.assertEqual(cfg.rbn_port, 7000)
        self.assertEqual(cfg.rbn_ft8_port, 7001)
        self.assertFalse(cfg.touch_view)
        self.assertEqual((cfg.touch_columns, cfg.touch_rows, cfg.touch_font), (2, 8, 26))
        self.assertEqual(cfg.fkeys, (
            ("F2", "RPRT"), ("F3", "TU"), ("F4", "CALL"), ("F5", "HisCALL"), ("F7", "?"),
            ("Enter", "Enter"),
        ))
        self.assertTrue(cfg.hide_beacons)

    def test_fkeys_from_legacy_web_section(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[web]\nfkeys = F1:CQ, F4:MyCall\n")
            cfg = load_config(path)
            self.assertEqual(cfg.fkeys, (("F1", "CQ"), ("F4", "MyCall")))

    def test_ui_fkeys_override_web(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[ui]\nfkeys =\n[web]\nfkeys = F4:MyCall\n")
            cfg = load_config(path)
            self.assertEqual(cfg.fkeys, ())

    def test_follow_modes_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[rbn]\nfollow_modes = CW, RTTY\n[ui]\ntouch_view = true\ntouch_columns = 5\ntouch_rows = 4\ntouch_font = 34\n")
            cfg = load_config(path)
            self.assertEqual(cfg.follow_modes, ("CW", "RTTY"))
            self.assertTrue(cfg.touch_view)
            self.assertEqual((cfg.touch_columns, cfg.touch_rows, cfg.touch_font), (5, 4, 34))


if __name__ == "__main__":
    unittest.main()
