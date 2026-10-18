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
        self.assertEqual(cfg.fkeys, (
            ("F1", "CQ"), ("F2", "RPRT"), ("F3", "TU"), ("F4", "CALL"), ("F5", "HisCALL"),
            ("F7", "?"), ("F11", ".."), ("Enter", "Enter"), ("SpotIt", "SPOTit"),
        ))
        self.assertEqual(cfg.fkeys_half, ("F1", "F3", "F5", "F7", "F11", "SPOTIT"))
        self.assertTrue(cfg.hide_beacons)

    def test_fkeys_from_legacy_web_section(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[web]\nfkeys = F1:CQ, F4:MyCall\n")
            cfg = load_config(path)
            self.assertEqual(cfg.fkeys, (("F1", "CQ"), ("F4", "MyCall")))

    def test_radio_b_qsy(self):
        self.assertEqual(load_config("").radio_b_qsy, "udp-left")
        self.assertEqual(load_config("").freq_decimal, "auto")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.ini")
            for text, want in (("udp", "udp"), ("UDP-Left", "udp-left"), ("TYPE", "type"), ("rubbish", "udp-left")):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(f"[n1mm]\nradio_b_qsy = {text}\n")
                self.assertEqual(load_config(path).radio_b_qsy, want)

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
            self.assertFalse(hasattr(cfg, "touch_rows"))  # old keys ignored


class FilterStartTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.ini")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            return load_config(path)

    def test_default_narrow(self):
        self.assertEqual(self._load("[ui]\n").filter_start, "narrow")

    def test_filter_cat_strings(self):
        cfg = self._load("[ui]\nfilter_narrow_cat = ZZFI05;\nfilter_wide_cat = ZZFI02;\n")
        self.assertEqual((cfg.filter_narrow_cat, cfg.filter_wide_cat), ("ZZFI05;", "ZZFI02;"))
        self.assertEqual(self._load("[ui]\n").filter_narrow_cat, "")

    def test_backup_and_fade(self):
        cfg = self._load("[rbn]\nbackup_spotters = OE9GHV, S50ARX\nbackup_after_minutes = 3\n"
                         "[ui]\nfade_minutes = 4\n")
        self.assertEqual(cfg.backup_spotters, ("OE9GHV", "S50ARX"))
        self.assertEqual((cfg.backup_after_minutes, cfg.fade_minutes), (3, 4))
        d = self._load("[ui]\n")
        self.assertEqual((d.backup_spotters, d.backup_after_minutes, d.fade_minutes), ((), 5, 5))

    def test_wide_and_unknown(self):
        self.assertEqual(self._load("[ui]\nfilter_start = WIDE\n").filter_start, "wide")
        self.assertEqual(self._load("[ui]\nfilter_start =\n").filter_start, "")
        self.assertEqual(self._load("[ui]\nfilter_start = x\n").filter_start, "")


class ForwardKeysTests(unittest.TestCase):
    def test_forward_fkeys(self) -> None:
        self.assertTrue(load_config("").forward_fkeys)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.ini")
            with open(path, "w") as fh:
                fh.write("[ui]\nforward_fkeys = false\n")
            self.assertFalse(load_config(path).forward_fkeys)


class FkeysUpgradeTests(unittest.TestCase):
    def test_old_default_list_gets_the_new_keys(self) -> None:
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[ui]\nfkeys = F2:RPRT F3:TU F4:CALL F5:HisCALL F7:? Enter\n")
            cfg = load_config(p)
            self.assertEqual(cfg.fkeys[0], ("F1", "CQ"))
            self.assertIn(("SpotIt", "SPOTit"), cfg.fkeys)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[ui]\nfkeys = F4:CALL Enter\nfkeys_half =\n")
            cfg = load_config(p)                       # changed by you: kept
            self.assertEqual(cfg.fkeys, (("F4", "CALL"), ("Enter", "Enter")))
            self.assertEqual(cfg.fkeys_half, ())
            self.assertEqual(cfg.fkeys_text, "F4:CALL Enter")

    def test_v084_default_lines_get_f11(self) -> None:
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[ui]\nfkeys = F1:QRL? F3:TU F2:RPRT F4:CALL F5:HisCALL F7:? Enter "
                         "SpotIt:SPOTit\nfkeys_half = F1 F3 F5 F7 SpotIt\n")
            cfg = load_config(p)
            keys = [k for k, _ in cfg.fkeys]
            self.assertEqual(keys.index("F11"), keys.index("F7") + 1)      # right after F7
            self.assertIn("F11", cfg.fkeys_half)                          # same size as F7
            self.assertEqual(cfg.fkeys[:3], (("F1", "CQ"), ("F2", "RPRT"), ("F3", "TU")))
            with open(p, "w", encoding="utf-8") as fh:                     # v0.7.85's default
                fh.write("[ui]\nfkeys = F1:QRL? F3:TU F2:RPRT F4:CALL F5:HisCALL F7:? F11 Enter "
                         "SpotIt:SPOTit\n")
            self.assertIn(("F11", ".."), load_config(p).fkeys)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[ui]\nfkeys_half = F1 F3\n")
            self.assertEqual(load_config(p).fkeys_half, ("F1", "F3"))     # yours: kept


if __name__ == "__main__":
    unittest.main()
