"""Top bar: this PC's address next to the frequency."""

import unittest

from tests.test_so2r import _import_app


class AddrTextTests(unittest.TestCase):
    def test_texts(self) -> None:
        app = _import_app()
        t = app.PouncerApp.addr_text
        self.assertEqual(t("10.66.66.5", "ZeroTier", 8080), "ZeroTier 10.66.66.5:8080")
        self.assertEqual(t("192.168.1.20", "LAN", 8080), "LAN 192.168.1.20:8080")
        self.assertEqual(t("192.168.1.20", "LAN", None), "LAN 192.168.1.20 (phone page off)")
        self.assertEqual(t("10.0.0.9", "bound", 8090), "10.0.0.9:8090")


if __name__ == "__main__":
    unittest.main()
