"""A tap whose frequency N1MM reports late: no false 'check the radio'."""

import os
import tempfile
import time
import types
import unittest

from pouncer.config import load_config


def _app():
    from tests.test_so2r import _import_app
    return _import_app()


class LateQsyTests(unittest.TestCase):
    def _fake(self, freq_now):
        app = _app()
        spot = app.Spot(dxcall="DL5ARM", freq_khz=7008.6, mode="CW", snr=21, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="0857Z", received=1.0)
        state = {"f": freq_now}
        f = types.SimpleNamespace(
            cfg=types.SimpleNamespace(qsy_wait_ms=2500),
            state=types.SimpleNamespace(snapshot=lambda nr: {"freq_khz": state["f"]}),
            _radio_tag=lambda nr: "", LATE_QSY_S=app.PouncerApp.LATE_QSY_S,
            _late_qsy={"spot": spot, "nr": 1, "t0": time.time() - 3.2, "typed": True},
            _last_tap={"n": 4})
        f._check_late_qsy = app.PouncerApp._check_late_qsy.__get__(f)
        return f, state

    def test_late_report_turns_into_ok(self) -> None:
        f, state = self._fake(7022.2)
        f._check_late_qsy()
        self.assertIsNotNone(f._late_qsy)                   # not there yet: still watching
        self.assertEqual(f._last_tap["n"], 4)
        state["f"] = 7008.6
        f._check_late_qsy()
        self.assertIsNone(f._late_qsy)
        self.assertTrue(f._last_tap["ok"])
        self.assertEqual(f._last_tap["n"], 5)               # the phone sees a new result
        self.assertIn("reported it late, 3.", f._last_tap["text"])
        self.assertIn("call in", f._last_tap["text"])

    def test_never_reported_warns(self) -> None:
        f, _ = self._fake(7022.2)
        f._late_qsy["t0"] = time.time() - 30
        f._check_late_qsy()
        self.assertIsNone(f._late_qsy)
        self.assertFalse(f._last_tap["ok"])
        self.assertIn("never reported 7008.6", f._last_tap["text"])


class WaitDefaultTests(unittest.TestCase):
    def test_old_default_read_as_new(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[n1mm]\nqsy_wait_ms = 1500\n")
            self.assertEqual(load_config(p).qsy_wait_ms, 2500)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[n1mm]\nqsy_wait_ms = 4000\n")
            self.assertEqual(load_config(p).qsy_wait_ms, 4000)      # yours: kept
        self.assertEqual(load_config("").qsy_wait_ms, 2500)


if __name__ == "__main__":
    unittest.main()


class RadioATimingTests(unittest.TestCase):
    """Radio A (UDP QSY) with a slow N1MM: the QSY is sent again when no
    report comes, the call survives N1MM's late wipe, and a wiped box is
    filled again after a very late report - never over what you typed."""

    def _run(self, report_after_qsy: int, report_delay: float, wipe=True):
        from unittest import mock
        from tests.test_so2r import AppRadioTests, radio_info
        app = _app()
        f = AppRadioTests._fake(AppRadioTests(), app, radio=1)
        f.cfg.qsy_wait_ms, f.cfg.qsy_settle_ms, f.cfg.type_mode = 2500, 250, "settext"
        f.cfg.return_focus = False
        f._guard_call = app.PouncerApp._guard_call.__get__(f)
        f._restore_call = app.PouncerApp._restore_call.__get__(f)
        clock = [1000.0]
        box = {"t": ""}
        qsys = []
        f.controller.set_frequency.side_effect = lambda khz, **kw: qsys.append(clock[0])

        def tick():
            # N1MM answers (and wipes the callsign) only after the n-th QSY
            if len(qsys) >= report_after_qsy and clock[0] >= qsys[report_after_qsy - 1] + report_delay \
                    and f.state.snapshot(1)["freq_khz"] != 7008.6:
                f.state.update(radio_info(1, 7008.6, 1111))
                if wipe:
                    box["t"] = ""

        def sleep(sec):
            clock[0] += sec
            tick()
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app, "set_focused_edit_text", side_effect=lambda h, t: box.update(t=t) or True), \
                mock.patch.object(app, "focused_edit_text", side_effect=lambda h: box["t"]), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(app.time, "sleep", side_effect=sleep):
            spot = app.Spot(dxcall="DL5ARM", freq_khz=7008.6, mode="CW", snr=21, wpm=28,
                            spot_type="CQ", spotter="S53WW", time_utc="0857Z", received=0)
            app.PouncerApp._act_on_spot(f, spot, 1)
            sleep(3)
        return len(qsys), box["t"]

    def test_no_report_qsy_sent_again(self) -> None:
        n, call = self._run(report_after_qsy=2, report_delay=0.5)
        self.assertEqual(n, 2)                    # QSY sent a second time
        self.assertEqual(call, "DL5ARM")

    def test_wipe_after_the_call_is_repaired(self) -> None:
        # no report for the first QSY; the second one's report (and wipe)
        # comes 3.4 s after it - after its 2.5 s wait and the re-check, when the call is
        # already in: the guard puts the call back
        n, call = self._run(report_after_qsy=2, report_delay=3.4)
        self.assertEqual(n, 2)
        self.assertEqual(call, "DL5ARM")

    def test_restore_only_into_an_empty_box(self) -> None:
        from unittest import mock
        app = _app()
        f = types.SimpleNamespace(cfg=types.SimpleNamespace(type_mode="settext"))
        f._settext = app.PouncerApp._settext.__get__(f)
        box = {"t": "OK1XYZ"}
        with mock.patch.object(app, "focused_edit_text", side_effect=lambda h: box["t"]), \
                mock.patch.object(app, "set_focused_edit_text", side_effect=lambda h, t: box.update(t=t)):
            self.assertFalse(app.PouncerApp._restore_call(f, 1111, "DL5ARM"))
            self.assertEqual(box["t"], "OK1XYZ")             # your typing stays
            box["t"] = ""
            self.assertTrue(app.PouncerApp._restore_call(f, 1111, "DL5ARM"))
            self.assertEqual(box["t"], "DL5ARM")
