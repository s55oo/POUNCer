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
        self.assertEqual(console.pb_note(a, t, a, t + 1), "  PT 09:48:55 +1s")
        self.assertEqual(console.pb_note(b, t, a, t + 1), "  PT 09:48:55 +1s as G4ZFE-#")
        self.assertEqual(console.pb_note(b, t + 3, a, t + 1), "  merged into PT 09:48:55")
        self.assertEqual(console.pb_note(console.spot_key(BUST), t, a, t + 1),
                         "  PT 09:48:55 +1s as SQ6OEP")       # a corrected call
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
        self.assertTrue(got[0].endswith("PT 09:48:55 +2s"), got[0])
        self.assertTrue(got[1].endswith("PT 09:48:55 +0s as G4ZFE-#"), got[1])
        self.assertNotIn("PT ", got[2])                         # DL1ABC: not sent
        self.assertTrue(got[4].endswith("merged into PT 09:48:55"), got[4])

    def test_pb_even_with_server_unticked(self) -> None:
        t = 35334.0
        self.c.show["SERVER"].set(False)
        self.c.add([(t, "telnet", A), (t + 1, "server >", A)])
        got = self.lines()
        self.assertEqual(len([x for x in got if x.strip()]), 1)   # no server line shown
        self.assertIn("PT 09:48:55", got[0])


@unittest.skipIf(_root is None, "no display")
class ConsoleExclTests(unittest.TestCase):
    def test_reason_at_the_end_of_a_kept_out_line(self) -> None:
        from pouncer import traffic
        why = {A: traffic.Excl("EXCL WARC"), B: True}
        c = console.TrafficConsole(_root, shown=lambda src, txt: why.get(txt))
        c.open()
        try:
            t = 35334.0
            c.add([(t, "telnet", A), (t, "telnet2", B)])
            c.recheck(now=10 ** 12)                    # the 5 s look-again is over
            got = c.text.get("1.0", "end").splitlines()
            self.assertTrue(got[0].endswith("EXCL WARC"), got[0])
            self.assertNotIn("EXCL", got[1])            # yellow: no reason
            self.assertIn("excl", c.text.tag_names("1.end-2c"))
        finally:
            c.close()

    def test_excl_is_false(self) -> None:
        from pouncer import traffic
        e = traffic.Excl("EXCL BCN")
        self.assertFalse(e)
        self.assertEqual(str(e), "EXCL BCN")

    def test_beacon_line_removed_with_beacon_group_unticked(self) -> None:
        # v0.7.228: EXCL WARC etc. still just get the reason at the end, but
        # EXCL BCN with BEACON unticked removes the whole raw line - before,
        # unticking BEACON only hid the separate "kept out" summary line,
        # not the beacon's own line under its real source (telnet / rbn ...)
        from pouncer import traffic
        why = {A: traffic.Excl("EXCL BCN"), B: True}
        c = console.TrafficConsole(_root, shown=lambda src, txt: why.get(txt))
        c.open()
        c.show["BEACON"].set(False)
        try:
            t = 35334.0
            c.add([(t, "telnet", A), (t, "telnet2", B)])
            c.recheck(now=10 ** 12)
            got = c.text.get("1.0", "end").splitlines()
            self.assertNotIn("G4ZFE", got)              # the beacon's line is gone
            self.assertTrue(any("OE9GHV" in line for line in got))  # the other line stays
        finally:
            c.close()
        # BEACON still ticked (default): the line stays, with its reason
        why2 = {A: traffic.Excl("EXCL BCN")}
        c2 = console.TrafficConsole(_root, shown=lambda src, txt: why2.get(txt))
        c2.open()
        try:
            c2.add([(t, "telnet", A)])
            c2.recheck(now=10 ** 12)
            self.assertTrue(c2.text.get("1.0", "end").splitlines()[0].endswith("EXCL BCN"))
        finally:
            c2.close()

    def test_warc_line_removed_with_warc_group_unticked(self) -> None:
        # v0.7.229: a WARC-band spot has no separate "kept out" summary line
        # of its own (unlike BEACON / FT8) - the WARC checkbox only ever
        # works by removing the raw line, same mechanism as BEACON's
        from pouncer import traffic
        why = {A: traffic.Excl("EXCL WARC"), B: True}
        c = console.TrafficConsole(_root, shown=lambda src, txt: why.get(txt))
        c.open()
        c.show["WARC"].set(False)
        try:
            t = 35334.0
            c.add([(t, "telnet", A), (t, "telnet2", B)])
            c.recheck(now=10 ** 12)
            got = c.text.get("1.0", "end").splitlines()
            self.assertNotIn("G4ZFE", got)
            self.assertTrue(any("OE9GHV" in line for line in got))
        finally:
            c.close()
        # WARC still ticked (default): the line stays, with its reason
        why2 = {A: traffic.Excl("EXCL WARC")}
        c2 = console.TrafficConsole(_root, shown=lambda src, txt: why2.get(txt))
        c2.open()
        try:
            c2.add([(t, "telnet", A)])
            c2.recheck(now=10 ** 12)
            self.assertTrue(c2.text.get("1.0", "end").splitlines()[0].endswith("EXCL WARC"))
        finally:
            c2.close()

    def test_ft8_checkbox_is_labelled_ft8_ft4(self) -> None:
        # v0.7.229: the FT8 group also keeps out FT4 (it always did); the
        # checkbox / chip text says so, without changing the GROUPS key
        # itself (app._console_info, EXCL reasons etc. still key on "FT8")
        self.assertEqual(console.GROUP_LABEL.get("FT8"), "FT8/FT4")
        self.assertNotIn("BEACON", console.GROUP_LABEL)
        self.assertNotIn("WARC", console.GROUP_LABEL)


if __name__ == "__main__":
    unittest.main()
