"""SO2R / SO2V: radio A / B selection (QSY packet, Entry Window, band follow)."""

import socket
import sys
import types
import unittest
from unittest import mock

from pouncer.n1mm import N1MMController, RadioState


def radio_info(nr, freq_khz, hwnd, active=1):
    return {"radio_nr": nr, "freq_khz": freq_khz, "mode": "CW", "active_radio": active,
            "entry_window_hwnd": hwnd, "station": "", "tx_freq_khz": None}


def two_radios() -> RadioState:
    st = RadioState()
    st.update(radio_info(1, 14025.0, 1111))   # radio A on 20 m, Entry Window A
    st.update(radio_info(2, 7012.0, 2222))    # radio B on 40 m, Entry Window B
    return st


class RadioStateTests(unittest.TestCase):
    def test_per_radio_snapshot_and_hwnd(self) -> None:
        st = two_radios()
        self.assertEqual(st.radio_numbers(), [1, 2])
        self.assertEqual(st.snapshot(1)["band"], "20m")
        self.assertEqual(st.snapshot(2)["band"], "40m")        # band follow: radio B
        self.assertEqual(st.entry_window_hwnd(2), 2222)        # Entry Window B
        self.assertEqual(st.entry_window_hwnd(1), 1111)
        self.assertEqual(st.active_radio_nr(2), 2)

    def test_single_radio_falls_back_to_it(self) -> None:
        st = RadioState()
        st.update(radio_info(1, 14025.0, 1111))
        self.assertEqual(st.snapshot(2)["band"], "20m")         # SO1R: B = the one radio
        self.assertEqual(st.entry_window_hwnd(2), 1111)
        self.assertEqual(st.active_radio_nr(2), 1)
        self.assertEqual(st.snapshot()["band"], "20m")           # unchanged default


class QsyPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("127.0.0.1", 0))
        self.rx.settimeout(2)
        self.ctl = N1MMController("127.0.0.1", self.rx.getsockname()[1], radionr=1)

    def tearDown(self) -> None:
        self.ctl.close()
        self.rx.close()

    def test_radio_2_uses_right_button(self) -> None:
        # N1MM docs: mouse button Left = radio 1, Right = radio 2 (SO2V/SO2R)
        self.ctl.set_frequency(7012.0, radio_nr=2)
        pkt = self.rx.recv(4096).decode()
        self.assertIn("<radionr>2</radionr>", pkt)
        self.assertIn("<mousebutton>Right</mousebutton>", pkt)
        self.ctl.set_frequency(14025.0)
        pkt = self.rx.recv(4096).decode()
        self.assertIn("<radionr>1</radionr>", pkt)
        self.assertIn("<mousebutton>Left</mousebutton>", pkt)


