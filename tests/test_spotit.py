"""SPOTit key: N1MM's Spot It (Alt+P), half size under ENTER."""

import unittest
from unittest import mock

from pouncer.config import parse_fkeys
from pouncer.web import INDEX_HTML, is_send_key, normalize_key


class SpotItTests(unittest.TestCase):
    def test_names(self) -> None:
        for k in ("SpotIt", "spotit", "spot", "Alt+P"):
            self.assertEqual(normalize_key(k), "SpotIt")
        self.assertTrue(is_send_key("SpotIt"))
        self.assertEqual(parse_fkeys("F2:RPRT Enter SpotIt:SPOTit")[-1], ("SpotIt", "SPOTit"))

    def test_sends_alt_p(self) -> None:
        from tests.test_so2r import _import_app
        app = _import_app()
        with mock.patch.object(app, "send_alt_p", return_value=True) as alt_p:
            self.assertTrue(app.PouncerApp._send_key("SpotIt"))
            alt_p.assert_called_once()

    def test_phone_page(self) -> None:
        self.assertIn("button.spot", INDEX_HTML)
        self.assertIn("Spot It (Alt+P)", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
