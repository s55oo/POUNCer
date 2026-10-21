"""Station test: QSY up / back per radio and a test call into the empty box."""

import unittest

from pouncer import stationtest


class Fake:
    def __init__(self, report=True, box=""):
        self.freq = {1: 14025.0, 2: 7012.0}
        self.report = report
        self.box = {11: box, 22: box}
        self.sent = []

    def snapshot(self, nr):
        return {"freq_khz": self.freq[nr]}

    def qsy(self, nr, khz):
        self.sent.append((nr, khz))
        if self.report:
            self.freq[nr] = khz
        return True

    def wait_for(self, nr, khz):
        return abs(self.freq[nr] - khz) < 0.01

    def run(self, radios=(1, 2), **kw):
        return stationtest.run(list(radios), self.snapshot, self.qsy, self.wait_for,
                               entry_hwnd={1: 11, 2: 22}.get, read_box=self.box.get,
                               set_box=lambda h, t: self.box.__setitem__(h, t) or True,
                               clock=lambda: 0.0, sleep=lambda s: None, **kw)


class StationTests(unittest.TestCase):
    def test_all_ok_and_back_on_the_frequency(self) -> None:
        f = Fake()
        res = f.run()
        self.assertTrue(all(r["ok"] for r in res))
        self.assertEqual(f.sent, [(1, 14025.1), (1, 14025.0), (2, 7012.1), (2, 7012.0)])
        self.assertEqual(f.freq, {1: 14025.0, 2: 7012.0})
        self.assertEqual(f.box, {11: "", 22: ""})            # test call cleared
        self.assertIn("✓ callsign entry: TEST in the box", stationtest.report(res))

    def test_no_report_and_busy_box(self) -> None:
        f = Fake(report=False, box="DL1ABC")
        res = f.run(radios=(1,))
        self.assertFalse(res[0]["ok"])
        text = stationtest.report(res)
        self.assertIn("N1MM didn't report it", text)
        self.assertIn("box holds 'DL1ABC' (left as it is)", text)
        self.assertEqual(f.box[11], "DL1ABC")
        self.assertEqual(f.sent, [(1, 14025.1)])             # stops after the first failure

    def test_no_radio_and_skipped_qsy(self) -> None:
        self.assertFalse(stationtest.run([], None, None, None)[0]["ok"])
        res = Fake().run(radios=(2,), skip_qsy=lambda nr: "typed")
        self.assertIn("QSY not tested: typed", stationtest.report(res))
        self.assertTrue(res[0]["ok"])


if __name__ == "__main__":
    unittest.main()
