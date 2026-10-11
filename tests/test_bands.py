import unittest

from pouncer.bands import CONTEST_BANDS, DEFAULT_BAND_KHZ, band_from_khz, khz_to_tens_of_hz, tens_of_hz_to_khz


class BandTests(unittest.TestCase):
    def test_known_bands(self):
        self.assertEqual(band_from_khz(14053.28), "20m")
        self.assertEqual(band_from_khz(3522.0), "80m")
        self.assertEqual(band_from_khz(28025.0), "10m")
        self.assertEqual(band_from_khz(7074.0), "40m")

    def test_out_of_band(self):
        self.assertIsNone(band_from_khz(12345.0))
        self.assertIsNone(band_from_khz(None))

    def test_conversions(self):
        self.assertAlmostEqual(tens_of_hz_to_khz(1405328), 14053.28, places=2)
        self.assertAlmostEqual(tens_of_hz_to_khz(181234), 1812.34, places=2)
        self.assertEqual(khz_to_tens_of_hz(14053.284), 1405328)


class ContestBandTests(unittest.TestCase):
    def test_defaults_are_inside_their_band(self) -> None:
        self.assertEqual(CONTEST_BANDS, ("160m", "80m", "40m", "20m", "15m", "10m"))
        for band in CONTEST_BANDS:
            self.assertEqual(band_from_khz(DEFAULT_BAND_KHZ[band]), band)


if __name__ == "__main__":
    unittest.main()
