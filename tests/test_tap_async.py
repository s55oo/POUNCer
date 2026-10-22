"""A tap runs as steps between window updates: _act_on_spot returns at once
(QSY sent), the rest comes from root.after; a new tap stops the old one."""

import types
import unittest
from unittest import mock

from tests.test_so2r import AppRadioTests, _import_app


class FakeRoot:
    def __init__(self):
        self.jobs = []

    def after(self, ms, fn):
        self.jobs.append((ms, fn))

    def run(self, clock):
        while self.jobs:
            ms, fn = self.jobs.pop(0)
            clock[0] += ms / 1000
            fn()


class TapAsyncTests(unittest.TestCase):
    def test_returns_at_once_then_finishes(self) -> None:
        app = _import_app()
        f = AppRadioTests._fake(AppRadioTests(), app, radio=1)
        f.cfg.qsy_wait_ms, f.cfg.qsy_settle_ms, f.cfg.type_mode = 2500, 250, "settext"
        f.cfg.return_focus = False
        f._guard_call_steps = app.PouncerApp._guard_call_steps.__get__(f)
        f._run_steps = app.PouncerApp._run_steps.__get__(f)
        f._tap_async, f._tap_gen, f.root, f._last_tap = True, None, FakeRoot(), {}
        notes = []
        clock = [1000.0]
        sleeps = []
        spot = app.Spot(dxcall="DL5ARM", freq_khz=7008.6, mode="CW", snr=21, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="0857Z", received=0)
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app, "set_focused_edit_text", return_value=True), \
                mock.patch.object(app, "focused_edit_text", return_value="DL5ARM"), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app, "_note_tap", side_effect=lambda *a: notes.append(a)), \
                mock.patch.object(app.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(app.time, "sleep", side_effect=sleeps.append):
            app.PouncerApp._act_on_spot(f, spot, 1)
            self.assertEqual(notes, [])            # not finished: waiting for N1MM
            self.assertTrue(f.root.jobs)           # ...between window updates
            # a second tap stops the first
            first = f._tap_gen
            app.PouncerApp._act_on_spot(f, spot, 1)
            self.assertIsNot(f._tap_gen, first)
            f.root.run(clock)
        self.assertEqual(sleeps, [])               # the window never slept
        self.assertEqual(len([s for s in f.sent if s[0] == "qsy"]), 2)   # 2nd tap: QSY + again; 1st: none
        self.assertEqual(len(notes), 1)            # only the second tap finished
        self.assertIsNone(f._tap_gen)


if __name__ == "__main__":
    unittest.main()
