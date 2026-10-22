"""Is the node's filter on the self-spot feed applied? Logged once."""

import time
import types
import unittest

from tests.test_so2r import _import_app


class FilterCheckTests(unittest.TestCase):
    def _fake(self, app):
        f = types.SimpleNamespace(_self_feed=types.SimpleNamespace(_for_call="S55OO"),
                                  _self_feed_check={"t": time.time() - 120, "mine": 0,
                                                    "other": 0, "said": False})
        f._note_self_spot = lambda *a: None
        f.SELF_FEED_BAD_N, f.SELF_FEED_OK_S = 20, 600
        return f

    def _spot(self, call):
        return types.SimpleNamespace(dxcall=call, spotter="DL1ABC", freq_khz=14025.0)

    def test_not_applied(self) -> None:
        app = _import_app()
        f = self._fake(app)
        with self.assertLogs("pouncer.app", "WARNING") as cm:
            for i in range(25):
                app.PouncerApp._on_self_feed_spot(f, self._spot(f"K{i}AA"))
        self.assertEqual(len(cm.output), 1)
        self.assertIn("does NOT apply", cm.output[0])

    def test_works(self) -> None:
        app = _import_app()
        f = self._fake(app)
        app.PouncerApp._on_self_feed_spot(f, self._spot("S55OO/P"))
        f._self_feed_check["t"] -= 600
        with self.assertLogs("pouncer.app", "INFO") as cm:
            app.PouncerApp._check_self_feed_filter(f)
        self.assertIn("filter works", cm.output[0])
        self.assertEqual(f._self_feed_check["mine"], 1)


if __name__ == "__main__":
    unittest.main()