def _import_app():
    """pouncer.app, with a stub tkinter where Tk isn't installed."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        tk = types.ModuleType("tkinter")
        ttk = types.ModuleType("tkinter.ttk")
        ttk.Frame = object
        tk.ttk = ttk
        sys.modules["tkinter"], sys.modules["tkinter.ttk"] = tk, ttk
        try:
            from pouncer import app
        finally:   # don't leave the stub for other tests (they skip without Tk)
            sys.modules.pop("tkinter", None)
            sys.modules.pop("tkinter.ttk", None)
        return app
    from pouncer import app
    return app


class AppRadioTests(unittest.TestCase):
    """Taps, keys and band follow go to the chosen radio's Entry Window."""

    def _fake(self, app, radio=1):
        st = two_radios()
        sent = []
        ctl = mock.Mock()
        ctl.set_frequency.side_effect = lambda khz, **kw: sent.append(("qsy", khz, kw))
        ctl.send_radio_command.side_effect = lambda cmd, nr, hwnd: sent.append(("cmd", cmd, nr, hwnd))
        f = types.SimpleNamespace(
            state=st, radio=radio, controller=ctl, sent=sent,
            cfg=types.SimpleNamespace(focus_n1mm=True, wipe_before_call=True),
            set_call_var=types.SimpleNamespace(get=lambda: True),
            status_label=types.SimpleNamespace(configure=lambda **kw: None),
        )
        for name in ("_radio_nr", "_radio_tag", "_so2r", "_current_band", "_radio_khz"):
            setattr(f, name, getattr(app.PouncerApp, name).__get__(f))
        return f

    def test_tap_on_radio_b(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=1)
        focused, typed = [], []
        spot = app.Spot(dxcall="K1ABC", freq_khz=7011.0, mode="CW", snr=10, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=0)
        with mock.patch.object(app, "force_foreground", side_effect=lambda h: focused.append(h) or True), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app, "clear_focused_edit", return_value=True), \
                mock.patch.object(app, "type_text", side_effect=typed.append), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._act_on_spot(f, spot, 2)   # the phone chose radio B
        self.assertEqual(focused, [2222])                       # Entry Window B
        self.assertIn(("qsy", 7011.0, {"radio_nr": 2}), f.sent)  # QSY radio 2
        self.assertIn(("cmd", "{WIPE}", 2, 2222), f.sent)        # RadioNr 2
        self.assertEqual(typed, ["K1ABC"])

    def test_key_goes_to_radio_b_window(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=2)                             # main window on B
        f._send_key = lambda key: True
        focused = []
        with mock.patch.object(app, "force_foreground", side_effect=lambda h: focused.append(h) or True), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._press_key(f, "F2")
        self.assertEqual(focused, [2222])

    def test_band_follows_chosen_radio(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=1)
        self.assertEqual(f._current_band(), "20m")
        self.assertEqual(f._current_band(2), "40m")
        f.radio = 2
        self.assertEqual(f._current_band(), "40m")
        self.assertEqual(f._radio_khz(), 7012.0)


class WebRadioTests(unittest.TestCase):
    """The phone sends ?radio=A|B (1|2); handlers get radio= only then."""

    def test_radio_param_reaches_handlers(self) -> None:
        import json
        import urllib.request
        from pouncer.web import WebServer
        got = []

        def spots(radio=None):
            got.append(("spots", radio))
            return {"band": "40m" if radio == 2 else "20m", "spots": [], "radio": radio or 1}

        web = WebServer(
            "127.0.0.1", 0, spots,
            lambda call, freq, mode, radio=None: got.append(("qsy", call, radio)),
            lambda key, radio=None: got.append(("key", key, radio)),
            lambda: None,
            lambda band, radio=None: got.append(("band", band, radio)),
            lambda *a: None,
            lambda hz, radio=None: got.append(("tune", hz, radio)),
        )
        web.start()
        base = f"http://127.0.0.1:{web.port}"
        try:
            def get(path):
                with urllib.request.urlopen(base + path, timeout=5) as r:
                    return json.load(r)
            self.assertEqual(get("/api/spots?radio=2")["band"], "40m")
            self.assertEqual(get("/api/spots")["band"], "20m")
            get("/api/qsy?call=K1ABC&freq=7011&mode=CW&radio=B")
            get("/api/key?key=F2&radio=2")
            get("/api/band?band=40m&radio=2")
            get("/api/tune?hz=25&radio=A")
            get("/api/key?key=F3")                       # no radio: as before
        finally:
            web.stop()
        self.assertEqual(got, [
            ("spots", 2), ("spots", None), ("qsy", "K1ABC", 2), ("key", "F2", 2),
            ("band", "40m", 2), ("tune", 25, 1), ("key", "F3", None),
        ])

    def test_page_has_radio_switch(self) -> None:
        from pouncer.web import INDEX_HTML, HELP_HTML
        self.assertIn('id="radiosw"', INDEX_HTML)
        self.assertIn("pouncer.radio", INDEX_HTML)
        self.assertIn("Two radios", HELP_HTML)


if __name__ == "__main__":
    unittest.main()
