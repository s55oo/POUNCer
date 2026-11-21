"""A spot with an older whole-minute stamp than one already seen of the same
call (v0.7.245): E77Z's self-spot (1123Z, via a slow cluster) came after a
skimmer's 1124Z spot of E77Z/P and was dropped silently as 'out of order'."""
import calendar
import unittest

from pouncer.spot_processing import SpotProcessor

T = calendar.timegm((2026, 11, 21, 11, 24, 42))
SKIMMER = b'DX de DL8LAS-#: 14061.7 E77Z/P CW 12 dB 22 WPM CQ 1124Z'
SELF = b'DX de E77Z:      14061.7  E77Z/P       [-SOTA-] CW E7/BO-102 Glatko   1123Z'


def engine():
    return SpotProcessor({'trust_quality': 2}, clock=lambda: T)


class OutOfOrderTests(unittest.TestCase):
    def test_self_spot_after_a_filtered_newer_skimmer_spot_goes_out(self):
        e = engine()
        e.process('telnet4', 'gocluster', SKIMMER, admit=False, received_at=T - 20)
        self.assertEqual(e.process('telnet', 'cluster', SELF, received_at=T), [SELF])

    def test_after_a_published_newer_spot_it_is_a_repeat(self):
        e = engine()
        self.assertEqual(e.process('telnet4', 'gocluster', SKIMMER, received_at=T - 20), [SKIMMER])
        self.assertEqual(e.process('telnet', 'cluster', SELF, received_at=T), [])
        self.assertEqual(e.last_evaluation['reason'], 'repeat')

    def test_older_spot_does_not_become_the_latest(self):
        e = engine()
        e.process('telnet4', 'gocluster', SKIMMER, admit=False, received_at=T - 20)
        e.process('telnet', 'cluster', SELF, received_at=T)
        latest = e.latest.get(('20M', 'CW', 'E77Z/P'))
        self.assertEqual(latest.spot.receiver_base, 'DL8LAS')

    def test_older_other_frequency_still_cannot_roll_back(self):
        e = engine()
        e.process('telnet4', 'gocluster', SKIMMER, admit=False, received_at=T - 20)
        old = SELF.replace(b'14061.7', b'14058.0')
        self.assertEqual(e.process('telnet', 'cluster', old, received_at=T), [])
        self.assertEqual(e.last_evaluation['reason'], 'out-of-order')
        self.assertEqual(e.counts['out_of_order_spots'], 1)

    def test_same_spotters_older_copy_is_still_dropped(self):
        e = engine()
        e.process('telnet4', 'gocluster', SKIMMER, admit=False, received_at=T - 20)
        copy = SKIMMER.replace(b'1124Z', b'1123Z')
        self.assertEqual(e.process('telnet3', 'cluster', copy, received_at=T), [])
        self.assertEqual(e.last_evaluation['reason'], 'out-of-order')

    def test_too_old_has_a_reason(self):
        e = SpotProcessor(clock=lambda: T + 600)
        self.assertEqual(e.process('telnet', 'cluster', SELF, received_at=T + 600), [])
        self.assertEqual(e.last_evaluation['reason'], 'too-old')


if __name__ == "__main__":
    unittest.main()
