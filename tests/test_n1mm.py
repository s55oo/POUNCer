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


if __name__ == "__main__":
    unittest.main()
