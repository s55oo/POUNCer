"""Traffic console: PB - the time a spot went out on port 10000, at the end
of the spot lines of that station (v0.7.218)."""

import unittest

A = "DX de G4ZFE-#:    7022.0  SQ6OEP       CW  14dB Q:9* Z:14,15          0948Z"
B = "DX de OE9GHV-#:   7022.0  SQ6OEP       CW  26dB Q:9* Z:14,15          0948Z"
BUST = "DX de G4ZFE-#:    7022.1  SQ6OFP       CW  14dB Q:2  Z:14,15          0948Z"
OTHER = "DX de G4ZFE-#:   14025.0  DL1ABC       CW  20dB Q:5  Z:14             0948Z"

try:
    import tkinter as tk
    from pouncer import console
    _root = tk.Tk()
    _root.withdraw()
except Exception:  # noqa: BLE001
    _root = None
    console = None


@unittest.skipIf(console is None, "no tkinter")
class NoteTests(unittest.TestCase):
    def test_spot_key(self) -> None:
        self.assertEqual(console.spot_key(A), ("SQ6OEP", 7022.0, "G4ZFE-#"))
        self.assertEqual(console.spot_key("S55OO de NODE >" + B)[2], "OE9GHV-#")
        self.assertIsNone(console.spot_key("login: S55OO"))

    def test_notes(self) -> None:
        a, b = console.spot_key(A), console.spot_key(B)
        t = 35334.0                                      # 09:48:54 UTC
        self.assertEqual(console.pb_note(a, t, a, t + 1), "  PB 09:48:55 +1s")
        self.assertEqual(console.pb_note(b, t, a, t + 1), "  PB 09:48:55 +1s as G4ZFE-#")
        self.assertEqual(console.pb_note(b, t + 3, a, t + 1), "  merged into PB 09:48:55")
        self.assertEqual(console.pb_note(console.spot_key(BUST), t, a, t + 1),
                         "  PB 09:48:55 +1s as SQ6OEP")       # a corrected call
        self.assertEqual(console.pb_note(console.spot_key(OTHER), t, a, t + 1), "")
        self.assertEqual(console.pb_note(a, t, a, t + console.PB_LINK_S + 5), "")


@unittest.skipIf(_root is None, "no display")
class ConsolePbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = console.TrafficConsole(_root, shown=lambda src, txt: None)
        self.c.open()

    def tearDown(self) -> None:
        self.c.close()

    def lines(self) -> list:
        return self.c.text.get("1.0", "end").splitlines()

    def test_pb_on_both_lines_and_merged_after(self) -> None:
        t = 35334.0
        self.c.add([(t, "telnet", A), (t + 1, "telnet2", B), (t + 1, "telnet", OTHER)])
        self.c.add([(t + 1.5, "server >", A)])                 # A goes out
        self.c.add([(t + 4, "telnet3", B.replace("OE9GHV", "YO2KAR"))])
        got = self.lines()
        self.assertTrue(got[0].endswith("PB 09:48:55 +2s"), got[0])
        self.assertTrue(got[1].endswith("PB 09:48:55 +0s as G4ZFE-#"), got[1])
        self.assertNotIn("PB", got[2])                         # DL1ABC: not sent
        self.assertTrue(got[4].endswith("merged into PB 09:48:55"), got[4])

    def test_pb_even_with_server_unticked(self) -> None:
        t = 35334.0
        self.c.show["SERVER"].set(False)
        self.c.add([(t, "telnet", A), (t + 1, "server >", A)])
        got = self.lines()
        self.assertEqual(len([x for x in got if x.strip()]), 1)   # no server line shown
        self.assertIn("PB 09:48:55", got[0])


if __name__ == "__main__":
    unittest.main()
