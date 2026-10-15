"""Phone CW keyboard: /api/cw and the app's N1MM Ctrl+K window handling."""

import json
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

from pouncer.web import WebServer, clean_cw_text


class CwApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ops = []
        self.web = WebServer("127.0.0.1", 0, lambda: {"spots": []},
                             lambda *a: None, lambda key: None)
        self.web.start()
        self.url = f"http://127.0.0.1:{self.web.port}"

    def tearDown(self) -> None:
        self.web.stop()

    def _get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as r:
            return json.loads(r.read())

    def test_off_until_a_handler_is_set(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/cw?op=open")
        self.assertEqual(cm.exception.code, 404)

    def test_ops_in_order_with_radio(self) -> None:
        self.web.set_cw_handler(lambda op, text, n, radio=None: self.ops.append((op, text, n, radio)))
        self._get("/api/cw?op=open&radio=B")
        self._get("/api/cw?op=text&text=cq%20test%0A%1B&radio=B")
        self._get("/api/cw?op=back&n=2")
        self._get("/api/cw?op=done")
        self.assertEqual(self.ops, [("open", "", 0, 2), ("text", "CQ TEST", 0, 2),
                                    ("back", "", 2, None), ("done", "", 0, None)])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/cw?op=format")
        self.assertEqual(cm.exception.code, 400)

    def test_clean(self) -> None:
        self.assertEqual(clean_cw_text("s55oo/p 5nn?\t\x1b"), "S55OO/P 5NN?")
        self.assertEqual(len(clean_cw_text("e" * 500)), 200)

    def test_page_has_the_keyboard(self) -> None:
        with urllib.request.urlopen(self.url + "/", timeout=5) as r:
            page = r.read().decode()
        for part in ('id="cwkb"', 'id="cwin"', "armHold", "/api/cw?", "cwdone", "cwstop"):
            self.assertIn(part, page)


def _fake_app(fg_after_ctrl_k):
    from tests.test_so2r import _import_app, two_radios
    app = _import_app()
    msgs = []
    f = types.SimpleNamespace(
        state=two_radios(), radio=1, status_label=types.SimpleNamespace(
            configure=lambda **kw: msgs.append(kw["text"])),
        root=types.SimpleNamespace(after=lambda ms, fn: None),
        _cw_win=None, _cw_radio=None, _cw_error="", _cw_error_n=0, _cw_pumping=False,
    )
    for name in ("_cw", "_cw_open", "_cw_ready", "_cw_close", "_cw_fail", "_cw_pump",
                 "_radio_nr", "_so2r"):
        setattr(f, name, getattr(app.PouncerApp, name).__get__(f))
    keys = []
    fg = {"now": 0}

    def front(h):
        fg["now"] = h
        keys.append(("front", h))
        return True

    def ctrl_k():
        keys.append("ctrl+k")
        fg["now"] = fg_after_ctrl_k
        return True
    patches = [
        mock.patch.object(app, "force_foreground", side_effect=front),
        mock.patch.object(app.win32mod, "send_ctrl_k", side_effect=ctrl_k),
        mock.patch.object(app.win32mod, "foreground_window", side_effect=lambda: fg["now"]),
        mock.patch.object(app.win32mod, "is_window", return_value=True),
        mock.patch.object(app.win32mod, "send_escape", side_effect=lambda: keys.append("esc")),
        mock.patch.object(app.win32mod, "send_backspace", side_effect=lambda n: keys.append(("bs", n))),
        mock.patch.object(app, "send_enter", side_effect=lambda: keys.append("enter")),
        mock.patch.object(app, "type_text", side_effect=lambda t: keys.append(("type", t))),
        mock.patch.object(app, "window_pid", return_value=42),
        mock.patch.object(app.time, "sleep"),
    ]
    return app, f, keys, msgs, patches


class CwAppTests(unittest.TestCase):
    def _run(self, fg_after, ops):
        app, f, keys, msgs, patches = _fake_app(fg_after)
        for p in patches:
            p.start()
        try:
            for op in ops:
                f._cw(*op)
        finally:
            for p in patches:
                p.stop()
        return f, keys, msgs

    def test_open_type_done_on_radio_b(self) -> None:
        f, keys, msgs = self._run(7777, [("open", "", 0, 2), ("text", "cq", 0, 2),
                                         ("back", "", 1, 2), ("text", "Q TEST", 0, 2),
                                         ("done", "", 0, 2)])
        self.assertEqual(keys, [("front", 2222), "ctrl+k", ("type", "CQ"), ("bs", 1),
                                ("type", "Q TEST"), "enter"])
        self.assertIsNone(f._cw_win)
        self.assertIn("rest is sent", msgs[-1])

    def test_never_types_into_the_entry_window(self) -> None:
        # Ctrl+K didn't open a window: the Entry Window still has the keyboard
        f, keys, msgs = self._run(1111, [("open", "", 0, 1), ("text", "TEST", 0, 1)])
        self.assertNotIn(("type", "TEST"), keys)
        self.assertEqual(f._cw_error_n, 2)          # open failed, and again for the text
        self.assertIn("did not open", msgs[-1])

    def test_stop_is_escape(self) -> None:
        f, keys, _ = self._run(7777, [("text", "TEST", 0, 1), ("stop", "", 0, 1)])
        self.assertEqual(keys[-1], "esc")
        self.assertEqual(keys[:3], [("front", 1111), "ctrl+k", ("type", "TEST")])

    def test_window_focus_lost_brings_it_back(self) -> None:
        app, f, keys, _, patches = _fake_app(7777)
        for p in patches:
            p.start()
        try:
            f._cw("open", "", 0, 1)
            app.win32mod.foreground_window.side_effect = lambda: 1111   # user clicked N1MM
            f._cw("text", "5NN", 0, 1)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(keys[-2:], [("front", 7777), ("type", "5NN")])


if __name__ == "__main__":
    unittest.main()
