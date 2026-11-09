"""A click in the clean row list must reach the station drawn there, also
after a row was selected and the list then changed (v0.7.173).

Before, a refresh with a selected row raised "Item 0 not found" (it tried
to select in the empty table) before the row list was redrawn: the window
still showed the old list while clicks used the new one - a click on
F8EXX took the station one line below."""

import os
import tempfile
import time
import types
import unittest

try:
    from pouncer.app import PouncerApp
    from pouncer.config import load_config
    from pouncer.rbn import Spot
    from pouncer import theme
    from pouncer.rowlist import ROW_H
    from tests.test_so2r import radio_info
except Exception:  # noqa: BLE001 - no Tk here
    PouncerApp = None


def _spot(call, khz, now):
    return Spot(dxcall=call, freq_khz=khz, mode="CW", snr=20, wpm=25, spot_type="CQ",
                spotter="S53WW", time_utc="1200Z", received=now)


@unittest.skipIf(PouncerApp is None or not os.environ.get("DISPLAY"), "needs a display")
class RowsClickTests(unittest.TestCase):
    def setUp(self) -> None:
        # the app sets the theme's scale, look and font for the whole
        # process: put them back afterwards for the other window tests
        self._theme = {k: getattr(theme, k) for k in ("LOOK", "SCALE", "FONT", "MODERN")
                       if hasattr(theme, k)}
        self._palette = theme.LOOK if hasattr(theme, "LOOK") else "classic"
        cfg = load_config("")
        cfg.run_history_dir = tempfile.mkdtemp()
        self.app = PouncerApp(cfg)
        self.app._ensure_rbn_clients = lambda: None
        self.app._tick = lambda: None
        self.app.state.update(radio_info(1, 7010.0, 1111))
        self.app.root.geometry("1100x700+0+0")

    def tearDown(self) -> None:
        self.app.root.destroy()
        theme._set_palette(self._palette)
        for k, v in self._theme.items():
            setattr(theme, k, v)
        import gc
        gc.collect()

    def _ticks(self, n=3):
        for _ in range(n):
            self.app._tick_body()
            self.app.root.update()

    def test_click_after_select_and_new_spot(self) -> None:
        app = self.app
        if app._list_kind() != "rows":
            self.skipTest("default layout is not the row list")
        now = time.time()
        for c, f in (("DL1ABC", 7010.0), ("F8EXX", 7012.0), ("OK1RR", 7020.0)):
            app.spot_queue.put(_spot(c, f, now))
        self._ticks()
        app.rows.selected = 1                     # F8EXX selected
        app.spot_queue.put(_spot("G4AAA", 7005.0, now))   # sorts on top
        with self.assertNoLogs("pouncer.app", "ERROR"):
            self._ticks()
        drawn = [it.get("call") for it in app.rows.items]
        spots = [s.dxcall for s in app._row_spots]
        self.assertEqual(len(drawn), len(spots))
        got = []
        app._act_on_spot = lambda s: got.append(s.dxcall)
        rh = theme.px(ROW_H)
        for i in range(len(spots)):
            app.rows._click(types.SimpleNamespace(x=50, y=i * rh + rh // 2))
        self.assertEqual(got, spots)
        self.assertIn("F8EXX", got)
        for d, s in zip(drawn, spots):
            self.assertIn(s, str(d))


if __name__ == "__main__":
    unittest.main()
