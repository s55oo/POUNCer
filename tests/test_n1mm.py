import unittest

from pouncer.n1mm import parse_radio_info

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


if __name__ == "__main__":
    unittest.main()
