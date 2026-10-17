"""The clicked / tapped station keeps its row until logged or 60 s passed."""

import types
import unittest
from unittest import mock


class PinTests(unittest.TestCase):
    def test_keeps_row_then_lets_go(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        sp = lambda c, f: types.SimpleNamespace(dxcall=c, freq_khz=f, mode="CW")
        worked = set()
        f = types.SimpleNamespace(worked=types.SimpleNamespace(is_worked=lambda c, fr, m: c in worked))
        f._pin = (("I2GG", "20m"), 1, 1000.0)
        items = [sp("DL1AA", 14010), sp("OK1BB", 14012), sp("W1CC", 14014), sp("I2GG", 14016)]
        with mock.patch.object(app.time, "time", return_value=1030.0):
            got = app.PouncerApp._apply_pin(f, items)
            self.assertEqual([x.dxcall for x in got], ["DL1AA", "I2GG", "OK1BB", "W1CC"])
            worked.add("I2GG")                         # logged: back to its sorted place
            self.assertEqual(app.PouncerApp._apply_pin(f, items), items)
            self.assertIsNone(f._pin)
        f._pin = (("I2GG", "20m"), 0, 1000.0)
        worked.clear()
        with mock.patch.object(app.time, "time", return_value=1061.0):   # over 60 s
            self.assertEqual(app.PouncerApp._apply_pin(f, items), items)
            self.assertIsNone(f._pin)

    def test_phone_page_pins_too(self) -> None:
        from pouncer.web import INDEX_HTML
        self.assertIn("const PIN_MS = 60000", INDEX_HTML)
        self.assertIn("applyPin(spots)", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
