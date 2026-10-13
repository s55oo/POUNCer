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
            taps=types.SimpleNamespace(record=lambda call, khz: sent.append(("tap", call, khz))),
            cfg=types.SimpleNamespace(focus_n1mm=True, wipe_before_call=True, radio_b_qsy="type"),
            set_call_var=types.SimpleNamespace(get=lambda: True),
            status_label=types.SimpleNamespace(configure=lambda **kw: None),
        )
        f._band_memory, f._tune_last = {}, {}
        for name in ("_radio_nr", "_radio_tag", "_so2r", "_current_band", "_radio_khz",
                     "_focus_radio", "_qsy", "_types_qsy", "_snapshot_focus", "_return_focus",
                     "_wait_for_qsy", "_check_call", "_freq_text",
                     "_settext", "_enter_text"):
            setattr(f, name, getattr(app.PouncerApp, name).__get__(f))
        return f

    def test_tap_on_radio_b(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=1)
        focused, typed = [], []
        spot = app.Spot(dxcall="K1ABC", freq_khz=7011.0, mode="CW", snr=10, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=0)
        enters = []
        with mock.patch.object(app, "force_foreground", side_effect=lambda h: focused.append(h) or True), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app, "clear_focused_edit", return_value=True), \
                mock.patch.object(app, "type_text", side_effect=typed.append), \
                mock.patch.object(app, "send_enter", side_effect=lambda: enters.append(1) or True), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._act_on_spot(f, spot, 2)   # the phone chose radio B
        self.assertEqual(focused, [2222])                       # Entry Window B
        # radio B is QSYed by typing the kHz + Enter into Entry Window B
        # (N1MM's UDP QSY moved radio A in SO2R), then the call is typed
        self.assertFalse([x for x in f.sent if x[0] == "qsy"])
        self.assertEqual(typed, ["7011", "K1ABC"])
        self.assertEqual(enters, [1])
        self.assertIn(("cmd", "{WIPE}", 2, 2222), f.sent)        # RadioNr 2
        self.assertIn(("tap", "K1ABC", 7011.0), f.sent)          # for the summary

    def test_cursor_returns_to_where_it_was(self) -> None:
        # typing the exchange in Entry Window A, tap a station for radio B:
        # B gets QSY + call, then the cursor goes back to A's exchange box
        app = _import_app()
        f = self._fake(app, radio=2)
        f.cfg.return_focus = True
        snap = {"fg": 1111, "focus": 5555, "sel": (3, 3), "pid": 42}
        restored = []
        spot = app.Spot(dxcall="K1ABC", freq_khz=7011.0, mode="CW", snr=10, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=0)
        status = []
        f.status_label = types.SimpleNamespace(configure=lambda **kw: status.append(kw["text"]))
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app, "focus_snapshot", return_value=snap), \
                mock.patch.object(app, "window_pid", return_value=42), \
                mock.patch.object(app, "restore_focus", side_effect=lambda s: restored.append(s) or True), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app, "clear_focused_edit", return_value=True), \
                mock.patch.object(app, "type_text"), \
                mock.patch.object(app, "send_enter", return_value=True), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._act_on_spot(f, spot, 2)
            self.assertEqual(restored, [snap])
            self.assertIn("cursor back in Entry Window A", status[-1])
            # already in Entry Window B, or in another program: stay
            restored.clear()
            snap.update(fg=2222)
            app.PouncerApp._act_on_spot(f, spot, 2)
            snap.update(fg=9999)
            with mock.patch.object(app, "window_pid", side_effect=lambda h: 7 if h == 2222 else 42):
                app.PouncerApp._act_on_spot(f, spot, 2)
            self.assertEqual(restored, [])

    def test_call_typed_only_after_n1mm_reports_new_frequency(self) -> None:
        # N1MM can wipe the call when the radio moves: wait for the new
        # frequency in RadioInfo, then put the call in; again if it got lost
        app = _import_app()
        f = self._fake(app, radio=1)
        f.cfg.qsy_wait_ms, f.cfg.qsy_settle_ms, f.cfg.type_mode = 1500, 250, "settext"
        events = []

        def fake_sleep(sec):
            events.append(("sleep", sec))
            if len([e for e in events if e[0] == "sleep"]) == 3:   # radio arrives
                f.state.update(radio_info(1, 14031.0, 1111))

        boxes = iter(["", "HB9CBR/P"])     # first check: the late wipe cleared it
        spot = app.Spot(dxcall="HB9CBR/P", freq_khz=14031.0, mode="CW", snr=10, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=0)
        status = []
        f.status_label = types.SimpleNamespace(configure=lambda **kw: status.append(kw["text"]))
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app, "wipe_callsign", side_effect=lambda: events.append(("altw",))), \
                mock.patch.object(app, "set_focused_edit_text",
                                  side_effect=lambda h, t: events.append(("set", t)) or True), \
                mock.patch.object(app, "type_text", side_effect=lambda t: events.append(("type", t))), \
                mock.patch.object(app, "focused_edit_text", side_effect=lambda h: next(boxes)), \
                mock.patch.object(app.time, "sleep", side_effect=fake_sleep):
            app.PouncerApp._act_on_spot(f, spot, 1)
        first_set = events.index(("set", "HB9CBR/P"))
        self.assertIn(("sleep", 0.25), events[:first_set])       # settled after the QSY
        # set in one go (no keystrokes that a slow PC could mix up), twice
        self.assertEqual([e for e in events if e[0] in ("set", "type")], [("set", "HB9CBR/P")] * 2)
        self.assertNotIn(("altw",), events)                      # no queued Alt+W either
        self.assertIn("call put in again", status[-1])

    def test_keys_mode_never_retypes(self) -> None:
        # typed keys: a slow PC may still be typing - typing again mixed
        # them up ("PHB9CBR/P"), so no second attempt
        app = _import_app()
        f = self._fake(app, radio=1)
        f.cfg.type_mode = "keys"
        typed = []
        spot = app.Spot(dxcall="HB9CBR/P", freq_khz=14031.0, mode="CW", snr=10, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=0)
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app, "clear_focused_edit", return_value=True), \
                mock.patch.object(app, "type_text", side_effect=typed.append), \
                mock.patch.object(app, "focused_edit_text", return_value="HB9C"), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._act_on_spot(f, spot, 1)
        self.assertEqual(typed, ["HB9CBR/P"])

    def test_typed_frequency_uses_the_windows_decimal_sign(self) -> None:
        # bug: "14000.1" typed on a Slovenian PC -> N1MM read 140001 kHz
        app = _import_app()
        f = self._fake(app)
        f.cfg.freq_decimal = "auto"
        with mock.patch.object(app, "decimal_separator", return_value=","):
            self.assertEqual(f._freq_text(14000.1), "14000,1")
            self.assertEqual(f._freq_text(14001.0), "14001")
            self.assertEqual(f._freq_text(7012.05), "7012,05")
        with mock.patch.object(app, "decimal_separator", return_value="."):
            self.assertEqual(f._freq_text(14000.1), "14000.1")
        f.cfg.freq_decimal = ","
        self.assertEqual(f._freq_text(21025.3), "21025,3")

    def test_tap_on_radio_a_uses_udp(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=1)
        typed = []
        spot = app.Spot(dxcall="DL1AA", freq_khz=14025.3, mode="CW", snr=10, wpm=28,
                        spot_type="CQ", spotter="S53WW", time_utc="1200Z", received=0)
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app, "wipe_callsign"), \
                mock.patch.object(app, "clear_focused_edit", return_value=True), \
                mock.patch.object(app, "type_text", side_effect=typed.append), \
                mock.patch.object(app, "send_enter") as enter, \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._act_on_spot(f, spot, 1)
        self.assertIn(("qsy", 14025.3, {"radio_nr": 1}), f.sent)
        self.assertEqual(typed, ["DL1AA"])
        enter.assert_not_called()

    def test_radio_b_udp_option(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=2)
        f.cfg.radio_b_qsy = "udp"
        with mock.patch.object(app, "force_foreground", return_value=True), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._qsy(f, 2, 7012.5, 2222, True)
        self.assertIn(("qsy", 7012.5, {"radio_nr": 2}), f.sent)

    def test_key_goes_to_radio_b_window(self) -> None:
        app = _import_app()
        f = self._fake(app, radio=2)                             # main window on B
        f._send_key = lambda key: True
        focused = []
        with mock.patch.object(app, "force_foreground", side_effect=lambda h: focused.append(h) or True), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._press_key(f, "F2")
        self.assertEqual(focused, [2222])

    def test_band_menu_on_radio_b_focuses_window_b(self) -> None:
        # bug v0.7.14: band QSY for B moved A - N1MM QSYs the focused radio,
        # so Entry Window B must be in front first (like a spot tap)
        app = _import_app()
        f = self._fake(app, radio=1)
        focused = []
        typed = []
        with mock.patch.object(app, "force_foreground", side_effect=lambda h: focused.append(h) or True), \
                mock.patch.object(app, "clear_focused_edit", return_value=True), \
                mock.patch.object(app, "type_text", side_effect=typed.append), \
                mock.patch.object(app, "send_enter", return_value=True), \
                mock.patch.object(app.time, "sleep"):
            app.PouncerApp._qsy_band(f, "20m", 2)
            app.PouncerApp._tune(f, 50, 2)
        self.assertEqual(focused, [2222, 2222])
        self.assertEqual(typed, ["14020", "7012.05"])           # typed into Entry Window B

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

    def test_band_param_asks_for_that_bands_list(self) -> None:
        import json
        import urllib.request
        from pouncer.web import WebServer
        got = []
        n = lambda *a, **k: None
        web = WebServer("127.0.0.1", 0,
                        lambda radio=None, band=None: got.append((radio, band)) or {"band": band},
                        n, n, n, n, n, n)
        web.start()
        try:
            for path in ("/api/spots?band=15m&radio=B", "/api/spots?band=40m", "/api/spots?radio=2"):
                with urllib.request.urlopen(f"http://127.0.0.1:{web.port}{path}", timeout=5) as r:
                    json.load(r)
        finally:
            web.stop()
        self.assertEqual(got, [(2, "15m"), (None, "40m"), (2, None)])

    def test_app_builds_the_viewed_band_next_tick(self) -> None:
        app = _import_app()
        f = types.SimpleNamespace(
            radio=1, _web_cache={2: {"radio": 2, "band": "40m"}},
            _web_views={}, _web_view_cache={})
        got = app.PouncerApp._web_spots(f, 2, "15m")
        self.assertEqual(got["pending"], "15m")                  # not built yet
        self.assertIn((2, "15m"), f._web_views)
        f._web_view_cache[(2, "15m")] = {"radio": 2, "band": "15m", "view": True}
        self.assertTrue(app.PouncerApp._web_spots(f, 2, "15m")["view"])
        self.assertEqual(app.PouncerApp._web_spots(f, 2, "40m")["band"], "40m")   # its own band
        self.assertEqual(app.PouncerApp._web_spots(f, 2)["band"], "40m")

    def test_page_has_radio_switch(self) -> None:
        from pouncer.web import INDEX_HTML, HELP_HTML
        self.assertIn('id="radiosw"', INDEX_HTML)
        self.assertIn("pouncer.radio", INDEX_HTML)
        self.assertIn("Two radios", HELP_HTML)
        self.assertIn("data.freqs", INDEX_HTML)                 # A over B in the header
        self.assertIn("lastData.rbands", INDEX_HTML)            # band menu: row per radio
        self.assertIn("pouncer.view", INDEX_HTML)               # band menu = show band, no QSY


class TestOptionsPageTests(unittest.TestCase):
    def test_freq_from_radio_and_calm_options(self) -> None:
        from pouncer.web import INDEX_HTML, HELP_HTML
        for key in ("pouncer.freqfrom", "pouncer.calm"):
            self.assertIn(key, INDEX_HTML)
            self.assertIn(key, HELP_HTML)                       # on / off there
        self.assertIn('id="opt-calm"', HELP_HTML)
        self.assertIn('id="opt-freqfrom"', HELP_HTML)


if __name__ == "__main__":
    unittest.main()
