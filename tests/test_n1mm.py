import unittest

from pouncer.n1mm import next_tune_khz, parse_radio_info

RADIO_INFO = b"""<?xml version="1.0" encoding="utf-8"?>
<RadioInfo>
    <app>N1MM</app>
    <StationName>SHACK-PC</StationName>
    <RadioNr>1</RadioNr>
    <Freq>1405328</Freq>
    <TXFreq>1405328</TXFreq>
    <Mode>CW</Mode>
    <mycall>N0CALL</mycall>
    <ActiveRadioNr>1</ActiveRadioNr>
    <EntryWindowHwnd>275678</EntryWindowHwnd>
</RadioInfo>"""


class ParseRadioInfoTests(unittest.TestCase):
    def test_parses_frequency_and_mode(self):
        info = parse_radio_info(RADIO_INFO)
        self.assertIsNotNone(info)
        self.assertEqual(info["radio_nr"], 1)
        self.assertAlmostEqual(info["freq_khz"], 14053.28, places=2)
        self.assertEqual(info["mode"], "CW")
        self.assertEqual(info["active_radio"], 1)
        self.assertEqual(info["entry_window_hwnd"], 275678)

    def test_ignores_other_packets(self):
        self.assertIsNone(parse_radio_info(b"<contactinfo><call>K1ABC</call></contactinfo>"))
        self.assertIsNone(parse_radio_info(b"not xml"))


class TuneStepTests(unittest.TestCase):
    def test_first_step_from_reported(self) -> None:
        self.assertEqual(next_tune_khz(14011.6, None, 99, 25), 14011.625)
        self.assertEqual(next_tune_khz(14011.6, None, 99, -25), 14011.575)

    def test_repeated_steps_do_not_drift(self) -> None:
        # N1MM reports 14011.62 for 14011.625 (10 Hz units) - keep stepping
        # from our own target, so 3 x +25 Hz is exactly +75 Hz.
        t = next_tune_khz(14011.6, None, 99, 25)
        t = next_tune_khz(14011.62, t, 10.0, 25)   # matches radio, old step
        t = next_tune_khz(14011.62, t, 0.3, 25)    # quick tap, RadioInfo late
        self.assertEqual(t, 14011.675)

    def test_hand_tuned_since_uses_reported(self) -> None:
        self.assertEqual(next_tune_khz(14020.0, 14011.625, 30.0, 25), 14020.025)

    def test_no_frequency(self) -> None:
        self.assertIsNone(next_tune_khz(None, None, 99, 25))


class RadioCommandTests(unittest.TestCase):
    def test_cat_macro_and_packet(self) -> None:
        from pouncer.n1mm import cat_macro, radio_command_xml

        self.assertEqual(cat_macro(" ZZFI05; ", 1), "{CAT1ASC ZZFI05;}")
        self.assertEqual(cat_macro("FW0500;", 2), "{CAT2ASC FW0500;}")
        self.assertEqual(
            radio_command_xml("{CAT1ASC ZZFI05;}", 1, 12345),
            "<RadioCmd><RadioNr>1</RadioNr><FocusEntry>12345</FocusEntry>"
            "<RadioCommand>{CAT1ASC ZZFI05;}</RadioCommand></RadioCmd>",
        )
        self.assertIn("<RadioCommand>a &amp; &lt;b&gt;</RadioCommand>",
                      radio_command_xml("a & <b>", None, None))

    def test_controller_sends_packet(self) -> None:
        import socket
        from pouncer.n1mm import N1MMController

        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
        ctl = N1MMController("127.0.0.1", rx.getsockname()[1])
        try:
            ctl.send_radio_command("{CAT1ASC ZZFI02;}", 1, 77)
            data = rx.recv(2048).decode("utf-8")
        finally:
            ctl.close()
            rx.close()
        self.assertIn("<FocusEntry>77</FocusEntry>", data)
        self.assertIn("{CAT1ASC ZZFI02;}", data)


if __name__ == "__main__":
    unittest.main()
