import os
import tempfile
import types
import unittest
from unittest import mock

from pouncer import cwspeed
from pouncer.cwspeed import SpeedReader, learn, pick


def box(hwnd, value, updown=False):
    return {"hwnd": hwnd, "value": value, "updown": updown, "cls": "WindowsForms10.EDIT",
            "parent_cls": "X", "rect": (0, 0, 1, 1)}


class FakeN1MM:
    """Entry Windows with number boxes; ``texts`` = what each box shows."""

    def __init__(self, layout):
        self.layout = layout            # entry hwnd -> list of (hwnd, updown)
        self.texts = {}
        self.alive = set()
        for boxes in layout.values():
            for h, _ in boxes:
                self.alive.add(h)

    def number_boxes(self, entry):
        return [box(h, int(self.texts[h]), up) for h, up in self.layout.get(entry, [])
                if self.texts.get(h, "").isdigit()]

    def window_text(self, h):
        return self.texts.get(h)

    def is_window(self, h):
        return h in self.alive


def reader(n1mm, wins, path=""):
    return SpeedReader(lambda: dict(wins), n1mm.number_boxes, n1mm.window_text,
                       n1mm.is_window, report_path=path)


class PickTests(unittest.TestCase):
    def test_single_spin_box_wins_over_plain_boxes(self) -> None:
        got = pick([box(1, 15), box(2, 28, updown=True), box(3, 14)])
        self.assertEqual(got["hwnd"], 2)

    def test_only_one_number_box(self) -> None:
        self.assertEqual(pick([box(5, 30)])["hwnd"], 5)

    def test_not_clear(self) -> None:
        self.assertIsNone(pick([box(1, 15), box(2, 28)]))           # zone + speed?
        self.assertIsNone(pick([box(1, 25, True), box(2, 28, True)]))
        self.assertIsNone(pick([box(1, 599)]))                     # not a speed
        self.assertIsNone(pick([]))

    def test_learn_from_the_box_that_moved(self) -> None:
        before = {1: 15, 2: 28}
        self.assertEqual(learn(before, [box(1, 15), box(2, 30)], 1)["hwnd"], 2)
        self.assertIsNone(learn(before, [box(1, 15), box(2, 26)], 1))   # wrong way
        self.assertEqual(learn(before, [box(1, 15), box(2, 26)], -1)["hwnd"], 2)
        self.assertIsNone(learn(before, [box(1, 16), box(2, 30)], 1))   # both moved


class SpeedReaderTests(unittest.TestCase):
    def test_reads_and_reports_changes(self) -> None:
        n = FakeN1MM({100: [(11, False), (12, True)], 200: [(21, True)]})
        n.texts = {11: "14", 12: "28", 21: "32"}
        r = reader(n, {1: 100, 2: 200})
        r.poll_once()
        self.assertEqual(r.wpm, {1: 28, 2: 32})
        self.assertEqual([r.changes.get_nowait(), r.changes.get_nowait()],
                         [(1, None, 28), (2, None, 32)])
        n.texts[12] = "30"
        n.texts[11] = "15"                # exchange box: not the speed
        r.poll_once()
        self.assertEqual(r.changes.get_nowait(), (1, 28, 30))
        self.assertTrue(r.changes.empty())
        self.assertEqual(r.status(1), "30 WPM")

    def test_ignores_busy_and_garbage(self) -> None:
        n = FakeN1MM({100: [(12, True)]})
        n.texts = {12: "28"}
        r = reader(n, {1: 100})
        r.poll_once()
        n.texts[12] = None               # N1MM busy (time-out)
        r.poll_once()
        n.texts[12] = ""                 # being typed
        r.poll_once()
        self.assertEqual(r.wpm, {1: 28})

    def test_learns_after_wpm_plus(self) -> None:
        n = FakeN1MM({100: [(11, False), (12, False)]})
        n.texts = {11: "14", 12: "28"}
        r = reader(n, {1: 100})
        r.poll_once()
        self.assertEqual(r.wpm, {})
        self.assertIn("press WPM +", r.status(1))
        r.expect(1, 1)                   # before PgUp
        n.texts[12] = "30"               # N1MM: +2
        r.poll_once()
        self.assertEqual(r.box, {1: 12})
        self.assertEqual(r.wpm, {1: 30})
        self.assertIn("learned", r.how[1])

    def test_new_entry_window_finds_again(self) -> None:
        n = FakeN1MM({100: [(12, True)], 300: [(31, True)]})
        n.texts = {12: "28", 31: "24"}
        wins = {1: 100}
        r = SpeedReader(lambda: dict(wins), n.number_boxes, n.window_text, n.is_window)
        r.poll_once()
        wins[1] = 300                    # N1MM restarted
        r.poll_once()
        self.assertEqual(r.box, {1: 31})
        self.assertEqual(r.wpm, {1: 24})
        del wins[1]                      # radio gone
        r.poll_once()
        self.assertEqual(r.wpm, {})

    def test_report_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache", "cwspeed.txt")
            n = FakeN1MM({100: [(11, False), (12, True)]})
            n.texts = {11: "14", 12: "28"}
            r = reader(n, {1: 100}, path)
            r.poll_once()
            r.poll_once()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("speed box 12 = 28 WPM (spin box)", text)
            self.assertIn("plain box", text)
            self.assertEqual(text.count("Entry Window 100"), 1)   # written once

    def test_report_lists_all_controls_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cwspeed.txt")
            n = FakeN1MM({100: []})
            r = SpeedReader(lambda: {1: 100}, n.number_boxes, n.window_text, n.is_window,
                            report_path=path, describe=lambda h: ["7  WindowsForms10.STATIC  '28'"])
            r.poll_once()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("NOT found", text)
            self.assertIn("WindowsForms10.STATIC  '28'", text)


class AppSpeedTests(unittest.TestCase):
    """The main window's text and the popup on a change."""

    def _fake(self, wpm, radios=(1,)):
        from tests.test_so2r import _import_app
        app = _import_app()
        rd = types.SimpleNamespace(wpm=dict(wpm), changes=__import__("queue").Queue(),
                                   status=lambda nr: "")
        f = types.SimpleNamespace(
            cwspeed=rd, radio=1, cfg=types.SimpleNamespace(cw_speed_popup=True),
            state=types.SimpleNamespace(radio_numbers=lambda: list(radios)),
        )
        for name in ("_wpm", "_wpm_text", "_drain_wpm", "_radio_tag", "_so2r", "_radio_nr"):
            setattr(f, name, getattr(app.PouncerApp, name).__get__(f))
        f._radio_nr = lambda radio=None: radio or f.radio
        f.popups = []
        f._show_wpm_popup = f.popups.append
        return app, f, rd

    def test_text(self) -> None:
        _, f, _ = self._fake({1: 28})
        self.assertEqual(f._wpm_text(), "CW 28 WPM")
        _, f, _ = self._fake({1: 28, 2: 32}, radios=(1, 2))
        self.assertEqual(f._wpm_text(), "CW A 28 / B 32 WPM")

    def test_popup_only_on_a_change(self) -> None:
        _, f, rd = self._fake({1: 30}, radios=(1, 2))
        rd.changes.put((1, None, 28))      # first reading
        rd.changes.put((2, 28, 30))
        rd.changes.put((1, 30, 26))
        f._drain_wpm()
        self.assertEqual(f.popups, ["B: CW 30 WPM ▲", "A: CW 26 WPM ▼"])
        f.cfg.cw_speed_popup = False
        rd.changes.put((1, 26, 28))
        f._drain_wpm()
        self.assertEqual(len(f.popups), 2)


if __name__ == "__main__":
    unittest.main()
